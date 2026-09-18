import contextlib
import io
import json
import os
import pty
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from halow_monitor import (
    EventBroker,
    CapturePublisher,
    MediaMetricsState,
    MeasurementStore,
    MonitorState,
    RunStore,
    SerialDeviceError,
    SerialTelemetryReader,
    TelemetryParseError,
    build_http_server,
    derive_ap_rates,
    main,
    parse_metric_line,
    parse_webrtc_byte_counters,
    select_serial_device,
)


VALID = (
    "HALOW_METRIC,v=1,uptime_ms=123456,sample_ms=1001,traffic=1,"
    "mcs=3,bw_mhz=1,sgi=0,tx_attempts=97,tx_success=95,"
    "delivery_pct=97.938,txq_drops=0,rxq_drops=0,"
    "rx_alloc_failures=0,rx_read_failures=0,reorder_overflow=0,"
    "reorder_timeouts=0,reorder_outdated=0,reorder_retransmit=0,"
    "hw_restarts=0"
)

VALID_V2 = (
    "HALOW_METRIC,v=2,uptime_ms=123456,sample_ms=1001,"
    "rate_available=0,radio_active=1,halow_clients=1,traffic=0,"
    "mcs=-1,bw_mhz=0,sgi=-1,tx_attempts=0,tx_success=0,"
    "delivery_pct=-1,txq_drops=0,rxq_drops=0,"
    "rx_alloc_failures=0,rx_read_failures=0,reorder_overflow=0,"
    "reorder_timeouts=0,reorder_outdated=0,reorder_retransmit=0,"
    "hw_restarts=0"
)

VALID_V3 = (
    "HALOW_METRIC,v=3,uptime_ms=124456,sample_ms=1000,"
    "rate_available=0,radio_active=1,halow_clients=1,"
    "traffic_counters_available=1,halow_tx_bytes_total=142000,"
    "halow_rx_bytes_total=12000,traffic=0,mcs=-1,bw_mhz=0,sgi=-1,"
    "tx_attempts=0,tx_success=0,delivery_pct=-1,txq_drops=0,"
    "rxq_drops=0,rx_alloc_failures=0,rx_read_failures=0,"
    "reorder_overflow=0,reorder_timeouts=0,reorder_outdated=0,"
    "reorder_retransmit=0,hw_restarts=0"
)


class ParseMetricTests(unittest.TestCase):
    def test_ignores_diagnostic_lines(self):
        self.assertIsNone(parse_metric_line("[BASE] NAPT enabled"))

    def test_parses_schema_v1_into_numeric_values(self):
        sample = parse_metric_line(VALID)

        self.assertEqual(sample["mcs"], 3)
        self.assertEqual(sample["tx_attempts"], 97)
        self.assertAlmostEqual(sample["delivery_pct"], 97.938)
        self.assertEqual(sample["rate_available"], 1)
        self.assertEqual(sample["radio_active"], 1)
        self.assertEqual(sample["halow_clients"], -1)

    def test_parses_schema_v2_without_inventing_rate_values(self):
        sample = parse_metric_line(VALID_V2)

        self.assertEqual(sample["rate_available"], 0)
        self.assertEqual(sample["radio_active"], 1)
        self.assertEqual(sample["halow_clients"], 1)
        self.assertEqual(sample["delivery_pct"], -1.0)

    def test_parses_schema_v3_cumulative_traffic_counters(self):
        sample = parse_metric_line(VALID_V3)

        self.assertEqual(sample["traffic_counters_available"], 1)
        self.assertEqual(sample["halow_tx_bytes_total"], 142000)
        self.assertEqual(sample["halow_rx_bytes_total"], 12000)
        self.assertEqual(sample["halow_total_kbps"], -1.0)

    def test_older_schemas_normalize_traffic_counters_as_unavailable(self):
        for line in (VALID, VALID_V2):
            sample = parse_metric_line(line)

            self.assertEqual(sample["traffic_counters_available"], 0)
            self.assertEqual(sample["halow_tx_bytes_total"], -1)
            self.assertEqual(sample["halow_rx_bytes_total"], -1)
            self.assertEqual(sample["halow_tx_kbps"], -1.0)
            self.assertEqual(sample["halow_rx_kbps"], -1.0)
            self.assertEqual(sample["halow_total_kbps"], -1.0)

    def test_preserves_idle_sentinels(self):
        line = (
            VALID.replace("traffic=1", "traffic=0")
            .replace("mcs=3", "mcs=-1")
            .replace("bw_mhz=1", "bw_mhz=0")
            .replace("sgi=0", "sgi=-1")
            .replace("tx_attempts=97", "tx_attempts=0")
            .replace("tx_success=95", "tx_success=0")
            .replace("delivery_pct=97.938", "delivery_pct=-1")
        )

        sample = parse_metric_line(line)

        self.assertEqual(sample["traffic"], 0)
        self.assertEqual(sample["mcs"], -1)
        self.assertEqual(sample["sgi"], -1)
        self.assertEqual(sample["delivery_pct"], -1.0)

    def test_rejects_unknown_versions(self):
        with self.assertRaisesRegex(TelemetryParseError, "version"):
            parse_metric_line(VALID.replace("v=1", "v=4"))

    def test_rejects_missing_fields(self):
        with self.assertRaisesRegex(TelemetryParseError, "missing"):
            parse_metric_line(VALID.replace(",mcs=3", ""))

    def test_rejects_duplicate_fields(self):
        with self.assertRaisesRegex(TelemetryParseError, "duplicate"):
            parse_metric_line(VALID + ",mcs=4")

    def test_rejects_extra_fields(self):
        with self.assertRaisesRegex(TelemetryParseError, "unexpected"):
            parse_metric_line(VALID + ",noise=4")

    def test_rejects_invalid_delivery_relationships(self):
        with self.assertRaisesRegex(TelemetryParseError, "success"):
            parse_metric_line(VALID.replace("tx_success=95", "tx_success=98"))
        with self.assertRaisesRegex(TelemetryParseError, "idle"):
            parse_metric_line(VALID.replace("traffic=1", "traffic=0"))

    def test_rejects_invalid_v2_state_fields(self):
        with self.assertRaisesRegex(TelemetryParseError, "halow_clients"):
            parse_metric_line(VALID_V2.replace("halow_clients=1", "halow_clients=21"))
        with self.assertRaisesRegex(TelemetryParseError, "rate_available"):
            parse_metric_line(VALID_V2.replace("rate_available=0", "rate_available=2"))
        with self.assertRaisesRegex(TelemetryParseError, "radio_active"):
            parse_metric_line(VALID_V2.replace("radio_active=1", "radio_active=2"))

    def test_rejects_invalid_v3_traffic_counter_fields(self):
        with self.assertRaisesRegex(TelemetryParseError, "traffic_counters_available"):
            parse_metric_line(
                VALID_V3.replace("traffic_counters_available=1", "traffic_counters_available=2")
            )
        with self.assertRaisesRegex(TelemetryParseError, "nonnegative"):
            parse_metric_line(
                VALID_V3.replace("halow_tx_bytes_total=142000", "halow_tx_bytes_total=-1")
            )


class APRateTests(unittest.TestCase):
    def test_derives_decimal_kilobits_from_counter_and_uptime_deltas(self):
        previous = parse_metric_line(
            VALID_V3.replace("uptime_ms=124456", "uptime_ms=123456")
            .replace("halow_tx_bytes_total=142000", "halow_tx_bytes_total=100000")
            .replace("halow_rx_bytes_total=12000", "halow_rx_bytes_total=10000")
        )
        current = parse_metric_line(VALID_V3)

        rates = derive_ap_rates(previous, current)

        self.assertEqual(rates["halow_tx_kbps"], 336.0)
        self.assertEqual(rates["halow_rx_kbps"], 16.0)
        self.assertEqual(rates["halow_total_kbps"], 352.0)

    def test_returns_zero_for_a_valid_idle_interval(self):
        previous = parse_metric_line(
            VALID_V3.replace("uptime_ms=124456", "uptime_ms=123456")
        )

        rates = derive_ap_rates(previous, parse_metric_line(VALID_V3))

        self.assertEqual(rates["halow_tx_kbps"], 0.0)
        self.assertEqual(rates["halow_rx_kbps"], 0.0)
        self.assertEqual(rates["halow_total_kbps"], 0.0)

    def test_suppresses_first_reset_regressed_and_unavailable_intervals(self):
        current = parse_metric_line(VALID_V3)
        unavailable = parse_metric_line(
            VALID_V3.replace("traffic_counters_available=1", "traffic_counters_available=0")
            .replace("halow_tx_bytes_total=142000", "halow_tx_bytes_total=0")
            .replace("halow_rx_bytes_total=12000", "halow_rx_bytes_total=0")
        )
        uptime_reset = parse_metric_line(
            VALID_V3.replace("uptime_ms=124456", "uptime_ms=100")
        )
        counter_reset = parse_metric_line(
            VALID_V3.replace("halow_tx_bytes_total=142000", "halow_tx_bytes_total=1")
        )

        for previous, next_sample in (
            (None, current),
            (current, unavailable),
            (current, uptime_reset),
            (current, counter_reset),
        ):
            rates = derive_ap_rates(previous, next_sample)
            self.assertEqual(rates["halow_tx_kbps"], -1.0)
            self.assertEqual(rates["halow_rx_kbps"], -1.0)
            self.assertEqual(rates["halow_total_kbps"], -1.0)


class MediaMetricsTests(unittest.TestCase):
    METRICS = """# HELP webrtc_sessions_outbound_bytes bytes sent
webrtc_sessions_outbound_bytes{id="viewer-a",path="kitties",remoteAddr="10.43.0.2:52100",state="read"} 120000
webrtc_sessions_outbound_bytes{id="viewer-b",path="kitties",remoteAddr="10.43.0.3:52101",state="read"} 80000
webrtc_sessions_outbound_bytes{id="other",path="other-path",remoteAddr="127.0.0.1:9999",state="read"} 999000
webrtc_sessions_outbound_bytes{id="pending",path="kitties",remoteAddr="10.43.0.4:52102",state="idle"} 7000
paths_inbound_bytes{name="kitties",state="ready"} 500000
"""

    def test_parses_only_active_sessions_for_the_requested_path(self):
        counters = parse_webrtc_byte_counters(self.METRICS, "kitties")

        self.assertEqual(
            counters,
            {
                "viewer-a": {"bytes": 120000, "remote_addr": "10.43.0.2:52100"},
                "viewer-b": {"bytes": 80000, "remote_addr": "10.43.0.3:52101"},
            },
        )

    def test_ignores_malformed_negative_and_duplicate_session_metrics(self):
        text = """webrtc_sessions_outbound_bytes{id="good",path="kitties",state="read"} 42
webrtc_sessions_outbound_bytes{id="negative",path="kitties",state="read"} -1
webrtc_sessions_outbound_bytes{id="broken",path="kitties",state="read"} nope
webrtc_sessions_outbound_bytes{id="good",path="kitties",state="read"} 99
"""

        self.assertEqual(
            parse_webrtc_byte_counters(text, "kitties"),
            {"good": {"bytes": 42, "remote_addr": None}},
        )

    def test_derives_aggregate_rate_without_spiking_new_sessions(self):
        state = MediaMetricsState(monotonic=lambda: 0.0, halow_network="10.43.0.0/24")

        first = state.update(
            {"viewer-a": {"bytes": 100000, "remote_addr": "192.168.1.4:5000"}},
            now=10.0,
        )
        second = state.update(
            {
                "viewer-a": {"bytes": 130000, "remote_addr": "192.168.1.4:5000"},
                "viewer-b": {"bytes": 500000, "remote_addr": "10.43.0.2:5001"},
            },
            now=11.0,
        )
        third = state.update(
            {
                "viewer-a": {"bytes": 160000, "remote_addr": "192.168.1.4:5000"},
                "viewer-b": {"bytes": 520000, "remote_addr": "10.43.0.2:5001"},
            },
            now=12.0,
        )

        self.assertEqual(first["aggregate_kbps"], 0.0)
        self.assertEqual(second["aggregate_kbps"], 240.0)
        self.assertEqual(second["viewers"], 2)
        self.assertEqual(third["aggregate_kbps"], 400.0)
        self.assertEqual(third["lan_kbps"], 240.0)
        self.assertEqual(third["halow_kbps"], 160.0)
        self.assertEqual(third["lan_viewers"], 1)
        self.assertEqual(third["halow_viewers"], 1)
        self.assertEqual(third["unclassified_viewers"], 0)

    def test_keeps_unknown_remote_addresses_out_of_endpoint_rates(self):
        state = MediaMetricsState(monotonic=lambda: 0.0, halow_network="10.41.0.0/24")
        first = {
            "lan": {"bytes": 1000, "remote_addr": "192.168.1.4:5000"},
            "halow": {"bytes": 1000, "remote_addr": "10.41.0.1:5001"},
            "unknown": {"bytes": 1000, "remote_addr": "not-an-address"},
        }
        second = {
            name: {"bytes": item["bytes"] + 1000, "remote_addr": item["remote_addr"]}
            for name, item in first.items()
        }

        state.update(first, now=1.0)
        result = state.update(second, now=2.0)

        self.assertEqual(result["lan_kbps"], 8.0)
        self.assertEqual(result["halow_kbps"], 8.0)
        self.assertEqual(result["unclassified_viewers"], 1)

    def test_healthy_empty_metrics_are_zero_and_failure_is_unavailable(self):
        state = MediaMetricsState(monotonic=lambda: 0.0)

        empty = state.update({}, now=5.0)
        unavailable = state.set_unavailable("connection refused")

        self.assertEqual(empty["status"], "live")
        self.assertEqual(empty["aggregate_kbps"], 0.0)
        self.assertEqual(empty["viewers"], 0)
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["aggregate_kbps"], -1.0)
        self.assertEqual(unavailable["viewers"], 0)
        self.assertEqual(unavailable["error"], "connection refused")


class MeasurementStoreTests(unittest.TestCase):
    def test_appends_csv_and_limits_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.csv"
            fixed = lambda: datetime(2026, 9, 1, tzinfo=timezone.utc)
            store = MeasurementStore(path, history_limit=2, clock=fixed)
            try:
                for mcs in (1, 2, 3):
                    metric = parse_metric_line(VALID.replace("mcs=3", "mcs=%d" % mcs))
                    store.append(metric)

                self.assertEqual([row["mcs"] for row in store.history()], [2, 3])
                self.assertEqual(store.latest["mcs"], 3)
                text = path.read_text()
                self.assertEqual(text.count("timestamp,"), 1)
                self.assertEqual(len(text.splitlines()), 4)
                self.assertTrue(text.splitlines()[1].startswith("2026-09-01T00:00:00"))
            finally:
                store.close()

    def test_refuses_to_overwrite_an_existing_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.csv"
            path.write_text("do not overwrite")

            with self.assertRaises(FileExistsError):
                MeasurementStore(path)


class RunStoreTests(unittest.TestCase):
    def test_records_timestamped_events_and_completes_run(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [datetime(2026, 9, 1, tzinfo=timezone.utc)]
            elapsed = [10.0]
            store = RunStore(
                Path(directory),
                "kitties.mp4",
                "kitties",
                "10.41.0.0/24",
                clock=lambda: now[0],
                monotonic=lambda: elapsed[0],
            )

            started = store.start()
            elapsed[0] = 11.25
            store.record("ap", {"halow_tx_kbps": 123.0})
            elapsed[0] = 12.0
            stopped = store.stop(started["id"])
            loaded = store.load(started["id"])

            self.assertEqual(stopped["status"], "completed")
            self.assertEqual(stopped["duration_ms"], 2000)
            self.assertEqual(loaded["samples"][0]["kind"], "ap")
            self.assertEqual(loaded["samples"][0]["elapsed_ms"], 1250)
            self.assertEqual(loaded["samples"][0]["sample"]["halow_tx_kbps"], 123.0)

    def test_start_is_global_and_stop_validates_active_id(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory), "source.mp4", "kitties", "10.41.0.0/24")
            first = store.start()

            self.assertEqual(store.start()["id"], first["id"])
            with self.assertRaises(KeyError):
                store.stop("not-the-active-run")
            store.stop(first["id"])

    def test_marks_recording_run_interrupted_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = RunStore(root, "source.mp4", "kitties", "10.41.0.0/24")
            run_id = first.start()["id"]
            first.close()

            recovered = RunStore(root, "source.mp4", "kitties", "10.41.0.0/24")

            self.assertEqual(recovered.load(run_id)["metadata"]["status"], "interrupted")
            self.assertIsNone(recovered.snapshot()["active"])

    def test_rejects_unsafe_ids_and_retains_newest_completed_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            ticks = [
                datetime(2026, 9, 1, 0, 0, second, tzinfo=timezone.utc)
                for second in range(6)
            ]
            store = RunStore(
                Path(directory), "source.mp4", "kitties", "10.41.0.0/24",
                retention=2, clock=lambda: ticks.pop(0),
            )
            ids = []
            for _ in range(3):
                run = store.start()
                ids.append(run["id"])
                store.stop(run["id"])

            self.assertEqual([item["id"] for item in store.list_runs()], ids[-2:][::-1])
            with self.assertRaises(ValueError):
                store.load("../metadata")

    def test_clear_completed_removes_finished_runs_but_keeps_active_run(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory), "source.mp4", "kitties", "10.41.0.0/24")
            completed = store.start()
            store.stop(completed["id"])
            active = store.start()

            deleted = store.clear_completed()

            self.assertEqual(deleted, 1)
            self.assertEqual(store.snapshot()["active"]["id"], active["id"])
            self.assertEqual([run["id"] for run in store.list_runs()], [active["id"]])
            store.close()

    def test_delete_run_removes_one_completed_run(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory), "source.mp4", "kitties", "10.41.0.0/24")
            first = store.start()
            store.stop(first["id"])
            second = store.start()
            store.stop(second["id"])

            store.delete_run(first["id"])

            self.assertEqual([run["id"] for run in store.list_runs()], [second["id"]])


class CapturePublisherTests(unittest.TestCase):
    def test_starts_and_stops_the_capture_encoder(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "encoder.log"
            publisher = CapturePublisher(
                [sys.executable, "-c", "import time; time.sleep(30)"], log_path
            )
            try:
                self.assertTrue(publisher.start())
                self.assertTrue(publisher.running())
                self.assertFalse(publisher.start())
                publisher.stop()
                self.assertFalse(publisher.running())
            finally:
                publisher.stop()

class MonitorStateTests(unittest.TestCase):
    def test_player_configuration_is_in_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MeasurementStore(Path(directory) / "samples.csv")
            try:
                state = MonitorState(
                    store, webrtc_port=8890, media_path="walk",
                    video_setup={"source_name": "range-test.mp4", "source_resolution": "1920x1080",
                                 "output_profile": "720p", "output_resolution": "1280x720",
                                 "bitrate_kbps": 900},
                )
                self.assertEqual(state.snapshot()["player"], {
                    "port": 8890, "path": "walk", "source_name": "range-test.mp4",
                    "source_resolution": "1920x1080", "output_profile": "720p",
                    "output_resolution": "1280x720", "bitrate_kbps": 900,
                })
            finally:
                store.close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.directory.cleanup()

    def make_state(self, monotonic=None):
        path = Path(self.directory.name) / "run.csv"
        store = MeasurementStore(path)
        self.addCleanup(store.close)
        return MonitorState(store, stale_after=3.0, monotonic=monotonic)

    def test_reports_waiting_live_and_stale_states(self):
        now = [100.0]
        state = self.make_state(monotonic=lambda: now[0])

        self.assertEqual(state.snapshot()["status"], "waiting")
        state.accept_line(VALID)
        self.assertEqual(state.snapshot()["status"], "live")
        now[0] += 4.0
        self.assertEqual(state.snapshot()["status"], "stale")

    def test_counts_bad_prefixed_lines_without_recording_them(self):
        state = self.make_state()

        state.accept_line("HALOW_METRIC,v=9")

        self.assertEqual(state.snapshot()["parser_errors"], 1)
        self.assertIsNone(state.snapshot()["latest"])

    def test_serial_errors_take_precedence_over_sample_age(self):
        state = self.make_state()
        state.accept_line(VALID)
        state.set_serial_error("device disconnected")

        snapshot = state.snapshot()

        self.assertEqual(snapshot["status"], "serial-error")
        self.assertEqual(snapshot["serial_error"], "device disconnected")

    def test_derives_ap_rates_before_persisting_and_publishing(self):
        state = self.make_state()
        previous = (
            VALID_V3.replace("uptime_ms=124456", "uptime_ms=123456")
            .replace("halow_tx_bytes_total=142000", "halow_tx_bytes_total=100000")
            .replace("halow_rx_bytes_total=12000", "halow_rx_bytes_total=10000")
        )
        subscriber = state.broker.subscribe()

        first = state.accept_line(previous)
        second = state.accept_line(VALID_V3)

        self.assertEqual(first["halow_total_kbps"], -1.0)
        self.assertEqual(second["halow_tx_kbps"], 336.0)
        self.assertEqual(second["halow_rx_kbps"], 16.0)
        self.assertEqual(second["halow_total_kbps"], 352.0)
        subscriber.get(timeout=1)
        event, payload = subscriber.get(timeout=1)
        self.assertEqual(event, "sample")
        self.assertEqual(payload["halow_total_kbps"], 352.0)
        self.assertIn(",352.0\n", state.store.output_path.read_text())

    def test_exposes_video_state_and_history_without_combining_ap_rates(self):
        state = self.make_state()
        subscriber = state.broker.subscribe()

        video = state.media.update(
            {"viewer-a": {"bytes": 1000, "remote_addr": "192.168.1.4:5000"}},
            now=1.0,
        )
        snapshot = state.snapshot()
        history = state.history_snapshot()

        self.assertEqual(snapshot["service"], "rc32-halow-monitor")
        self.assertEqual(snapshot["api_version"], 4)
        self.assertEqual(snapshot["video"], video)
        self.assertEqual(history["video_samples"], [video])
        self.assertNotIn("aggregate_kbps", snapshot.get("latest") or {})
        event, payload = subscriber.get(timeout=1)
        self.assertEqual(event, "video")
        self.assertEqual(payload, video)


class EventBrokerTests(unittest.TestCase):
    def test_slow_subscriber_keeps_the_newest_sixteen_events(self):
        broker = EventBroker()
        subscriber = broker.subscribe()

        for value in range(20):
            broker.publish("sample", {"mcs": value})

        received = [subscriber.get_nowait()[1]["mcs"] for _ in range(16)]
        self.assertEqual(received, list(range(4, 20)))


class SerialTelemetryReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = MeasurementStore(Path(self.directory.name) / "serial.csv")
        self.state = MonitorState(self.store)
        self.stop_event = threading.Event()
        self.master_fd, self.slave_fd = pty.openpty()
        self.slave_path = os.ttyname(self.slave_fd)

    def tearDown(self):
        self.stop_event.set()
        if hasattr(self, "reader"):
            self.reader.join(timeout=2)
        for descriptor in (self.master_fd, self.slave_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self.store.close()
        self.directory.cleanup()

    def wait_for(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_reads_split_lines_and_survives_disconnect(self):
        self.reader = SerialTelemetryReader(
            self.slave_path, self.state, self.stop_event
        )
        self.reader.start()
        time.sleep(0.05)

        encoded = VALID.encode("utf-8")
        os.write(self.master_fd, b"[BASE] ordinary diagnostic\r\n")
        os.write(self.master_fd, encoded[:41])
        os.write(self.master_fd, encoded[41:] + b"\r\n" + encoded + b"\n")

        self.assertTrue(self.wait_for(lambda: len(self.store.history()) == 2))
        os.close(self.master_fd)
        self.master_fd = -1
        self.assertTrue(
            self.wait_for(lambda: self.state.snapshot()["serial_error"] is not None)
        )
        self.assertEqual(len(self.store.history()), 2)


class CommandLineTests(unittest.TestCase):
    def test_help_documents_public_arguments(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:
                main(["--help"])

        self.assertEqual(result.exception.code, 0)
        for option in (
            "--serial",
            "--bind",
            "--port",
            "--output",
            "--media-metrics-url",
            "--media-path",
            "--runs-dir",
            "--run-retention",
            "--halow-viewer-network",
            "--source-video",
        ):
            self.assertIn(option, output.getvalue())

    def test_rejects_invalid_run_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.mp4"
            cases = (
                ["--halow-viewer-network", "invalid"],
                ["--run-retention", "0"],
                ["--source-video", str(missing)],
            )
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    error = io.StringIO()
                    with contextlib.redirect_stderr(error):
                        result = main(["--serial", "/dev/null"] + arguments)
                    self.assertEqual(result, 2)

    def test_existing_output_is_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.csv"
            path.write_text("preserve me")
            error = io.StringIO()

            with contextlib.redirect_stderr(error):
                result = main(["--serial", "/dev/null", "--output", str(path)])

            self.assertEqual(result, 2)
            self.assertIn("already exists", error.getvalue())
            self.assertEqual(path.read_text(), "preserve me")

    def test_ambiguous_auto_detection_names_every_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "cu.usbmodem101"
            second = Path(directory) / "cu.usbmodem202"
            first.touch()
            second.touch()

            with self.assertRaises(SerialDeviceError) as error:
                select_serial_device("auto", str(Path(directory) / "cu.usbmodem*"))

            self.assertIn(str(first), str(error.exception))
            self.assertIn(str(second), str(error.exception))


class HttpApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.csv_path = root / "run.csv"
        self.assets = root / "monitor"
        self.assets.mkdir()
        (self.assets / "index.html").write_text("<h1>dashboard</h1>")
        (self.assets / "path.html").write_text("<h1>network path</h1>")
        (self.assets / "path.css").write_text("body { color: black; }")
        (self.assets / "dashboard.js").write_text("console.log('dashboard')")
        (self.assets / "dashboard.css").write_text("body { color: black; }")
        (self.assets / "video.html").write_text("<h1>video player</h1>")
        (self.assets / "video.js").write_text("console.log('video')")
        (self.assets / "video.css").write_text("body { color: black; }")
        self.source_video = root / "source.mp4"
        self.source_video.write_bytes(b"0123456789abcdef")
        self.store = MeasurementStore(self.csv_path)
        self.runs = RunStore(root / "runs", self.source_video.name, "kitties", "10.41.0.0/24")
        self.state = MonitorState(self.store, run_store=self.runs)
        self.server = build_http_server(
            "127.0.0.1", 0, self.state, self.assets, source_video=self.source_video
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()
        self.runs.close()
        self.directory.cleanup()

    def get_json(self, path):
        with urllib.request.urlopen(self.base + path) as response:
            return response.status, json.load(response)

    def test_server_startup_does_not_require_reverse_dns(self):
        with mock.patch(
            "socket.getfqdn",
            side_effect=AssertionError("dashboard startup attempted reverse DNS"),
        ):
            server = build_http_server("127.0.0.1", 0, self.state, self.assets)

        server.server_close()

    def test_latest_history_health_and_csv_endpoints(self):
        self.state.accept_line(VALID)

        self.assertEqual(self.get_json("/api/latest")[1]["latest"]["mcs"], 3)
        self.assertEqual(len(self.get_json("/api/history")[1]["samples"]), 1)
        self.assertEqual(self.get_json("/healthz")[0], 200)
        health = self.get_json("/healthz")[1]
        self.assertEqual(health["service"], "rc32-halow-monitor")
        self.assertEqual(health["api_version"], 4)
        self.assertEqual(health["video"]["status"], "unavailable")
        with urllib.request.urlopen(self.base + "/measurements.csv") as response:
            self.assertIn(b"delivery_pct", response.read())
            self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_latest_endpoint_exposes_v2_client_count(self):
        self.state.accept_line(VALID_V2)

        latest = self.get_json("/api/latest")[1]["latest"]
        self.assertEqual(latest["halow_clients"], 1)
        self.assertEqual(latest["rate_available"], 0)

    def test_serves_video_at_root_and_dashboard_at_admin(self):
        with urllib.request.urlopen(self.base + "/") as response:
            self.assertIn(b"video player", response.read())
        with urllib.request.urlopen(self.base + "/admin") as response:
            self.assertIn(b"dashboard", response.read())
        with urllib.request.urlopen(self.base + "/admin/") as response:
            self.assertIn(b"dashboard", response.read())
        with urllib.request.urlopen(self.base + "/video.js") as response:
            self.assertIn(b"video", response.read())

    def test_serves_network_path_page_below_admin(self):
        for path in ("/admin/path", "/admin/path/"):
            with urllib.request.urlopen(self.base + path) as response:
                self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
                self.assertIn(b"network path", response.read())
        with urllib.request.urlopen(self.base + "/path.css") as response:
            self.assertEqual(response.headers["Content-Type"], "text/css; charset=utf-8")

    def test_serves_only_fixed_asset_paths_and_rejects_post(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.base + "/private-file")
        self.assertEqual(error.exception.code, 404)

        request = urllib.request.Request(self.base + "/", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 405)

    def test_run_lifecycle_is_global_and_recorded(self):
        request = urllib.request.Request(self.base + "/api/runs", data=b"{}", method="POST")
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 201)
            started = json.load(response)["run"]

        self.state.accept_line(VALID)
        self.assertEqual(self.get_json("/api/latest")[1]["run"]["active"]["id"], started["id"])

        stop = urllib.request.Request(
            self.base + "/api/runs/%s/stop" % started["id"], data=b"{}", method="POST"
        )
        with urllib.request.urlopen(stop) as response:
            self.assertEqual(json.load(response)["run"]["status"], "completed")

        listing = self.get_json("/api/runs")[1]
        self.assertEqual(listing["runs"][0]["id"], started["id"])
        loaded = self.get_json("/api/runs/%s" % started["id"])[1]
        self.assertEqual(loaded["samples"][0]["kind"], "ap")

    def test_delete_runs_removes_completed_runs_but_not_an_active_capture(self):
        first = urllib.request.Request(self.base + "/api/runs", data=b"{}", method="POST")
        with urllib.request.urlopen(first) as response:
            completed = json.load(response)["run"]
        stop = urllib.request.Request(
            self.base + "/api/runs/%s/stop" % completed["id"], data=b"{}", method="POST"
        )
        urllib.request.urlopen(stop).close()
        active = urllib.request.Request(self.base + "/api/runs", data=b"{}", method="POST")
        with urllib.request.urlopen(active) as response:
            active_id = json.load(response)["run"]["id"]

        delete = urllib.request.Request(self.base + "/api/runs", method="DELETE")
        with urllib.request.urlopen(delete) as response:
            self.assertEqual(json.load(response)["deleted"], 1)

        listing = self.get_json("/api/runs")[1]
        self.assertEqual(listing["active"]["id"], active_id)
        self.assertEqual(listing["runs"][0]["id"], active_id)

    def test_delete_one_completed_run(self):
        start = urllib.request.Request(self.base + "/api/runs", data=b"{}", method="POST")
        with urllib.request.urlopen(start) as response:
            run_id = json.load(response)["run"]["id"]
        stop = urllib.request.Request(
            self.base + "/api/runs/%s/stop" % run_id, data=b"{}", method="POST"
        )
        urllib.request.urlopen(stop).close()

        delete = urllib.request.Request(self.base + "/api/runs/%s" % run_id, method="DELETE")
        with urllib.request.urlopen(delete) as response:
            self.assertEqual(json.load(response)["deleted"], 1)

        self.assertEqual(self.get_json("/api/runs")[1]["runs"], [])

    def test_source_video_supports_byte_ranges(self):
        request = urllib.request.Request(
            self.base + "/source-video", headers={"Range": "bytes=3-7"}
        )
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), b"34567")
            self.assertEqual(response.headers["Content-Range"], "bytes 3-7/16")
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")

    def test_head_returns_headers_without_a_body(self):
        request = urllib.request.Request(self.base + "/api/latest", method="HEAD")
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")
            self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_client_reset_does_not_emit_server_traceback(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            with socket.create_connection(
                ("127.0.0.1", self.server.server_port)
            ) as client:
                client.sendall(b"GET /")
                client.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_LINGER,
                    struct.pack("ii", 1, 0),
                )
            time.sleep(0.1)

        self.assertNotIn("Traceback", errors.getvalue())

    def test_broker_publishes_sample_event(self):
        subscriber = self.state.broker.subscribe()

        self.state.accept_line(VALID)

        event, payload = subscriber.get(timeout=1)
        self.assertEqual(event, "sample")
        self.assertEqual(payload["mcs"], 3)


if __name__ == "__main__":
    unittest.main()
