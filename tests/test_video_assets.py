import unittest
from pathlib import Path


ASSET_ROOT = Path(__file__).resolve().parent.parent / "monitor"


class VideoAssetContractTests(unittest.TestCase):
    def test_video_only_page_has_the_player_and_base_measurements(self):
        html = (ASSET_ROOT / "video.html").read_text()
        javascript = (ASSET_ROOT / "video.js").read_text()

        self.assertIn('id="video-player"', html)
        self.assertIn('id="video-poster"', html)
        self.assertIn("/video-poster.jpg", html)
        self.assertNotIn("Video setup", html)
        self.assertNotIn("Source file", html)
        self.assertIn("Measured by base firmware", html)
        self.assertIn("Total interface throughput", html)
        self.assertIn("RSSI", html)
        self.assertIn("SNR", html)
        self.assertIn("/api/latest", javascript)
        self.assertIn("snapshot.player", javascript)
        self.assertIn("if (!run || !playerConfig)", javascript)
        self.assertIn("player.removeAttribute(\"src\")", javascript)
        self.assertIn("poster.hidden = true", javascript)
        self.assertIn("#video-poster[hidden] { display: none; }", (ASSET_ROOT / "video.css").read_text())
