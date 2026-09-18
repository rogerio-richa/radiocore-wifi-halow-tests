import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSET_ROOT = PROJECT_ROOT / "monitor"


class DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = set()
        self.text = []
        self.tag_stack = []
        self.header_links = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = set(attributes.get("class", "").split())
        if tag == "a" and "href" in attributes and any(
            "instrument-head" in ancestor_classes
            for _, ancestor_classes in self.tag_stack
        ):
            self.header_links.add(attributes["href"])
        if "id" in attributes:
            self.ids.add(attributes["id"])
        if tag == "a" and "href" in attributes:
            self.links.add(attributes["href"])
        self.tag_stack.append((tag, classes))

    def handle_endtag(self, tag):
        while self.tag_stack:
            open_tag, _ = self.tag_stack.pop()
            if open_tag == tag:
                break

    def handle_data(self, data):
        self.text.append(data)


class DashboardAssetContractTests(unittest.TestCase):
    def setUp(self):
        self.html = (ASSET_ROOT / "index.html").read_text()
        self.css = (ASSET_ROOT / "dashboard.css").read_text()
        self.javascript = (ASSET_ROOT / "dashboard.js").read_text()
        self.parser = DashboardParser()
        self.parser.feed(self.html)

    def test_required_readouts_and_chart_are_present(self):
        required = {
            "connection-state",
            "sample-age",
            "halow-total-value",
            "halow-downlink-value",
            "halow-uplink-value",
            "drops-value",
            "halow-clients-value",
            "radio-activity-value",
            "halow-phy-ceiling-value",
            "ap-history-canvas",
            "lan-video-bitrate-value",
            "halow-video-bitrate-value",
            "lan-video-viewers-value",
            "halow-video-viewers-value",
            "video-status-value",
            "video-history-canvas",
            "run-play",
            "run-stop",
            "review-runs",
            "run-elapsed",
            "run-browser",
            "run-list",
            "review-player",
            "review-playback",
            "review-scrubber",
            "exit-review",
            "clear-runs",
        }

        self.assertEqual(required - self.parser.ids, set())

    def test_ap_panel_marks_the_configured_phy_ceiling_without_rescaling_the_chart(self):
        visible_text = " ".join(self.parser.text)

        self.assertIn("1 MHz MCS7 PHY ceiling", visible_text)
        self.assertIn("3,333", visible_text)
        self.assertNotIn("drawReferenceLine", self.javascript)
        self.assertNotIn("HALOW_PHY_CEILING_KBPS", self.javascript)
        self.assertNotIn("key--phy-ceiling", self.html)

    def test_measurements_download_is_in_review_and_boundary_card_is_absent(self):
        visible_text = " ".join(self.parser.text)

        self.assertIn("/measurements.csv", self.parser.links)
        self.assertIn("Download AP measurements", visible_text)
        self.assertNotIn("Measurement boundary", visible_text)
        self.assertNotIn("Two meters, never one blended number", visible_text)
        self.assertNotIn("Combined bitrate", visible_text)

    def test_javascript_uses_history_and_server_sent_events(self):
        self.assertIn("/api/history", self.javascript)
        self.assertIn("/events", self.javascript)
        self.assertIn("EventSource", self.javascript)

    def test_live_player_is_present(self):
        self.assertIn('id="live-player"', self.html)
        self.assertIn('id="admin-video-poster"', self.html)
        self.assertIn("adminVideoPoster", self.javascript)
        self.assertIn("frame.hidden = !enabled", self.javascript)
        self.assertIn(".live-video iframe[hidden] { display: none; }", self.css)
        self.assertIn(".video-setup .metric-bank { grid-template-columns: 1fr; margin-top: 8px; }", self.css)
        self.assertIn('id="source-name"', self.html)
        self.assertIn('id="source-resolution"', self.html)
        self.assertIn('id="output-resolution"', self.html)
        self.assertIn('class="live-video-body"', self.html)
        self.assertIn('allow="autoplay; fullscreen"', self.html)
        visible_text = " ".join(self.parser.text)
        self.assertNotIn("Compare the elapsed time in the video", visible_text)
        self.assertNotIn("source-chip--video\">Elapsed time", self.html)
        self.assertIn("location.hostname", self.javascript)
        self.assertIn("snapshot.player", self.javascript)
        self.assertIn("source_resolution", self.javascript)
        self.assertIn("output_resolution", self.javascript)

    def test_run_controls_and_endpoint_delivery_are_explicit(self):
        visible_text = " ".join(self.parser.text)

        for label in ("CAPTURE", "STOP", "Review runs", "Delete stored runs", "LAN delivery", "HaLow delivery", "Source reference"):
            self.assertIn(label, visible_text)
        self.assertNotIn("Aggregate viewer delivery", visible_text)
        self.assertIn("lan_kbps", self.javascript)
        self.assertIn("halow_kbps", self.javascript)
        self.assertIn('addEventListener("run"', self.javascript)
        self.assertIn("deleteRun", self.javascript)
        self.assertIn("Delete this stored run", self.javascript)
        self.assertIn("run-choice--selected", self.javascript)
        self.assertIn("Replay review", self.javascript)

    def test_live_panel_shows_the_synced_pi_clock(self):
        clock = (ASSET_ROOT / "clock.js").read_text()

        self.assertIn('id="pi-clock"', self.html)
        self.assertIn('id="pi-clock-sync"', self.html)
        self.assertIn('<script src="/clock.js" defer></script>', self.html)
        self.assertIn("/api/time", clock)
        self.assertIn("utc_offset_seconds", clock)
        self.assertIn("performance.now()", clock)
        self.assertNotIn("http://", clock.lower())
        self.assertNotIn("https://", clock.lower())

    def test_review_mode_hides_the_live_video_comparison_content(self):
        self.assertIn('body[data-mode="review"] .live-video > .panel-head', self.css)
        self.assertIn('body[data-mode="review"] .live-video > .live-video-body', self.css)

    def test_stored_measurements_review_has_no_top_divider(self):
        self.assertIn(".run-browser {\n  margin-top: 16px;", self.css)
        self.assertNotIn(".run-browser {\n  border-top:", self.css)
        self.assertIn('aria-pressed', self.javascript)

    def test_header_links_to_the_network_path_page_without_a_topology_strip(self):
        visible_text = " ".join(self.parser.text)

        self.assertIn("/admin/path/", self.parser.header_links)
        self.assertNotIn("path-strip", self.html)
        self.assertNotIn("path-strip", self.css)
        self.assertNotIn("Portable RC32", visible_text)

    def test_traffic_directions_use_destination_labels(self):
        visible_text = " ".join(self.parser.text)

        self.assertIn("Toward phone", visible_text)
        self.assertIn("HaLow downlink", visible_text)
        self.assertIn("Toward Mac", visible_text)
        self.assertIn("HaLow uplink", visible_text)

    def test_assets_are_fully_local_and_contain_no_external_assets_or_analytics(self):
        combined = "\n".join((self.html, self.css, self.javascript)).lower()

        for forbidden in (
            "http://",
            "https://",
            "@import",
            "analytics",
            "googletag",
            "third-party",
        ):
            self.assertNotIn(forbidden, combined)
        self.assertIsNone(re.search(r"<script[^>]+src=[\"'](?:/|\.)?[^\"']*[?&]", self.html))


class NetworkPathPageContractTests(unittest.TestCase):
    def setUp(self):
        self.html = (ASSET_ROOT / "path.html").read_text()
        self.css = (ASSET_ROOT / "path.css").read_text()
        self.parser = DashboardParser()
        self.parser.feed(self.html)
        self.visible_text = " ".join(self.parser.text)

    def test_page_describes_every_hop_in_path_order(self):
        for hop_id in ("hop-mac", "hop-base", "hop-halow", "hop-portable", "hop-phone"):
            self.assertIn(hop_id, self.parser.ids)
        self.assertLess(self.html.index('id="hop-mac"'), self.html.index('id="hop-base"'))
        self.assertLess(self.html.index('id="hop-base"'), self.html.index('id="hop-halow"'))
        self.assertLess(self.html.index('id="hop-halow"'), self.html.index('id="hop-portable"'))
        self.assertLess(self.html.index('id="hop-portable"'), self.html.index('id="hop-phone"'))

    def test_page_names_what_runs_on_each_box(self):
        for label in (
            "Raspberry Pi",
            "FFmpeg",
            "MediaMTX",
            "halow_monitor.py",
            "ESP32-S3",
            "HT-HC01",
            "NAPT",
            "DHCP",
            "802.11ah",
            "WebRTC",
            "10.41.0.3",
            "10.41.0.1",
            "10.42.0.1",
            "10.42.0.2",
            "10.43.0.1",
            "RC32-Base",
            "RC32-HaLow-Backbone",
            "RC32-HaLow",
            "922.5",
            "1 MHz",
        ):
            self.assertIn(label, self.visible_text)

    def test_page_links_back_to_the_dashboard_and_has_its_own_stylesheet(self):
        self.assertIn("/admin/", self.parser.links)
        self.assertIn('href="/path.css"', self.html)
        self.assertNotIn("dashboard.css", self.html)
        self.assertIn("prefers-reduced-motion", self.css)

    def test_page_draws_every_box_and_link_as_inline_svg(self):
        for symbol in ("sym-pi", "sym-rc32-base", "sym-rc32-portable", "sym-phone", "sym-link-wifi", "sym-link-halow"):
            self.assertIn('id="%s"' % symbol, self.html)
            self.assertIn('href="#%s"' % symbol, self.html)
        self.assertNotIn("<img", self.html)

    def test_page_omits_secrets_and_external_assets(self):
        lowered = "\n".join((self.html, self.css)).lower()
        for forbidden in ("http://", "https://", "@import", "analytics", "passphrase:", "<script"):
            self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
