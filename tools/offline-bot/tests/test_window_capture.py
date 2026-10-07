"""Exact-window capture contract tests without touching the Windows desktop."""
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

from bsgo_offline.native_capture import NativeCaptureError, WindowGeometry
from bsgo_offline import window_capture as window


class WindowCaptureTests(unittest.TestCase):
    def make_capture(self, results):
        connection = Mock()
        connection.recv.side_effect = results
        connection.poll.return_value = True
        process = Mock()
        process.is_alive.return_value = False
        context = Mock()
        context.Pipe.return_value = connection, Mock()
        context.Process.return_value = process
        with patch.object(window.importlib.util, "find_spec", return_value=True):
            capture = window.WindowCapture(WindowGeometry(123, 42, "game", (2, 1), (0, 0)), context=context)
        return capture, connection, process

    def test_dependency_missing_has_explicit_error(self):
        with patch.object(window.importlib.util, "find_spec", return_value=None):
            with self.assertRaisesRegex(NativeCaptureError, "windows-capture==2.0.1"):
                window.WindowCapture(None)

    def test_source_time_maps_qpc_age_and_rejects_stale_or_future_frame(self):
        self.assertAlmostEqual(window._produced_at(100_000_000, 100., 10.125), 99.875)
        for timestamp in [0, 120_000_000, float("nan")]:
            with self.assertRaisesRegex(NativeCaptureError, "timestamp"):
                window._produced_at(timestamp, 100., 10.)

    def test_client_surface_crop_excludes_window_chrome_and_copies_rgb(self):
        import numpy as np
        pixels = np.zeros((4, 5, 4), dtype=np.uint8)
        pixels[2, 1] = [0, 0, 255, 255]
        pixels[2, 2] = [0, 255, 0, 255]
        frame = SimpleNamespace(width=5, height=4, frame_buffer=pixels)
        geometry = WindowGeometry(123, 42, "game", (2, 1), (101, 202))
        result = window._client_pixels(frame, geometry, (100, 200, 105, 204))
        self.assertEqual(result, bytes([255, 0, 0, 0, 255, 0]))
        pixels[2, 1] = 0
        self.assertEqual(result[0], 255)
        with self.assertRaisesRegex(NativeCaptureError, "dimensions"):
            window._client_pixels(frame, geometry, (100, 200, 104, 204))
        with self.assertRaisesRegex(NativeCaptureError, "outside"):
            window._client_pixels(frame, geometry, (105, 200, 110, 204))

    def test_repeated_source_frame_is_rejected_and_worker_closed(self):
        now = time.monotonic()
        capture, connection, process = self.make_capture([("frame", b"pixels", now, 100),
                                                         ("frame", b"pixels", now, 100)])
        self.assertEqual(capture.capture(), (b"pixels", now))
        with self.assertRaisesRegex(NativeCaptureError, "did not advance"):
            capture.capture()
        process.join.assert_called_once_with(.5)
        process.close.assert_called_once()
        connection.close.assert_called_once()

    def test_timeout_and_backend_error_close_owned_worker(self):
        for timeout in [True, False]:
            capture, connection, process = self.make_capture([("error", "game unavailable")])
            connection.poll.return_value = not timeout
            with self.assertRaisesRegex(NativeCaptureError, "timed out" if timeout else "game unavailable"):
                capture.capture()
            process.close.assert_called_once()

    def test_stale_source_frame_is_not_redated_on_receipt(self):
        capture, _, process = self.make_capture([("frame", b"pixels", time.monotonic() - 2., 100)])
        with self.assertRaisesRegex(NativeCaptureError, "stale"):
            capture.capture()
        process.close.assert_called_once()

    def test_shutdown_is_bounded_and_only_targets_owned_process(self):
        capture, connection, process = self.make_capture([])
        process.is_alive.side_effect = [True, True, False]
        capture.close()
        capture.close()
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.join.call_count, 3)
        connection.close.assert_called_once()
        with self.assertRaisesRegex(NativeCaptureError, "closed"):
            capture.capture()


if __name__ == "__main__":
    unittest.main()
