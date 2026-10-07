"""Exact HWND read-only capture, isolated so native shutdown is always bounded.

Windows Graphics Capture copies the application's surface rather than desktop
overlays. Input and foreground capture continue to use their original guards.
"""
from __future__ import annotations

import ctypes
import importlib.util
import math
import multiprocessing
import os
from threading import Thread
import time

from .native_capture import NativeCaptureError, _RECT, _coordinate_signature, _get_api


def _window_bounds(hwnd):
    rectangle = _RECT()
    dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    function = dwm.DwmGetWindowAttribute
    function.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    function.restype = ctypes.c_int32
    if function(hwnd, 9, ctypes.byref(rectangle), ctypes.sizeof(rectangle)) != 0:
        raise NativeCaptureError("Cannot inspect captured window frame bounds")
    return (rectangle.left, rectangle.top, rectangle.right, rectangle.bottom)


def _produced_at(timespan, now, performance_now):
    """Map WGC's QPC-based 100ns source clock onto Python monotonic time."""
    age = performance_now - timespan / 10_000_000.
    if not math.isfinite(age) or age < -.1 or age > 1.:
        raise NativeCaptureError("Window capture returned an invalid or stale source timestamp")
    return now - max(0., age)


def _client_pixels(frame, geometry, bounds):
    left, top, right, bottom = bounds
    if (frame.width, frame.height) != (right - left, bottom - top):
        raise NativeCaptureError("Captured window frame dimensions changed or are unsupported")
    width, height = geometry.client_size
    x, y = geometry.screen_origin[0] - left, geometry.screen_origin[1] - top
    if x < 0 or y < 0 or x + width > frame.width or y + height > frame.height:
        raise NativeCaptureError("Game client rectangle is outside captured window surface")
    # The copy must happen before the library releases its mapped native frame.
    return frame.frame_buffer[y:y + height, x:x + width, 2::-1].tobytes()


def _watch_parent(parent):
    while parent.is_alive():
        time.sleep(.25)
    os._exit(0)


def _worker(connection, geometry):
    try:
        # A crashed launcher cannot run Python's usual daemon-child cleanup.
        # Watch its OS process sentinel even when the game stops producing frames.
        parent = multiprocessing.parent_process()
        if parent is not None:
            Thread(target=_watch_parent, args=(parent,), daemon=True,
                   name="capture-parent-watch").start()
        from windows_capture import WindowsCapture
        api = _get_api()
        capture = WindowsCapture(window_hwnd=geometry.hwnd, cursor_capture=False,
                                 draw_border=False, minimum_update_interval=100)

        @capture.event
        def on_frame_arrived(frame, control):
            try:
                if not connection.poll():
                    return
                command = connection.recv()
                if command == "close":
                    control.stop()
                    return
                if command != "capture":
                    raise NativeCaptureError("Unexpected window capture command")
                received_at, performance_now = time.monotonic(), time.perf_counter()
                produced_at = _produced_at(frame.timespan, received_at, performance_now)
                with api.dpi_context():
                    current = api.inspect_window(geometry.hwnd, geometry.pid)
                    if _coordinate_signature(current) != _coordinate_signature(geometry):
                        raise NativeCaptureError("Target client geometry changed during window capture")
                    pixels = _client_pixels(frame, geometry, _window_bounds(geometry.hwnd))
                connection.send(("frame", pixels, produced_at, frame.timespan))
            except Exception as exc:
                try:
                    connection.send(("error", str(exc)))
                finally:
                    control.stop()

        @capture.event
        def on_closed():
            connection.send(("error", "Target window capture closed"))

        capture.start()
    except Exception as exc:
        try:
            connection.send(("error", str(exc)))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


class WindowCapture:
    """Owned worker; a fresh source frame per request, no title-based selection."""

    def __init__(self, geometry, *, timeout=3., context=None):
        if importlib.util.find_spec("windows_capture") is None:
            raise NativeCaptureError("Read-only window capture requires windows-capture==2.0.1")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Window capture timeout must be finite and positive")
        self.timeout = timeout
        self._closed = False
        self._previous_timespan = None
        context = context or multiprocessing.get_context("spawn")
        self._connection, child = context.Pipe()
        self._process = context.Process(target=_worker, args=(child, geometry), daemon=True,
                                        name="bsgo-read-only-window-capture")
        try:
            self._process.start()
        except Exception:
            self._connection.close()
            raise
        finally:
            child.close()

    def capture(self):
        if self._closed:
            raise NativeCaptureError("Window capture is closed")
        try:
            self._connection.send("capture")
            if not self._connection.poll(self.timeout):
                raise NativeCaptureError("Window capture timed out waiting for a fresh game frame")
            result = self._connection.recv()
            if result[0] != "frame":
                raise NativeCaptureError(f"Window capture failed: {result[1]}")
            _, pixels, produced_at, timespan = result
            if self._previous_timespan is not None and timespan <= self._previous_timespan:
                raise NativeCaptureError("Window capture source timestamp did not advance")
            if not math.isfinite(produced_at) or time.monotonic() - produced_at > 1.:
                raise NativeCaptureError("Window capture source frame is stale")
            self._previous_timespan = timespan
            return pixels, produced_at
        except Exception:
            self.close()
            raise

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._connection.send("close")
        except (BrokenPipeError, EOFError, OSError):
            pass
        self._process.join(.5)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(.5)
        if self._process.is_alive():
            self._process.kill()
            self._process.join(.5)
        self._connection.close()
        if self._process.is_alive():
            raise NativeCaptureError("Owned window capture worker did not terminate")
        self._process.close()
