"""Windows visible-client capture, intended for a harmless test receiver first.

This GDI backend copies pixels presently visible on the desktop. Foreground
checks reduce accidental capture of another application but cannot detect every
always-on-top overlay, compositor race, occlusion, or unsupported GPU surface.
It is not a background-window or game-capture guarantee.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
from itertools import count
import math
import os
from threading import Event, Lock
import time


_SESSION_EPOCHS = count(1)


class NativeCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class WindowGeometry:
    hwnd: int
    pid: int
    title: str
    client_size: tuple[int, int]
    screen_origin: tuple[int, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_size", tuple(self.client_size))
        object.__setattr__(self, "screen_origin", tuple(self.screen_origin))


@dataclass(frozen=True)
class NativeFrame:
    acquisition_sequence: int
    session_epoch: int
    layout: str
    acquired_at: float
    width: int
    height: int
    rgb: bytes
    geometry: WindowGeometry
    source_id: str = "native-visible-client"

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("Native frame dimensions must be positive")
        pixels = bytes(self.rgb)
        if len(pixels) != self.width * self.height * 3:
            raise ValueError("Native frame RGB byte count does not match dimensions")
        if self.geometry.client_size != (self.width, self.height):
            raise ValueError("Native frame dimensions do not match window geometry")
        if not math.isfinite(self.acquired_at):
            raise ValueError("Native acquisition timestamp must be finite")
        object.__setattr__(self, "rgb", pixels)

    @property
    def frame_size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def to_image(self):
        """Return a fresh mutable image; immutable shared pixel bytes stay intact."""
        from PIL import Image
        return Image.frombytes("RGB", self.frame_size, self.rgb)


class _RECT(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int32) for name in ("left", "top", "right", "bottom")]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
               ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
               ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
               ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
               ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
               ("biClrImportant", ctypes.c_uint32)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 1)]


def _prototype(dll, name, result, args):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = args
    return function


class _Win32:
    def __init__(self) -> None:
        if os.name != "nt":
            raise NativeCaptureError("Native capture requires Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        handle, boolean, integer = ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32
        uint = ctypes.c_uint32
        for name, result, args in [
            ("IsWindow", boolean, [handle]), ("IsWindowVisible", boolean, [handle]),
            ("IsIconic", boolean, [handle]),
            ("GetClientRect", boolean, [handle, ctypes.POINTER(_RECT)]),
            ("ClientToScreen", boolean, [handle, ctypes.POINTER(_POINT)]),
            ("GetWindowThreadProcessId", uint, [handle, ctypes.POINTER(uint)]),
            ("GetWindowTextLengthW", integer, [handle]),
            ("GetWindowTextW", integer, [handle, ctypes.c_wchar_p, integer]),
            ("GetForegroundWindow", handle, []), ("GetDC", handle, [handle]),
            ("WindowFromPoint", handle, [_POINT]),
            ("GetAncestor", handle, [handle, uint]),
            ("ReleaseDC", integer, [handle, handle]),
            ("GetSystemMetrics", integer, [integer]),
            ("SetThreadDpiAwarenessContext", handle, [handle]),
        ]:
            _prototype(self.user32, name, result, args)
        for name, result, args in [
            ("CreateCompatibleDC", handle, [handle]),
            ("CreateCompatibleBitmap", handle, [handle, integer, integer]),
            ("SelectObject", handle, [handle, handle]),
            ("BitBlt", boolean, [handle, integer, integer, integer, integer,
                                 handle, integer, integer, uint]),
            ("GetDIBits", integer, [handle, handle, uint, uint, ctypes.c_void_p,
                                    ctypes.POINTER(_BITMAPINFO), uint]),
            ("DeleteObject", boolean, [handle]), ("DeleteDC", boolean, [handle]),
        ]:
            _prototype(self.gdi32, name, result, args)

    @contextmanager
    def dpi_context(self):
        # Thread-local, reversible context. Physical client-to-screen coordinates
        # and desktop bitmap pixels must share a DPI coordinate space.
        previous = self.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if not previous:
            raise NativeCaptureError("Cannot establish Per-Monitor-V2 thread DPI context")
        try:
            yield
        finally:
            if not self.user32.SetThreadDpiAwarenessContext(previous):
                raise NativeCaptureError("Cannot restore thread DPI context")

    def inspect_window(self, hwnd: int, expected_pid: int | None) -> WindowGeometry:
        if not isinstance(hwnd, int) or isinstance(hwnd, bool) or hwnd <= 0:
            raise NativeCaptureError("A positive explicit window handle is required")
        if not self.user32.IsWindow(hwnd):
            raise NativeCaptureError("Target window is no longer alive")
        if not self.user32.IsWindowVisible(hwnd):
            raise NativeCaptureError("Target window is not visible")
        if self.user32.IsIconic(hwnd):
            raise NativeCaptureError("Target window is minimized")
        pid = ctypes.c_uint32()
        if not self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            raise NativeCaptureError("Cannot read target window process identity")
        if expected_pid is not None and pid.value != expected_pid:
            raise NativeCaptureError("Target window process identity changed")
        rectangle, origin = _RECT(), _POINT()
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rectangle)):
            raise NativeCaptureError("Cannot read target client rectangle")
        size = (rectangle.right - rectangle.left, rectangle.bottom - rectangle.top)
        if min(size) <= 0:
            raise NativeCaptureError("Target window has an empty client rectangle")
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise NativeCaptureError("Cannot locate target client rectangle on screen")
        length = self.user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(max(1, length + 1))
        if length:
            self.user32.GetWindowTextW(hwnd, title, len(title))
        return WindowGeometry(hwnd, pid.value, title.value, size, (origin.x, origin.y))

    def foreground_hwnd(self) -> int:
        return int(self.user32.GetForegroundWindow() or 0)

    def ensure_unoccluded(self, geometry: WindowGeometry) -> None:
        # Sparse checks catch common overlapping windows, not every overlay or
        # compositor condition. The resulting image still needs recognition.
        width, height = geometry.client_size
        x, y = geometry.screen_origin
        positions = {(x, y), (x + width - 1, y), (x, y + height - 1),
                     (x + width - 1, y + height - 1), (x + width // 2, y + height // 2)}
        for px, py in positions:
            hit = self.user32.WindowFromPoint(_POINT(px, py))
            root = self.user32.GetAncestor(hit, 2) if hit else None
            if root != geometry.hwnd:
                raise NativeCaptureError("Target client rectangle is occluded at a sampled point")

    def capture_rgb(self, geometry: WindowGeometry) -> bytes:
        from PIL import Image
        width, height = geometry.client_size
        x, y = geometry.screen_origin
        vx, vy, vw, vh = [self.user32.GetSystemMetrics(index) for index in (76, 77, 78, 79)]
        if not (vx <= x and vy <= y and x + width <= vx + vw and y + height <= vy + vh):
            raise NativeCaptureError("Target client rectangle extends outside the virtual desktop")
        screen = memory = bitmap = selected = None
        try:
            screen = self.user32.GetDC(None)
            if not screen:
                raise NativeCaptureError("GetDC failed")
            memory = self.gdi32.CreateCompatibleDC(screen)
            if not memory:
                raise NativeCaptureError("CreateCompatibleDC failed")
            bitmap = self.gdi32.CreateCompatibleBitmap(screen, width, height)
            if not bitmap:
                raise NativeCaptureError("CreateCompatibleBitmap failed")
            old = self.gdi32.SelectObject(memory, bitmap)
            if not old or old == ctypes.c_void_p(-1).value:
                raise NativeCaptureError("SelectObject failed")
            selected = old
            # SRCCOPY | CAPTUREBLT includes visible layered overlays; it cannot
            # remove occluding windows or recover an obscured application's pixels.
            if not self.gdi32.BitBlt(memory, 0, 0, width, height, screen, x, y, 0x40CC0020):
                raise NativeCaptureError("BitBlt failed")
            # GetDIBits requires its bitmap to be unselected from a DC.
            restored = self.gdi32.SelectObject(memory, selected)
            if not restored or restored == ctypes.c_void_p(-1).value:
                raise NativeCaptureError("Cannot restore selected GDI object")
            selected = None
            info = _BITMAPINFO()
            info.bmiHeader = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), width,
                -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
            pixels = ctypes.create_string_buffer(width * height * 4)
            if self.gdi32.GetDIBits(memory, bitmap, 0, height, pixels,
                                  ctypes.byref(info), 0) != height:
                raise NativeCaptureError("GetDIBits returned incomplete pixels")
            return Image.frombytes("RGB", (width, height), pixels.raw,
                                   "raw", "BGRX", 0, 1).tobytes()
        finally:
            if selected is not None and memory:
                restored = self.gdi32.SelectObject(memory, selected)
                if not restored or restored == ctypes.c_void_p(-1).value:
                    # A bitmap cannot be deleted while selected. Destroying its
                    # DC releases that selection before deleting the bitmap.
                    self.gdi32.DeleteDC(memory)
                    memory = None
            if bitmap:
                self.gdi32.DeleteObject(bitmap)
            if memory:
                self.gdi32.DeleteDC(memory)
            if screen:
                self.user32.ReleaseDC(None, screen)


def _get_api():
    return _Win32()


def inspect_window(hwnd: int, expected_pid: int | None = None) -> WindowGeometry:
    api = _get_api()
    with api.dpi_context():
        return api.inspect_window(hwnd, expected_pid)


def _coordinate_signature(geometry: WindowGeometry) -> tuple:
    # Title text may change without changing pixel coordinates or process identity.
    return (geometry.hwnd, geometry.pid, geometry.client_size, geometry.screen_origin)


class NativeCapture:
    """Explicit HWND/PID target; first guard failure permanently ends this session.

    Moving, resizing, losing foreground, becoming minimized, or changing process
    identity requires constructing a new NativeCapture after inspecting the target.
    No focus stealing, window selection, background retries, or automatic rebinding.
    """

    def __init__(self, hwnd: int, pid: int, *, require_foreground: bool = True,
                 layout: str = "native-client-rgb") -> None:
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise NativeCaptureError("A positive explicit process ID is required")
        if not isinstance(layout, str) or not layout.strip() or len(layout) > 128:
            raise NativeCaptureError("An explicit nonempty layout name of at most 128 characters is required")
        self._api = _get_api()
        with self._api.dpi_context():
            self.geometry = self._api.inspect_window(hwnd, pid)
        self.require_foreground = require_foreground
        self.layout = layout
        self._sequence = 0
        self._epoch = next(_SESSION_EPOCHS)
        self._invalid_reason: str | None = None
        self._closed = False
        self._lock = Lock()

    def capture(self) -> NativeFrame:
        with self._lock:
            if self._closed:
                raise NativeCaptureError("Native capture is closed")
            if self._invalid_reason is not None:
                raise NativeCaptureError(f"Native capture session invalidated: {self._invalid_reason}")
            try:
                with self._api.dpi_context():
                    geometry = self._guard()
                    acquired_at = time.monotonic()
                    pixels = self._api.capture_rgb(geometry)
                    self._guard()
                width, height = geometry.client_size
                self._sequence += 1
                return NativeFrame(self._sequence, self._epoch, self.layout,
                                   acquired_at, width, height, pixels, geometry)
            except Exception as exc:
                self._invalid_reason = str(exc)
                self._epoch += 1
                raise

    def _guard(self) -> WindowGeometry:
        geometry = self._api.inspect_window(self.geometry.hwnd, self.geometry.pid)
        if _coordinate_signature(geometry) != _coordinate_signature(self.geometry):
            raise NativeCaptureError("Target client geometry changed; capture session invalidated")
        if self.require_foreground and self._api.foreground_hwnd() != self.geometry.hwnd:
            raise NativeCaptureError("Target window is not foreground")
        self._api.ensure_unoccluded(geometry)
        return geometry

    def close(self) -> None:
        with self._lock:
            self._closed = True


class NativeSource:
    """Cancellable paced captures for the independent RecognitionWorker."""

    def __init__(self, capture: NativeCapture, interval: float = .05) -> None:
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("Native capture interval must be finite and positive")
        self.capture = capture
        self.interval = interval
        self._previous_at: float | None = None

    def next_sample(self, stop_event: Event) -> NativeFrame | None:
        if stop_event.is_set():
            return None
        if self._previous_at is not None:
            remaining = max(0., self._previous_at + self.interval - time.monotonic())
            if stop_event.wait(remaining):
                return None
        if stop_event.is_set():
            return None
        sample = self.capture.capture()
        self._previous_at = sample.acquired_at
        return sample

    def close(self) -> None:
        self.capture.close()
