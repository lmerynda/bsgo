"""Contract/resource tests only: these never capture the real desktop."""
from contextlib import nullcontext
import ctypes
from dataclasses import replace
from threading import Event, Thread
import unittest
from unittest.mock import patch

from bsgo_offline import native_capture as native


GEOMETRY = native.WindowGeometry(0x100000123, 42, "receiver", (2, 1), (100, 200))


class Backend:
    def __init__(self):
        self.geometry = GEOMETRY
        self.foreground = GEOMETRY.hwnd
        self.captures = 0
        self.fail = False
        self.after_capture = None

    def dpi_context(self):
        return nullcontext()

    def inspect_window(self, hwnd, pid):
        if self.fail:
            raise native.NativeCaptureError("window no longer alive")
        if self.geometry.pid != pid:
            raise native.NativeCaptureError("process identity changed")
        return self.geometry

    def foreground_hwnd(self):
        return self.foreground

    def ensure_unoccluded(self, geometry):
        pass

    def capture_rgb(self, geometry):
        self.captures += 1
        if self.after_capture:
            self.after_capture()
        return bytes([255, 0, 0, 0, 255, 0])


class FakeGDI:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []
        self.selections = 0

    def CreateCompatibleDC(self, screen):
        self.calls.append(("create_dc", screen))
        return 0 if self.fail == "memory" else 0x200000001

    def CreateCompatibleBitmap(self, screen, width, height):
        self.calls.append(("create_bitmap", width, height))
        return 0 if self.fail == "bitmap" else 0x300000002

    def SelectObject(self, memory, bitmap):
        self.calls.append(("select", bitmap))
        self.selections += 1
        if self.fail == "restore" and self.selections > 1:
            return 0
        return 0 if self.fail == "select" else (0x400000003 if self.selections == 1 else 0x300000002)

    def BitBlt(self, *args):
        self.calls.append(("blit", args[-1]))
        return self.fail != "blit"

    def GetDIBits(self, memory, bitmap, first, lines, pixels, info, usage):
        self.calls.append(("dibits", lines))
        # BGRX -> RGB: red pixel then green pixel.
        ctypes.memmove(pixels, bytes([0, 0, 255, 0, 0, 255, 0, 0]), 8)
        return 0 if self.fail == "pixels" else lines

    def DeleteObject(self, bitmap):
        self.calls.append(("delete_bitmap", bitmap))
        return 1

    def DeleteDC(self, memory):
        self.calls.append(("delete_dc", memory))
        return 1


class FakeUser:
    def __init__(self):
        self.released = []

    def GetSystemMetrics(self, index):
        return {76: 0, 77: 0, 78: 1920, 79: 1080}[index]

    def GetDC(self, hwnd):
        return 0x100000001

    def ReleaseDC(self, hwnd, dc):
        self.released.append((hwnd, dc))
        return 1


class NativeCaptureTests(unittest.TestCase):
    def create(self, backend, **kwargs):
        with patch.object(native, "_get_api", return_value=backend):
            return native.NativeCapture(GEOMETRY.hwnd, GEOMETRY.pid, **kwargs)

    def test_capture_freezes_pixels_and_retains_64bit_target(self):
        capture = self.create(Backend())
        frame = capture.capture()
        self.assertEqual(frame.geometry.hwnd, 0x100000123)
        self.assertEqual(frame.frame_size, (2, 1))
        image = frame.to_image()
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
        image.putpixel((0, 0), (0, 0, 0))
        self.assertEqual(frame.to_image().getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(capture.capture().acquisition_sequence, 2)

    def test_explicit_layout_is_preserved_without_profile_guessing(self):
        frame = self.create(Backend(), layout="original").capture()
        self.assertEqual(frame.layout, "original")
        for layout in ["", "   ", "x" * 129, None]:
            with self.assertRaisesRegex(native.NativeCaptureError, "layout"):
                self.create(Backend(), layout=layout)

    def test_window_backend_is_read_only_and_preserves_production_time(self):
        backend = Backend()
        backend.foreground = 99
        backend.ensure_unoccluded = lambda geometry: self.fail("Desktop occlusion guard must not reject exact window pixels")
        with self.assertRaisesRegex(native.NativeCaptureError, "read-only"):
            self.create(backend, background_window=True)
        capture = self.create(backend, require_foreground=False, background_window=True)
        from unittest.mock import Mock
        window = Mock()
        window.capture.return_value = (bytes([255, 0, 0, 0, 255, 0]), 12.)
        with patch("bsgo_offline.window_capture.WindowCapture", return_value=window):
            frame = capture.capture()
        self.assertEqual(frame.source_id, "native-window-graphics-capture")
        self.assertEqual(frame.frame_produced_at, 12.)
        self.assertEqual(frame.acquired_at, 12.)
        self.assertEqual(backend.captures, 0)
        capture.close()
        window.close.assert_called_once()

    def test_window_backend_geometry_change_discards_pixels_and_closes_worker(self):
        from unittest.mock import Mock
        backend = Backend()
        capture = self.create(backend, require_foreground=False, background_window=True)
        window = Mock()
        def changed():
            backend.geometry = replace(GEOMETRY, screen_origin=(101, 200))
            return bytes([255, 0, 0, 0, 255, 0]), 12.
        window.capture.side_effect = changed
        with patch("bsgo_offline.window_capture.WindowCapture", return_value=window):
            with self.assertRaisesRegex(native.NativeCaptureError, "geometry changed"):
                capture.capture()
        self.assertEqual(capture._sequence, 0)
        window.close.assert_called_once()

    def test_loss_of_foreground_invalidates_before_reading_pixels(self):
        backend = Backend()
        capture = self.create(backend)
        backend.foreground = 99
        with self.assertRaisesRegex(native.NativeCaptureError, "foreground"):
            capture.capture()
        self.assertEqual(backend.captures, 0)
        backend.foreground = GEOMETRY.hwnd
        with self.assertRaisesRegex(native.NativeCaptureError, "invalidated"):
            capture.capture()

    def test_move_or_resize_requires_new_capture_session(self):
        for geometry in [replace(GEOMETRY, client_size=(3, 1)),
                         replace(GEOMETRY, screen_origin=(101, 200))]:
            backend = Backend()
            capture = self.create(backend)
            backend.geometry = geometry
            with self.assertRaisesRegex(native.NativeCaptureError, "geometry changed"):
                capture.capture()
            self.assertEqual(backend.captures, 0)

    def test_geometry_or_focus_change_during_blit_discards_pixels(self):
        for change in [lambda b: setattr(b, "foreground", 99),
                       lambda b: setattr(b, "geometry", replace(GEOMETRY, screen_origin=(101, 200)))]:
            backend = Backend()
            capture = self.create(backend)
            backend.after_capture = lambda: change(backend)
            with self.assertRaises(native.NativeCaptureError):
                capture.capture()
            self.assertEqual(capture._sequence, 0)

    def test_dead_window_and_pid_reuse_fail_closed(self):
        for change in [lambda b: setattr(b, "fail", True),
                       lambda b: setattr(b, "geometry", replace(GEOMETRY, pid=43))]:
            backend = Backend()
            capture = self.create(backend)
            change(backend)
            with self.assertRaises(native.NativeCaptureError):
                capture.capture()
            self.assertEqual(backend.captures, 0)

    def test_close_is_idempotent_and_prohibits_capture(self):
        capture = self.create(Backend())
        capture.close()
        capture.close()
        with self.assertRaisesRegex(native.NativeCaptureError, "closed"):
            capture.capture()

    def test_native_frame_copies_mutable_bytes(self):
        data = bytearray([255, 0, 0, 0, 255, 0])
        frame = native.NativeFrame(1, 1, "native-client-rgb", 1., 2, 1, data, GEOMETRY)
        data[0] = 0
        self.assertEqual(frame.rgb[0], 255)

    def test_new_capture_object_has_a_distinct_session_epoch(self):
        first = self.create(Backend()).capture()
        second = self.create(Backend()).capture()
        self.assertNotEqual(first.session_epoch, second.session_epoch)

    def test_source_interval_validation_and_early_cancellation(self):
        backend = Backend()
        capture = self.create(backend)
        for interval in [0, -1, float("inf"), float("nan")]:
            with self.assertRaises(ValueError):
                native.NativeSource(capture, interval)
        source = native.NativeSource(capture)
        stopped = Event()
        stopped.set()
        self.assertIsNone(source.next_sample(stopped))
        self.assertEqual(backend.captures, 0)

    def test_source_long_wait_can_be_cancelled_without_another_capture(self):
        backend = Backend()
        source = native.NativeSource(self.create(backend), interval=30)
        stopped = Event()
        self.assertIsNotNone(source.next_sample(stopped))
        returned = []
        thread = Thread(target=lambda: returned.append(source.next_sample(stopped)))
        thread.start()
        stopped.set()
        thread.join(.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(returned, [None])
        self.assertEqual(backend.captures, 1)

    def test_dpi_context_restored_when_capture_guard_fails(self):
        class DPI:
            def __init__(self):
                self.contexts = []

            def SetThreadDpiAwarenessContext(self, context):
                self.contexts.append(context)
                return 77

        api = native._Win32.__new__(native._Win32)
        api.user32 = DPI()
        with self.assertRaisesRegex(RuntimeError, "injected"):
            with api.dpi_context():
                raise RuntimeError("injected")
        self.assertEqual(api.user32.contexts[-1], 77)

    def test_window_inspection_rejects_hidden_minimized_and_empty_clients(self):
        class Inspect:
            def __init__(self):
                self.visible, self.minimized, self.width = True, False, 2

            def IsWindow(self, hwnd):
                return True

            def IsWindowVisible(self, hwnd):
                return self.visible

            def IsIconic(self, hwnd):
                return self.minimized

            def GetWindowThreadProcessId(self, hwnd, pid):
                pid._obj.value = 42
                return 1

            def GetClientRect(self, hwnd, rectangle):
                rectangle._obj.right = self.width
                rectangle._obj.bottom = 1
                return 1

            def ClientToScreen(self, hwnd, origin):
                origin._obj.x, origin._obj.y = 100, 200
                return 1

            def GetWindowTextLengthW(self, hwnd):
                return 0

        api = native._Win32.__new__(native._Win32)
        api.user32 = Inspect()
        self.assertEqual(api.inspect_window(GEOMETRY.hwnd, 42).client_size, (2, 1))
        api.user32.visible = False
        with self.assertRaisesRegex(native.NativeCaptureError, "not visible"):
            api.inspect_window(GEOMETRY.hwnd, 42)
        api.user32.visible, api.user32.minimized = True, True
        with self.assertRaisesRegex(native.NativeCaptureError, "minimized"):
            api.inspect_window(GEOMETRY.hwnd, 42)
        api.user32.minimized, api.user32.width = False, 0
        with self.assertRaisesRegex(native.NativeCaptureError, "empty"):
            api.inspect_window(GEOMETRY.hwnd, 42)

    def test_bitmap_conversion_and_cleanup(self):
        api = native._Win32.__new__(native._Win32)
        api.user32, api.gdi32 = FakeUser(), FakeGDI()
        pixels = api.capture_rgb(GEOMETRY)
        self.assertEqual(pixels, bytes([255, 0, 0, 0, 255, 0]))
        names = [call[0] for call in api.gdi32.calls]
        self.assertEqual(names.count("select"), 2)
        self.assertLess(names.index("select", names.index("select") + 1), names.index("dibits"))
        self.assertEqual(names[-2:], ["delete_bitmap", "delete_dc"])
        self.assertEqual(api.user32.released, [(None, 0x100000001)])

    def test_gdi_resources_cleaned_for_every_partial_failure(self):
        for failure in ["memory", "bitmap", "select", "blit", "pixels", "restore"]:
            with self.subTest(failure=failure):
                api = native._Win32.__new__(native._Win32)
                api.user32, api.gdi32 = FakeUser(), FakeGDI(failure)
                with self.assertRaises(native.NativeCaptureError):
                    api.capture_rgb(GEOMETRY)
                names = [call[0] for call in api.gdi32.calls]
                self.assertEqual(api.user32.released, [(None, 0x100000001)])
                if failure != "memory":
                    self.assertIn("delete_dc", names)
                if failure not in ("memory", "bitmap"):
                    self.assertIn("delete_bitmap", names)
                if failure == "restore":
                    self.assertLess(names.index("delete_dc"), names.index("delete_bitmap"))

    def test_sampled_occlusion_guard_checks_root_window(self):
        class HitTest:
            def __init__(self):
                self.points = []
                self.hit = GEOMETRY.hwnd

            def WindowFromPoint(self, point):
                self.points.append((point.x, point.y))
                return self.hit

            def GetAncestor(self, hwnd, flags):
                return hwnd

        api = native._Win32.__new__(native._Win32)
        api.user32 = HitTest()
        api.ensure_unoccluded(GEOMETRY)
        self.assertTrue(api.user32.points)
        api.user32.hit = 99
        with self.assertRaisesRegex(native.NativeCaptureError, "occluded"):
            api.ensure_unoccluded(GEOMETRY)

    def test_off_desktop_region_rejected_before_gdi_allocation(self):
        api = native._Win32.__new__(native._Win32)
        api.user32, api.gdi32 = FakeUser(), FakeGDI()
        with self.assertRaisesRegex(native.NativeCaptureError, "outside"):
            api.capture_rgb(replace(GEOMETRY, screen_origin=(-1, 0)))
        self.assertFalse(api.gdi32.calls)

    def test_win32_requires_windows_and_bitmap_header_has_fixed_layout(self):
        self.assertEqual(ctypes.sizeof(native._BITMAPINFOHEADER), 40)
        with patch.object(native.os, "name", "posix"):
            with self.assertRaisesRegex(native.NativeCaptureError, "requires Windows"):
                native._Win32()


if __name__ == "__main__":
    unittest.main()
