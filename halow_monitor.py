#!/usr/bin/env python3

"""Local Wi-Fi HaLow telemetry parser and dashboard service."""

import argparse
import csv
import glob
import ipaddress
import json
import os
import queue
import re
import select
import shlex
import shutil
import subprocess
import signal
import sys
import termios
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit


METRIC_PREFIX = "HALOW_METRIC"
METRIC_FIELDS_V1 = (
    "v",
    "uptime_ms",
    "sample_ms",
    "traffic",
    "mcs",
    "bw_mhz",
    "sgi",
    "tx_attempts",
    "tx_success",
    "delivery_pct",
    "txq_drops",
    "rxq_drops",
    "rx_alloc_failures",
    "rx_read_failures",
    "reorder_overflow",
    "reorder_timeouts",
    "reorder_outdated",
    "reorder_retransmit",
    "hw_restarts",
)
METRIC_FIELDS_V2 = (
    "v",
    "uptime_ms",
    "sample_ms",
    "rate_available",
    "radio_active",
    "halow_clients",
) + METRIC_FIELDS_V1[3:]
METRIC_FIELDS_V3 = METRIC_FIELDS_V2[:6] + (
    "traffic_counters_available",
    "halow_tx_bytes_total",
    "halow_rx_bytes_total",
) + METRIC_FIELDS_V2[6:]
METRIC_FIELDS_BY_VERSION = {
    1: METRIC_FIELDS_V1,
    2: METRIC_FIELDS_V2,
    3: METRIC_FIELDS_V3,
}
AP_RATE_FIELDS = (
    "halow_tx_kbps",
    "halow_rx_kbps",
    "halow_total_kbps",
)
CSV_FIELDS = ("timestamp",) + METRIC_FIELDS_V3 + AP_RATE_FIELDS


class TelemetryParseError(ValueError):
    """Raised when a line claims to be telemetry but violates its schema."""


class SerialDeviceError(RuntimeError):
    """Raised when automatic serial-device selection is not deterministic."""


def _parse_pairs(parts):
    values = {}
    for part in parts:
        if "=" not in part:
            raise TelemetryParseError("malformed key/value field: %s" % part)
        key, value = part.split("=", 1)
        if key in values:
            raise TelemetryParseError("duplicate field: %s" % key)
        values[key] = value
    return values


def _validate_metric(metric):
    if metric["v"] not in METRIC_FIELDS_BY_VERSION:
        raise TelemetryParseError("unsupported telemetry version: %s" % metric["v"])

    nonnegative = (
        "uptime_ms",
        "sample_ms",
        "tx_attempts",
        "tx_success",
        "txq_drops",
        "rxq_drops",
        "rx_alloc_failures",
        "rx_read_failures",
        "reorder_overflow",
        "reorder_timeouts",
        "reorder_outdated",
        "reorder_retransmit",
        "hw_restarts",
    )
    if any(metric[field] < 0 for field in nonnegative):
        raise TelemetryParseError("counters and timestamps must be nonnegative")
    if metric["traffic"] not in (0, 1):
        raise TelemetryParseError("traffic must be 0 or 1")
    if metric["rate_available"] not in (0, 1):
        raise TelemetryParseError("rate_available must be 0 or 1")
    if metric["radio_active"] not in (0, 1):
        raise TelemetryParseError("radio_active must be 0 or 1")
    if metric["v"] == 1:
        if metric["halow_clients"] != -1:
            raise TelemetryParseError("schema v1 client count must be unknown")
    elif not 0 <= metric["halow_clients"] <= 20:
        raise TelemetryParseError("halow_clients must be between 0 and 20")
    if metric["traffic_counters_available"] not in (0, 1):
        raise TelemetryParseError("traffic_counters_available must be 0 or 1")
    if metric["v"] == 3:
        if metric["halow_tx_bytes_total"] < 0 or metric["halow_rx_bytes_total"] < 0:
            raise TelemetryParseError("traffic counters must be nonnegative")
        if metric["traffic_counters_available"] == 0 and (
            metric["halow_tx_bytes_total"] != 0
            or metric["halow_rx_bytes_total"] != 0
        ):
            raise TelemetryParseError(
                "unavailable traffic counters must use zero totals"
            )
    if metric["rate_available"] == 0 and metric["traffic"] != 0:
        raise TelemetryParseError("traffic cannot be reported without rate statistics")
    if metric["tx_success"] > metric["tx_attempts"]:
        raise TelemetryParseError("tx success cannot exceed attempts")

    if metric["traffic"] == 0:
        expected = {
            "mcs": -1,
            "bw_mhz": 0,
            "sgi": -1,
            "tx_attempts": 0,
            "tx_success": 0,
            "delivery_pct": -1.0,
        }
        if any(metric[field] != value for field, value in expected.items()):
            raise TelemetryParseError("idle telemetry has inconsistent sentinels")
    else:
        if metric["tx_attempts"] == 0:
            raise TelemetryParseError("traffic telemetry requires attempts")
        if not 0 <= metric["mcs"] <= 15:
            raise TelemetryParseError("traffic MCS is outside the encoded range")
        if metric["bw_mhz"] not in (1, 2, 4):
            raise TelemetryParseError("traffic bandwidth must be 1, 2, or 4 MHz")
        if metric["sgi"] not in (0, 1):
            raise TelemetryParseError("traffic guard interval must be 0 or 1")
        if not 0.0 <= metric["delivery_pct"] <= 100.0:
            raise TelemetryParseError("traffic delivery percentage is outside 0-100")


def parse_metric_line(line):
    """Parse one serial line, returning None for non-telemetry diagnostics."""

    stripped = line.strip()
    if not stripped.startswith(METRIC_PREFIX + ","):
        return None

    raw_values = _parse_pairs(stripped.split(",")[1:])
    try:
        version = int(raw_values.get("v", ""), 10)
    except ValueError as exc:
        raise TelemetryParseError("nonnumeric telemetry version: %s" % exc)
    if version not in METRIC_FIELDS_BY_VERSION:
        raise TelemetryParseError("unsupported telemetry version: %s" % version)

    present = set(raw_values)
    expected_fields = METRIC_FIELDS_BY_VERSION[version]
    expected = set(expected_fields)
    missing = expected - present
    unexpected = present - expected
    if missing:
        raise TelemetryParseError("missing fields: %s" % ", ".join(sorted(missing)))
    if unexpected:
        raise TelemetryParseError("unexpected fields: %s" % ", ".join(sorted(unexpected)))

    metric = {}
    try:
        for field in expected_fields:
            if field != "delivery_pct":
                metric[field] = int(raw_values[field], 10)
        metric["delivery_pct"] = float(raw_values["delivery_pct"])
    except ValueError as exc:
        raise TelemetryParseError("nonnumeric telemetry value: %s" % exc)

    if version == 1:
        metric["rate_available"] = 1
        metric["radio_active"] = metric["traffic"]
        metric["halow_clients"] = -1
    if version < 3:
        metric["traffic_counters_available"] = 0
        metric["halow_tx_bytes_total"] = -1
        metric["halow_rx_bytes_total"] = -1
    for field in AP_RATE_FIELDS:
        metric[field] = -1.0

    _validate_metric(metric)
    return metric


def derive_ap_rates(previous, current):
    """Derive decimal-kbps AP rates from consecutive cumulative counters."""

    unavailable = {field: -1.0 for field in AP_RATE_FIELDS}
    if previous is None:
        return unavailable
    if (
        previous["traffic_counters_available"] != 1
        or current["traffic_counters_available"] != 1
    ):
        return unavailable

    elapsed_ms = current["uptime_ms"] - previous["uptime_ms"]
    tx_delta = (
        current["halow_tx_bytes_total"] - previous["halow_tx_bytes_total"]
    )
    rx_delta = (
        current["halow_rx_bytes_total"] - previous["halow_rx_bytes_total"]
    )
    if elapsed_ms <= 0 or tx_delta < 0 or rx_delta < 0:
        return unavailable

    tx_kbps = tx_delta * 8.0 / elapsed_ms
    rx_kbps = rx_delta * 8.0 / elapsed_ms
    return {
        "halow_tx_kbps": tx_kbps,
        "halow_rx_kbps": rx_kbps,
        "halow_total_kbps": tx_kbps + rx_kbps,
    }


_PROMETHEUS_LABEL = re.compile(
    r'([A-Za-z_][A-Za-z0-9_]*)="((?:\\.|[^"\\])*)"(?:,|$)'
)
_WEBRTC_OUTBOUND_BYTES = re.compile(
    r"^webrtc_sessions_outbound_bytes\{(.*)\}\s+([0-9]+)(?:\s+[0-9]+)?$"
)


def _parse_prometheus_labels(raw):
    labels = {}
    position = 0
    while position < len(raw):
        match = _PROMETHEUS_LABEL.match(raw, position)
        if match is None:
            return None
        try:
            value = json.loads('"%s"' % match.group(2))
        except json.JSONDecodeError:
            return None
        name = match.group(1)
        if name in labels:
            return None
        labels[name] = value
        position = match.end()
    return labels


def parse_webrtc_byte_counters(text, path):
    """Return active outbound WebRTC byte counters and addresses by session ID."""

    counters = {}
    for raw_line in text.splitlines():
        match = _WEBRTC_OUTBOUND_BYTES.match(raw_line.strip())
        if match is None:
            continue
        labels = _parse_prometheus_labels(match.group(1))
        if labels is None:
            continue
        session_id = labels.get("id")
        if (
            not session_id
            or labels.get("path") != path
            or labels.get("state") != "read"
            or session_id in counters
        ):
            continue
        counters[session_id] = {
            "bytes": int(match.group(2), 10),
            "remote_addr": labels.get("remoteAddr"),
        }
    return counters


def _remote_ip(remote_addr):
    if not remote_addr or not isinstance(remote_addr, str):
        return None
    value = remote_addr.strip()
    if value.startswith("["):
        closing = value.find("]")
        host = value[1:closing] if closing > 1 else ""
    else:
        host = value.rsplit(":", 1)[0] if ":" in value else value
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _utc_timestamp(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class MeasurementStore:
    """Append-only CSV storage plus a bounded in-memory history."""

    def __init__(self, output_path, history_limit=900, clock=None):
        self.output_path = Path(output_path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._history = deque(maxlen=history_limit)
        self._latest = None
        self._lock = threading.Lock()
        self._file = self.output_path.open("x", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=CSV_FIELDS)
        self._writer.writeheader()
        self._file.flush()

    @property
    def latest(self):
        with self._lock:
            return None if self._latest is None else dict(self._latest)

    def history(self):
        with self._lock:
            return [dict(record) for record in self._history]

    def append(self, metric):
        record = {"timestamp": _utc_timestamp(self._clock())}
        record.update(metric)
        with self._lock:
            self._writer.writerow(record)
            self._file.flush()
            self._latest = record
            self._history.append(record)
        return dict(record)

    def close(self):
        with self._lock:
            if not self._file.closed:
                self._file.close()

    def csv_bytes(self):
        with self._lock:
            self._file.flush()
            return self.output_path.read_bytes()


_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{12}Z(?:-[0-9]+)?$")


class RunStore:
    """Crash-tolerant storage for one globally active measurement run."""

    def __init__(
        self,
        root,
        source_name,
        media_path,
        halow_network,
        retention=100,
        clock=None,
        monotonic=None,
        broker=None,
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_name = Path(source_name).name
        self.media_path = media_path
        self.halow_network = str(halow_network)
        self.retention = retention
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._broker = broker
        self._active = None
        self._active_started_at = None
        self._sample_file = None
        self._lock = threading.RLock()
        self._recover_interrupted()

    def set_broker(self, broker):
        self._broker = broker

    def _metadata_path(self, run_id):
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run id")
        return self.root / run_id / "metadata.json"

    @staticmethod
    def _write_metadata(path, metadata):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(metadata, separators=(",", ":")) + "\n")
        os.replace(temporary, path)

    def _recover_interrupted(self):
        for path in self.root.glob("*/metadata.json"):
            try:
                metadata = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if metadata.get("status") == "recording":
                metadata["status"] = "interrupted"
                self._write_metadata(path, metadata)

    def _publish(self):
        payload = self.snapshot()
        if self._broker is not None:
            self._broker.publish("run", payload)
        return payload

    def start(self):
        with self._lock:
            if self._active is not None:
                return dict(self._active)
            now = self._clock()
            base = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            run_id = base
            suffix = 1
            while (self.root / run_id).exists():
                run_id = "%s-%d" % (base, suffix)
                suffix += 1
            run_dir = self.root / run_id
            run_dir.mkdir()
            metadata = {
                "schema_version": 1,
                "id": run_id,
                "status": "recording",
                "started_at": _utc_timestamp(now),
                "stopped_at": None,
                "duration_ms": None,
                "source": self.source_name,
                "media_path": self.media_path,
                "halow_network": self.halow_network,
            }
            self._write_metadata(run_dir / "metadata.json", metadata)
            self._sample_file = (run_dir / "samples.jsonl").open("a", buffering=1)
            self._active = metadata
            self._active_started_at = self._monotonic()
        self._publish()
        return dict(metadata)

    def stop(self, run_id):
        with self._lock:
            if self._active is None or self._active["id"] != run_id:
                raise KeyError(run_id)
            stopped_at = self._clock()
            metadata = dict(self._active)
            metadata.update(
                status="completed",
                stopped_at=_utc_timestamp(stopped_at),
                duration_ms=max(0, int(round((self._monotonic() - self._active_started_at) * 1000))),
            )
            self._sample_file.close()
            self._sample_file = None
            self._write_metadata(self._metadata_path(run_id), metadata)
            self._active = None
            self._active_started_at = None
            self._enforce_retention()
        self._publish()
        return dict(metadata)

    def record(self, kind, sample):
        with self._lock:
            if self._active is None or self._sample_file is None:
                return None
            event = {
                "kind": kind,
                "elapsed_ms": max(0, int(round((self._monotonic() - self._active_started_at) * 1000))),
                "sample": dict(sample),
            }
            self._sample_file.write(json.dumps(event, separators=(",", ":")) + "\n")
            self._sample_file.flush()
            return event

    def snapshot(self):
        with self._lock:
            active = None if self._active is None else dict(self._active)
            if active is not None:
                active["elapsed_ms"] = max(
                    0, int(round((self._monotonic() - self._active_started_at) * 1000))
                )
            return {"active": active}

    def list_runs(self):
        runs = []
        for path in self.root.glob("*/metadata.json"):
            try:
                metadata = json.loads(path.read_text())
                samples = path.parent / "samples.jsonl"
                metadata["size_bytes"] = path.stat().st_size + (samples.stat().st_size if samples.exists() else 0)
                runs.append(metadata)
            except (OSError, ValueError):
                continue
        return sorted(runs, key=lambda item: item.get("started_at", ""), reverse=True)

    def load(self, run_id):
        metadata_path = self._metadata_path(run_id)
        if not metadata_path.is_file():
            raise KeyError(run_id)
        metadata = json.loads(metadata_path.read_text())
        samples = []
        sample_path = metadata_path.parent / "samples.jsonl"
        if sample_path.exists():
            for line in sample_path.read_text().splitlines():
                try:
                    samples.append(json.loads(line))
                except ValueError:
                    break
        return {"metadata": metadata, "samples": samples}

    def clear_completed(self):
        """Remove completed or interrupted runs while preserving a live capture."""
        with self._lock:
            active_id = None if self._active is None else self._active["id"]
            deleted = 0
            for metadata in self.list_runs():
                run_id = metadata.get("id")
                if run_id == active_id or metadata.get("status") == "recording":
                    continue
                try:
                    run_dir = self._metadata_path(run_id).parent
                except ValueError:
                    continue
                if run_dir.is_dir():
                    shutil.rmtree(run_dir)
                    deleted += 1
        self._publish()
        return deleted

    def delete_run(self, run_id):
        """Remove one completed or interrupted run."""
        try:
            metadata_path = self._metadata_path(run_id)
        except ValueError:
            raise KeyError(run_id)
        with self._lock:
            active_id = None if self._active is None else self._active["id"]
            if run_id == active_id or not metadata_path.is_file():
                raise KeyError(run_id)
            try:
                metadata = json.loads(metadata_path.read_text())
            except (OSError, ValueError):
                raise KeyError(run_id)
            if metadata.get("status") == "recording":
                raise KeyError(run_id)
            shutil.rmtree(metadata_path.parent)
        self._publish()
        return 1

    def _enforce_retention(self):
        completed = [run for run in self.list_runs() if run.get("status") != "recording"]
        for metadata in completed[self.retention :]:
            shutil.rmtree(self.root / metadata["id"])

    def close(self):
        with self._lock:
            if self._sample_file is not None:
                self._sample_file.close()
                self._sample_file = None


class CapturePublisher:
    """Own the FFmpeg process for one on-demand video capture."""

    def __init__(self, command, log_path):
        self.command = tuple(command)
        self.log_path = Path(log_path)
        self._process = None
        self._log = None
        self._lock = threading.Lock()

    def running(self):
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return False
            if not self.command:
                raise RuntimeError("capture encoder command is empty")
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = self.log_path.open("ab")
            try:
                self._process = subprocess.Popen(
                    self.command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=self._log,
                )
            except Exception:
                self._log.close()
                self._log = None
                raise
            return True

    def stop(self):
        with self._lock:
            process, log = self._process, self._log
            self._process = None
            self._log = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if log is not None:
            log.close()


class EventBroker:
    """Fan events out without ever blocking the telemetry reader."""

    def __init__(self, queue_size=16):
        self._queue_size = queue_size
        self._subscribers = set()
        self._lock = threading.Lock()

    def subscribe(self):
        subscriber = queue.Queue(maxsize=self._queue_size)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber):
        with self._lock:
            self._subscribers.discard(subscriber)

    def publish(self, event, data):
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait((event, data))
            except queue.Full:
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber.put_nowait((event, data))
                except queue.Full:
                    pass


class MediaMetricsState:
    """Thread-safe WebRTC delivery rate and bounded history."""

    def __init__(
        self,
        broker=None,
        history_limit=900,
        monotonic=None,
        clock=None,
        halow_network="10.41.0.0/24",
        recorder=None,
    ):
        self.broker = broker
        self._history = deque(maxlen=history_limit)
        self._monotonic = monotonic or time.monotonic
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.halow_network = ipaddress.ip_network(halow_network, strict=False)
        self._recorder = recorder
        self._previous = None
        self._previous_at = None
        self._latest = {
            "timestamp": None,
            "status": "unavailable",
            "aggregate_kbps": -1.0,
            "viewers": 0,
            "lan_kbps": -1.0,
            "halow_kbps": -1.0,
            "lan_viewers": 0,
            "halow_viewers": 0,
            "unclassified_viewers": 0,
            "error": "MediaMTX metrics have not been received",
        }
        self._lock = threading.Lock()

    def update(self, counters, now=None):
        observed_at = self._monotonic() if now is None else now
        with self._lock:
            aggregate_kbps = 0.0
            endpoint_bytes = {"lan": 0, "halow": 0}
            endpoint_viewers = {"lan": 0, "halow": 0, "unclassified": 0}
            classifications = {}
            for session_id, counter in counters.items():
                remote_ip = _remote_ip(counter.get("remote_addr"))
                if remote_ip is None:
                    endpoint = "unclassified"
                elif remote_ip.version == self.halow_network.version and remote_ip in self.halow_network:
                    endpoint = "halow"
                else:
                    endpoint = "lan"
                classifications[session_id] = endpoint
                endpoint_viewers[endpoint] += 1
            if self._previous is not None and self._previous_at is not None:
                elapsed = observed_at - self._previous_at
                if elapsed > 0:
                    byte_delta = 0
                    for session_id, counter in counters.items():
                        total = counter["bytes"]
                        previous = self._previous.get(session_id)
                        previous_total = None if previous is None else previous["bytes"]
                        if previous_total is not None and total >= previous_total:
                            delta = total - previous_total
                            byte_delta += delta
                            endpoint = classifications[session_id]
                            if endpoint in endpoint_bytes:
                                endpoint_bytes[endpoint] += delta
                    aggregate_kbps = byte_delta * 8.0 / elapsed / 1000.0
                    lan_kbps = endpoint_bytes["lan"] * 8.0 / elapsed / 1000.0
                    halow_kbps = endpoint_bytes["halow"] * 8.0 / elapsed / 1000.0
                else:
                    lan_kbps = halow_kbps = 0.0
            else:
                lan_kbps = halow_kbps = 0.0

            record = {
                "timestamp": _utc_timestamp(self._clock()),
                "status": "live",
                "aggregate_kbps": aggregate_kbps,
                "viewers": len(counters),
                "lan_kbps": lan_kbps,
                "halow_kbps": halow_kbps,
                "lan_viewers": endpoint_viewers["lan"],
                "halow_viewers": endpoint_viewers["halow"],
                "unclassified_viewers": endpoint_viewers["unclassified"],
                "error": None,
            }
            self._previous = dict(counters)
            self._previous_at = observed_at
            self._latest = record
            self._history.append(record)
        payload = dict(record)
        if self.broker is not None:
            self.broker.publish("video", payload)
        if self._recorder is not None:
            self._recorder("video", payload)
        return payload

    def set_unavailable(self, error):
        record = {
            "timestamp": _utc_timestamp(self._clock()),
            "status": "unavailable",
            "aggregate_kbps": -1.0,
            "viewers": 0,
            "lan_kbps": -1.0,
            "halow_kbps": -1.0,
            "lan_viewers": 0,
            "halow_viewers": 0,
            "unclassified_viewers": 0,
            "error": str(error) if error else "MediaMTX metrics unavailable",
        }
        with self._lock:
            self._previous = None
            self._previous_at = None
            self._latest = record
            self._history.append(record)
        payload = dict(record)
        if self.broker is not None:
            self.broker.publish("video", payload)
        if self._recorder is not None:
            self._recorder("video", payload)
        return payload

    def snapshot(self):
        with self._lock:
            return dict(self._latest)

    def history(self):
        with self._lock:
            return [dict(record) for record in self._history]


class MediaMetricsPoller(threading.Thread):
    """Poll one loopback MediaMTX Prometheus endpoint."""

    MAX_RESPONSE_BYTES = 2 * 1024 * 1024

    def __init__(
        self,
        url,
        path,
        state,
        stop_event,
        interval=1.0,
    ):
        super().__init__(name="mediamtx-metrics-poller", daemon=True)
        self.url = url
        self.path = path
        self.state = state
        self.stop_event = stop_event
        self.interval = interval

    def poll_once(self):
        try:
            with urllib_request.urlopen(self.url, timeout=1.0) as response:
                raw = response.read(self.MAX_RESPONSE_BYTES + 1)
            if len(raw) > self.MAX_RESPONSE_BYTES:
                raise ValueError("MediaMTX metrics response is too large")
            text = raw.decode("utf-8")
            counters = parse_webrtc_byte_counters(text, self.path)
            return self.state.update(counters)
        except (OSError, UnicodeError, ValueError, urllib_error.URLError) as error:
            return self.state.set_unavailable(error)

    def run(self):
        while not self.stop_event.is_set():
            self.poll_once()
            self.stop_event.wait(self.interval)


class MonitorState:
    """Thread-safe view of serial ingestion and its freshest measurement."""

    def __init__(self, store, stale_after=3.0, monotonic=None, webrtc_port=8889,
                 media_path="kitties", run_store=None, halow_network="10.41.0.0/24",
                 publisher=None, video_setup=None, video_poster=None):
        self.store = store
        self.player = {"port": webrtc_port, "path": media_path}
        self.player.update(video_setup or {})
        self.video_poster = None if video_poster is None else Path(video_poster)
        self.broker = EventBroker()
        self.runs = run_store
        self.publisher = publisher
        if self.runs is not None:
            self.runs.set_broker(self.broker)
        recorder = None if self.runs is None else self.runs.record
        self.media = MediaMetricsState(
            self.broker, halow_network=halow_network, recorder=recorder
        )
        self.stale_after = stale_after
        self._monotonic = monotonic or time.monotonic
        self._last_sample_at = None
        self._parser_errors = 0
        self._serial_error = None
        self._previous_metric = None
        self._lock = threading.Lock()

    def start_capture(self):
        if self.runs is None:
            raise RuntimeError("runs are unavailable")
        if self.publisher is not None:
            self.publisher.start()
        return self.runs.start()

    def stop_capture(self, run_id):
        if self.runs is None:
            raise RuntimeError("runs are unavailable")
        record = self.runs.stop(run_id)
        if self.publisher is not None:
            self.publisher.stop()
        return record

    def accept_line(self, line):
        try:
            metric = parse_metric_line(line)
        except TelemetryParseError:
            with self._lock:
                self._parser_errors += 1
            return None
        if metric is None:
            return None

        metric.update(derive_ap_rates(self._previous_metric, metric))
        self._previous_metric = dict(metric)
        record = self.store.append(metric)
        if self.runs is not None:
            self.runs.record("ap", record)
        with self._lock:
            self._last_sample_at = self._monotonic()
            self._serial_error = None
        self.broker.publish("sample", record)
        return record

    def set_serial_error(self, message):
        with self._lock:
            self._serial_error = str(message) if message else None
        self.broker.publish("status", self.snapshot())

    def snapshot(self):
        now = self._monotonic()
        with self._lock:
            last_sample_at = self._last_sample_at
            parser_errors = self._parser_errors
            serial_error = self._serial_error

        age = None if last_sample_at is None else max(0.0, now - last_sample_at)
        if serial_error is not None:
            status = "serial-error"
        elif age is None:
            status = "waiting"
        elif age > self.stale_after:
            status = "stale"
        else:
            status = "live"
        return {
            "service": "rc32-halow-monitor",
            "api_version": 4,
            "status": status,
            "sample_age_seconds": age,
            "parser_errors": parser_errors,
            "serial_error": serial_error,
            "latest": self.store.latest,
            "video": self.media.snapshot(),
            "player": dict(self.player),
            "run": {"active": None} if self.runs is None else self.runs.snapshot(),
        }

    def history_snapshot(self):
        snapshot = self.snapshot()
        snapshot["samples"] = self.store.history()
        snapshot["video_samples"] = self.media.history()
        return snapshot


DEFAULT_SERIAL_GLOBS = ("/dev/cu.usbmodem*", "/dev/ttyACM*", "/dev/ttyUSB*")


def select_serial_device(device, autodetect_glob=DEFAULT_SERIAL_GLOBS):
    """Resolve an explicit path or require exactly one auto-detected device."""

    if device not in (None, "auto"):
        return str(device)
    patterns = (autodetect_glob,) if isinstance(autodetect_glob, str) else autodetect_glob
    candidates = sorted({path for pattern in patterns for path in glob.glob(pattern)})
    if not candidates:
        raise SerialDeviceError(
            "no serial devices match %s" % ", ".join(patterns)
        )
    if len(candidates) > 1:
        raise SerialDeviceError(
            "multiple serial devices found; use --serial with one of: %s"
            % ", ".join(candidates)
        )
    return candidates[0]


class SerialTelemetryReader(threading.Thread):
    """Continuously ingest telemetry from one explicit or auto-selected TTY."""

    MAX_LINE_BYTES = 4096

    def __init__(
        self,
        device,
        state,
        stop_event,
        autodetect_glob=DEFAULT_SERIAL_GLOBS,
    ):
        super().__init__(name="halow-serial-reader", daemon=True)
        self.device = device
        self.state = state
        self.stop_event = stop_event
        self.autodetect_glob = autodetect_glob

    def run(self):
        while not self.stop_event.is_set():
            try:
                path = select_serial_device(self.device, self.autodetect_glob)
                self._read_device(path)
            except (OSError, SerialDeviceError, termios.error) as error:
                if not self.stop_event.is_set():
                    self.state.set_serial_error(str(error))
            if not self.stop_event.is_set():
                self.stop_event.wait(1.0)

    def _read_device(self, path):
        descriptor = os.open(
            path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK
        )
        try:
            self._configure(descriptor)
            self.state.set_serial_error(None)
            buffered = bytearray()
            discarding = False
            while not self.stop_event.is_set():
                readable, _, _ = select.select([descriptor], [], [], 0.25)
                if not readable:
                    continue
                chunk = os.read(descriptor, 1024)
                if not chunk:
                    raise OSError("serial device disconnected")
                buffered, discarding = self._consume(
                    buffered, discarding, chunk
                )
        finally:
            os.close(descriptor)

    @staticmethod
    def _configure(descriptor):
        attributes = termios.tcgetattr(descriptor)
        attributes[0] = 0
        attributes[1] = 0
        attributes[2] = termios.CLOCAL | termios.CREAD | termios.CS8
        attributes[3] = 0
        attributes[4] = termios.B115200
        attributes[5] = termios.B115200
        attributes[6][termios.VMIN] = 0
        attributes[6][termios.VTIME] = 0
        termios.tcsetattr(descriptor, termios.TCSANOW, attributes)
        termios.tcflush(descriptor, termios.TCIFLUSH)

    def _consume(self, buffered, discarding, chunk):
        buffered.extend(chunk)
        while True:
            newline = buffered.find(b"\n")
            if newline < 0:
                if len(buffered) > self.MAX_LINE_BYTES:
                    buffered.clear()
                    discarding = True
                return buffered, discarding

            line = bytes(buffered[:newline]).rstrip(b"\r")
            del buffered[: newline + 1]
            if discarding:
                discarding = False
                continue
            if len(line) <= self.MAX_LINE_BYTES:
                self.state.accept_line(line.decode("utf-8", errors="replace"))


class _DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(
            error,
            (BrokenPipeError, ConnectionResetError, ConnectionAbortedError),
        ):
            return
        super().handle_error(request, client_address)


def _compact_json(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")


def build_http_server(bind, port, state, assets_dir, source_video=None):
    """Build a dashboard server whose public paths are an explicit allowlist."""

    assets_dir = Path(assets_dir)
    source_video = None if source_video is None else Path(source_video)
    assets = {
        "/": (assets_dir / "video.html", "text/html; charset=utf-8"),
        "/admin": (assets_dir / "index.html", "text/html; charset=utf-8"),
        "/admin/": (assets_dir / "index.html", "text/html; charset=utf-8"),
        "/admin/path": (assets_dir / "path.html", "text/html; charset=utf-8"),
        "/admin/path/": (assets_dir / "path.html", "text/html; charset=utf-8"),
        "/path.css": (assets_dir / "path.css", "text/css; charset=utf-8"),
        "/clock.js": (assets_dir / "clock.js", "text/javascript; charset=utf-8"),
        "/dashboard.js": (
            assets_dir / "dashboard.js",
            "text/javascript; charset=utf-8",
        ),
        "/dashboard.css": (
            assets_dir / "dashboard.css",
            "text/css; charset=utf-8",
        ),
        "/video.js": (assets_dir / "video.js", "text/javascript; charset=utf-8"),
        "/video.css": (assets_dir / "video.css", "text/css; charset=utf-8"),
    }
    if getattr(state, "video_poster", None) is not None:
        assets["/video-poster.jpg"] = (state.video_poster, "image/jpeg")

    class Handler(BaseHTTPRequestHandler):
        server_version = "HaLowMonitor/1"

        def log_message(self, _format, *_args):
            return

        def _send(self, status, content_type, body, head_only=False, extra_headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if extra_headers:
                for name, value in extra_headers.items():
                    self.send_header(name, value)
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _send_json(self, value, head_only=False, status=200):
            self._send(
                status,
                "application/json; charset=utf-8",
                _compact_json(value),
                head_only=head_only,
            )

        def _route(self, head_only=False):
            path = urlsplit(self.path).path
            if path == "/api/latest":
                self._send_json(state.snapshot(), head_only=head_only)
                return
            if path == "/api/history":
                self._send_json(state.history_snapshot(), head_only=head_only)
                return
            if path == "/healthz":
                self._send_json(state.snapshot(), head_only=head_only)
                return
            if path == "/api/time":
                self._send_json(
                    {
                        "epoch_ms": int(time.time() * 1000),
                        "utc_offset_seconds": time.localtime().tm_gmtoff,
                    },
                    head_only=head_only,
                )
                return
            if path == "/api/runs" and state.runs is not None:
                self._send_json(
                    {"runs": state.runs.list_runs(), "active": state.runs.snapshot()["active"]},
                    head_only=head_only,
                )
                return
            run_match = re.fullmatch(r"/api/runs/([^/]+)", path)
            if run_match and state.runs is not None:
                try:
                    payload = state.runs.load(run_match.group(1))
                except (KeyError, ValueError):
                    self.send_error(404, "run not found")
                    return
                self._send_json(payload, head_only=head_only)
                return
            if path == "/source-video" and source_video is not None:
                self._serve_source_video(head_only)
                return
            if path == "/measurements.csv":
                self._send(
                    200,
                    "text/csv; charset=utf-8",
                    state.store.csv_bytes(),
                    head_only=head_only,
                    extra_headers={
                        "Content-Disposition": 'attachment; filename="halow-measurements.csv"'
                    },
                )
                return
            if path == "/events":
                if head_only:
                    self._send(200, "text/event-stream", b"", head_only=True)
                else:
                    self._serve_events()
                return
            if path in assets:
                asset_path, content_type = assets[path]
                try:
                    body = asset_path.read_bytes()
                except OSError:
                    self.send_error(500, "dashboard asset unavailable")
                    return
                self._send(200, content_type, body, head_only=head_only)
                return
            self.send_error(404, "not found")

        def _serve_source_video(self, head_only):
            try:
                size = source_video.stat().st_size
            except OSError:
                self.send_error(404, "source video unavailable")
                return
            start, end = 0, max(0, size - 1)
            status = 200
            range_header = self.headers.get("Range")
            if range_header:
                match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", range_header.strip())
                if match is None or size == 0:
                    self._send(416, "text/plain", b"invalid range\n", head_only=head_only,
                               extra_headers={"Content-Range": "bytes */%d" % size})
                    return
                left, right = match.groups()
                if not left:
                    length = int(right or "0")
                    if length <= 0:
                        self._send(416, "text/plain", b"invalid range\n", head_only=head_only,
                                   extra_headers={"Content-Range": "bytes */%d" % size})
                        return
                    start = max(0, size - length)
                else:
                    start = int(left)
                    end = int(right) if right else size - 1
                if start >= size or end < start:
                    self._send(416, "text/plain", b"invalid range\n", head_only=head_only,
                               extra_headers={"Content-Range": "bytes */%d" % size})
                    return
                end = min(end, size - 1)
                status = 206
            length = max(0, end - start + 1)
            self.send_response(status)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.end_headers()
            if head_only:
                return
            try:
                with source_video.open("rb") as stream:
                    stream.seek(start)
                    remaining = length
                    while remaining:
                        chunk = stream.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (OSError, BrokenPipeError, ConnectionResetError):
                return

        def _serve_events(self):
            subscriber = state.broker.subscribe()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self._write_event("status", state.snapshot())
                while True:
                    try:
                        event, payload = subscriber.get(timeout=15.0)
                        self._write_event(event, payload)
                    except queue.Empty:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                state.broker.unsubscribe(subscriber)

        def _write_event(self, event, payload):
            self.wfile.write(b"event: " + event.encode("ascii") + b"\n")
            self.wfile.write(b"data: " + _compact_json(payload) + b"\n\n")
            self.wfile.flush()

        def do_GET(self):
            self._route(head_only=False)

        def do_HEAD(self):
            self._route(head_only=True)

        def do_POST(self):
            path = urlsplit(self.path).path
            if state.runs is None:
                self.send_error(404, "runs unavailable")
                return
            length_text = self.headers.get("Content-Length", "0")
            try:
                length = int(length_text)
            except ValueError:
                self.send_error(400, "invalid content length")
                return
            if length < 0 or length > 4096:
                self.send_error(413, "request too large")
                return
            if length:
                raw = self.rfile.read(length)
                try:
                    json.loads(raw.decode("utf-8"))
                except (UnicodeError, ValueError):
                    self.send_error(400, "invalid JSON")
                    return
            if path == "/api/runs":
                was_active = state.runs.snapshot()["active"] is not None
                try:
                    run = state.start_capture()
                except (OSError, RuntimeError) as error:
                    self.send_error(503, "capture encoder unavailable: %s" % error)
                    return
                self._send_json({"run": run}, status=200 if was_active else 201)
                return
            match = re.fullmatch(r"/api/runs/([^/]+)/stop", path)
            if match:
                try:
                    run = state.stop_capture(match.group(1))
                except (KeyError, ValueError):
                    self.send_error(409, "run is not active")
                    return
                self._send_json({"run": run})
                return
            self._method_not_allowed()

        def do_DELETE(self):
            path = urlsplit(self.path).path
            if state.runs is None:
                self._method_not_allowed()
                return
            if path == "/api/runs":
                self._send_json({"deleted": state.runs.clear_completed()})
                return
            match = re.fullmatch(r"/api/runs/([^/]+)", path)
            if match:
                try:
                    deleted = state.runs.delete_run(match.group(1))
                except KeyError:
                    self.send_error(404, "run is unavailable for deletion")
                    return
                self._send_json({"deleted": deleted})
                return
            self._method_not_allowed()

        def _method_not_allowed(self):
            body = b"method not allowed\n"
            self._send(
                405,
                "text/plain; charset=utf-8",
                body,
                extra_headers={"Allow": "GET, HEAD, POST, DELETE"},
            )

        do_PUT = _method_not_allowed
        do_PATCH = _method_not_allowed

    return _DashboardHTTPServer((bind, port), Handler)


def _default_output_path():
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return Path(__file__).resolve().parent / ".scratch" / "measurements" / (
        "halow-%s.csv" % timestamp
    )


def _argument_parser():
    parser = argparse.ArgumentParser(
        description="Serve live AP-side Wi-Fi HaLow telemetry from an RC32 base"
    )
    parser.add_argument(
        "--serial",
        default="auto",
        metavar="DEVICE",
        help="serial device path (default: auto-detect USB serial)",
    )
    parser.add_argument(
        "--bind",
        default="10.41.0.2",
        metavar="ADDRESS",
        help="dashboard listen address (default: 10.41.0.2)",
    )
    parser.add_argument(
        "--port",
        default=8091,
        type=int,
        metavar="PORT",
        help="dashboard listen port (default: 8091)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help="new CSV output path (default: timestamped project measurement)",
    )
    parser.add_argument(
        "--media-metrics-url",
        default="http://127.0.0.1:9998/metrics",
        metavar="URL",
        help="MediaMTX Prometheus endpoint (default: http://127.0.0.1:9998/metrics)",
    )
    parser.add_argument("--webrtc-port", type=int, choices=range(1, 65536), default=8889,
                        metavar="PORT", help="local WebRTC player port (default: 8889)")
    parser.add_argument(
        "--media-path",
        default="kitties",
        metavar="PATH",
        help="MediaMTX path to measure (default: kitties)",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path(__file__).resolve().parent / ".scratch" / "runs",
        metavar="DIRECTORY",
        help="measurement run directory (default: project .scratch/runs)",
    )
    parser.add_argument(
        "--run-retention",
        type=int,
        default=100,
        metavar="COUNT",
        help="completed runs to retain (default: 100)",
    )
    parser.add_argument(
        "--halow-viewer-network",
        default="10.41.0.0/24",
        metavar="CIDR",
        help="remote-address network classified as HaLow (default: 10.41.0.0/24)",
    )
    parser.add_argument(
        "--source-video",
        type=Path,
        default=None,
        metavar="PATH",
        help="source MP4 exposed for run review",
    )
    parser.add_argument("--video-source-resolution", default="unavailable", help=argparse.SUPPRESS)
    parser.add_argument("--video-output-profile", default="unavailable", help=argparse.SUPPRESS)
    parser.add_argument("--video-output-resolution", default="unavailable", help=argparse.SUPPRESS)
    parser.add_argument("--video-bitrate-kbps", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--video-poster", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--encoder-command",
        default=None,
        metavar="COMMAND",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--encoder-log",
        type=Path,
        default=None,
        metavar="PATH",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--assets",
        type=Path,
        default=Path(__file__).resolve().parent / "monitor",
        metavar="DIRECTORY",
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv=None):
    args = _argument_parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        print("error: --port must be between 0 and 65535", file=sys.stderr)
        return 2
    try:
        halow_network = ipaddress.ip_network(args.halow_viewer_network, strict=False)
    except ValueError as error:
        print("error: invalid --halow-viewer-network: %s" % error, file=sys.stderr)
        return 2
    if args.run_retention < 1:
        print("error: --run-retention must be positive", file=sys.stderr)
        return 2
    if args.source_video is not None and not args.source_video.is_file():
        print("error: source video does not exist: %s" % args.source_video, file=sys.stderr)
        return 2
    if (args.encoder_command is None) != (args.encoder_log is None):
        print("error: --encoder-command and --encoder-log must be used together", file=sys.stderr)
        return 2

    output_path = args.output or _default_output_path()
    if output_path.exists():
        print("error: output already exists: %s" % output_path, file=sys.stderr)
        return 2
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print("error: cannot create output directory: %s" % error, file=sys.stderr)
        return 2

    if args.serial == "auto":
        candidates = sorted({path for pattern in DEFAULT_SERIAL_GLOBS for path in glob.glob(pattern)})
        if len(candidates) > 1:
            print(
                "error: multiple serial devices found; use --serial with one of: %s"
                % ", ".join(candidates),
                file=sys.stderr,
            )
            return 2

    try:
        store = MeasurementStore(output_path)
    except (FileExistsError, OSError) as error:
        print("error: cannot create output CSV: %s" % error, file=sys.stderr)
        return 2

    try:
        runs = RunStore(
            args.runs_dir,
            args.source_video.name if args.source_video is not None else "unavailable",
            args.media_path,
            str(halow_network),
            retention=args.run_retention,
        )
    except OSError as error:
        store.close()
        print("error: cannot initialize run directory: %s" % error, file=sys.stderr)
        return 2
    publisher = None
    if args.encoder_command is not None:
        try:
            publisher = CapturePublisher(shlex.split(args.encoder_command), args.encoder_log)
        except ValueError as error:
            store.close()
            runs.close()
            print("error: invalid capture encoder command: %s" % error, file=sys.stderr)
            return 2
    state = MonitorState(
        store,
        webrtc_port=args.webrtc_port,
        media_path=args.media_path,
        run_store=runs,
        halow_network=str(halow_network),
        publisher=publisher,
        video_setup={"source_name": args.source_video.name if args.source_video else "unavailable",
                     "source_resolution": args.video_source_resolution,
                     "output_profile": args.video_output_profile,
                     "output_resolution": args.video_output_resolution,
                     "bitrate_kbps": args.video_bitrate_kbps},
        video_poster=args.video_poster,
    )
    try:
        server = build_http_server(
            args.bind, args.port, state, args.assets, source_video=args.source_video
        )
    except OSError as error:
        store.close()
        runs.close()
        if publisher is not None:
            publisher.stop()
        print(
            "error: cannot listen on %s:%s: %s" % (args.bind, args.port, error),
            file=sys.stderr,
        )
        return 2

    stop_event = threading.Event()
    reader = SerialTelemetryReader(args.serial, state, stop_event)
    media_poller = MediaMetricsPoller(
        args.media_metrics_url,
        args.media_path,
        state.media,
        stop_event,
    )
    server_thread = threading.Thread(
        target=server.serve_forever, name="halow-http-server", daemon=True
    )

    def request_stop(_signum, _frame):
        stop_event.set()

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    reader.start()
    media_poller.start()
    server_thread.start()
    print("HaLow dashboard: http://%s:%d/" % (args.bind, server.server_port))
    print("Measurements: %s" % output_path)
    sys.stdout.flush()

    try:
        while not stop_event.wait(0.25):
            pass
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        stop_event.set()
        server.shutdown()
        server.server_close()
        reader.join(timeout=2.0)
        media_poller.join(timeout=2.0)
        server_thread.join(timeout=2.0)
        store.close()
        runs.close()
        if publisher is not None:
            publisher.stop()
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
