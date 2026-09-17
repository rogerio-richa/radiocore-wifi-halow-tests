import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from stream_timestamp import timestamp_filter


class StreamTimestampTests(unittest.TestCase):
    def test_drawtext_renderer_uses_pts_clock(self):
        result = subprocess.run(
            ['/usr/bin/python3', 'stream_timestamp.py', '--drawtext'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('drawtext=', result.stdout)
        self.assertIn('%{pts\\:hms}', result.stdout)
        self.assertNotIn('drawbox=', result.stdout)

    def test_elapsed_overlay_renders_and_advances_on_identical_frames(self):
        if not shutil.which('ffmpeg'):
            self.skipTest('FFmpeg not installed')
        video_filter = timestamp_filter()
        result = subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
            '-i', 'color=black:s=640x360:r=2:d=2', '-vf', video_filter,
            '-f', 'framemd5', '-'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        hashes = [line.split(',')[-1].strip() for line in result.stdout.splitlines()
                  if line and not line.startswith('#')]
        self.assertEqual(len(hashes), 4)
        self.assertEqual(len(set(hashes)), 4, 'elapsed time must advance each frame')

    def test_elapsed_time_continues_across_input_loops(self):
        if not shutil.which('ffmpeg'):
            self.skipTest('FFmpeg not installed')
        with tempfile.TemporaryDirectory() as directory:
            clip = str(Path(directory) / 'clip.mkv')
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=black:s=640x360:r=2:d=1', '-c:v', 'ffv1', clip],
                           check=True, capture_output=True)
            result = subprocess.run([
                'ffmpeg', '-v', 'error', '-stream_loop', '2', '-i', clip,
                '-vf', timestamp_filter(), '-frames:v', '6', '-f', 'framemd5', '-'],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            hashes = [line.split(',')[-1].strip() for line in result.stdout.splitlines()
                      if line and not line.startswith('#')]
            self.assertEqual(len(hashes), 6)
            self.assertEqual(len(set(hashes)), 6, 'clock must not reset on each loop')
