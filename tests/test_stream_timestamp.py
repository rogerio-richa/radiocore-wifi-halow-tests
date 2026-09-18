import datetime
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from stream_timestamp import local_midnight_epoch_us, timestamp_filter


class StreamTimestampTests(unittest.TestCase):
    def test_drawtext_renderer_prints_the_wall_clock_to_the_second(self):
        result = subprocess.run(
            ['/usr/bin/python3', 'stream_timestamp.py', '--drawtext', '--fps', '15'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('drawtext=', result.stdout)
        self.assertIn('%{localtime\\:%T}', result.stdout)
        self.assertNotIn('pts', result.stdout)
        self.assertNotIn('drawbox=', result.stdout)

    def test_seven_segment_renderer_reads_the_wall_clock_and_restores_frame_timing(self):
        video_filter = timestamp_filter(15)
        self.assertIn('setpts=RTCTIME-', video_filter)
        self.assertTrue(video_filter.endswith('setpts=N/(15*TB)'))
        self.assertNotIn('.mmm', video_filter)
        # Six digits (HH MM SS) and two colons, no millisecond digits.
        self.assertEqual(video_filter.count("enable='"), 6 * 7)

    def test_local_midnight_offset_is_todays_midnight_in_local_time(self):
        now = datetime.datetime.now()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(local_midnight_epoch_us(now), int(midnight.timestamp() * 1_000_000))

    def test_wall_clock_overlay_renders_on_identical_frames(self):
        if not shutil.which('ffmpeg'):
            self.skipTest('FFmpeg not installed')
        result = subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
            '-i', 'color=black:s=640x360:r=2:d=2', '-vf', timestamp_filter(2),
            '-f', 'framemd5', '-'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line for line in result.stdout.splitlines() if line and not line.startswith('#')]
        self.assertEqual(len(lines), 4)
        # Output timestamps are regenerated as a clean constant-rate sequence.
        pts = [int(line.split(',')[2]) for line in lines]
        steps = {later - earlier for earlier, later in zip(pts, pts[1:])}
        self.assertEqual(pts[0], 0)
        self.assertEqual(len(steps), 1)
        self.assertGreater(steps.pop(), 0)

    def test_wall_clock_overlay_survives_input_loops(self):
        if not shutil.which('ffmpeg'):
            self.skipTest('FFmpeg not installed')
        with tempfile.TemporaryDirectory() as directory:
            clip = str(Path(directory) / 'clip.mkv')
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=black:s=640x360:r=2:d=1', '-c:v', 'ffv1', clip],
                           check=True, capture_output=True)
            result = subprocess.run([
                'ffmpeg', '-v', 'error', '-stream_loop', '2', '-i', clip,
                '-vf', timestamp_filter(2), '-frames:v', '6', '-f', 'framemd5', '-'],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = [line for line in result.stdout.splitlines() if line and not line.startswith('#')]
            self.assertEqual(len(lines), 6)
            pts = [int(line.split(',')[2]) for line in lines]
            self.assertEqual(pts, sorted(pts), 'frame timing must stay monotonic across loops')
            self.assertEqual(len(set(pts)), 6)
