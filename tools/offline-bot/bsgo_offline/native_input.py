"""Opt-in Win32 SendInput consumer for one explicitly identified window.

Requests, insertion into the Windows input stream, and observed receiver events
are separate evidence. SendInput cannot make foreground/geometry inspection and
injection atomic; the receiver must independently report what it received.
No activation, PostMessage input, keyboard hooks, or Win/meta keys are used.

API references:
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-input
https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput
https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput
"""
from __future__ import annotations

from contextlib import contextmanager
from collections import deque
import ctypes
from dataclasses import asdict, dataclass
import math
import os
import threading
import time

from .input import ActionRecord, RecordingInputBackend


DWORD, WORD, LONG, UINT, BOOL = (ctypes.c_uint32, ctypes.c_uint16,
                               ctypes.c_int32, ctypes.c_uint32, ctypes.c_int32)
ULONG_PTR, HANDLE = ctypes.c_size_t, ctypes.c_void_p
MAX_INPUT_SEQUENCE = 0xFFFFFF
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 1, 2, 8
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 1, 0x8000, 0x4000
MOUSEEVENTF_WHEEL, WHEEL_DELTA = 0x0800, 120
_BUTTON_FLAGS = {"left": (2, 4), "right": (8, 16), "middle": (32, 64)}
_VK = {key: ord(key) for key in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}
_VK.update({"Space": 0x20, "Enter": 0x0D, "Escape": 0x1B, "Tab": 9,
            "Backspace": 8, "Delete": 0x2E, "Insert": 0x2D,
            "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
            "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
            "Shift": 0x10, "Control": 0x11, "Alt": 0x12})
_VK.update({f"F{i}": 0x6F + i for i in range(1, 13)})
_MODIFIERS = ((0x10, "Shift"), (0x11, "Control"), (0x12, "Alt"),
              (0x5B, "Win"), (0x5C, "Win"))


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD),
                ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("payload",)
    _fields_ = [("type", DWORD), ("payload", _INPUT_UNION)]


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


@dataclass(frozen=True, slots=True)
class WindowStatus:
    hwnd: int
    pid: int
    alive: bool
    visible: bool
    minimized: bool
    client_size: tuple[int, int]
    screen_origin: tuple[int, int]
    foreground: int


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    request_sequence: int | None
    at: float
    kind: str
    outcome: str
    requested_count: int
    inserted_count: int
    extra_info: int
    reason: str = ""
    error_code: int = 0
    evidence: str = "windows_input_stream_insertion"


class NativeDispatchError(RuntimeError):
    pass


def input_tag(sequence: int) -> int:
    """Return a 32-bit marker with a 24-bit sequence; zero marks emergency releases.

    The owned-window experiment retained full keyboard tags but only the low
    32 bits of mouse tags in GetMessageExtraInfo. Using a bounded 32-bit marker
    preserves the same matching contract for both tested message kinds. This is
    an empirical compatibility choice, not a guarantee about every Windows path.
    """
    if type(sequence) is not int or not 0 <= sequence <= MAX_INPUT_SEQUENCE:
        raise ValueError("input tag sequence must fit in 24 bits")
    return 0x42000000 | sequence


class _Win32Api:
    """Private native seam; tests replace this object without desktop access."""

    def __init__(self):
        if os.name != "nt":
            raise OSError("NativeInputDispatcher requires Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        signatures = {
            "SendInput": ([UINT, ctypes.POINTER(INPUT), ctypes.c_int], UINT),
            "IsWindow": ([HANDLE], BOOL), "IsIconic": ([HANDLE], BOOL),
            "IsWindowVisible": ([HANDLE], BOOL),
            "GetWindowThreadProcessId": ([HANDLE, ctypes.POINTER(DWORD)], DWORD),
            "GetForegroundWindow": ([], HANDLE),
            "GetClientRect": ([HANDLE, ctypes.POINTER(RECT)], BOOL),
            "ClientToScreen": ([HANDLE, ctypes.POINTER(POINT)], BOOL),
            "WindowFromPoint": ([POINT], HANDLE),
            "GetAncestor": ([HANDLE, UINT], HANDLE),
            "GetSystemMetrics": ([ctypes.c_int], ctypes.c_int),
            "GetAsyncKeyState": ([ctypes.c_int], ctypes.c_int16),
            "GetKeyboardLayout": ([DWORD], HANDLE),
            "MapVirtualKeyExW": ([UINT, UINT, HANDLE], UINT),
            "SetThreadDpiAwarenessContext": ([HANDLE], HANDLE),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.user32, name)
            function.argtypes, function.restype = arguments, result
        expected = 40 if ctypes.sizeof(ULONG_PTR) == 8 else 28
        if ctypes.sizeof(INPUT) != expected:
            raise NativeDispatchError("INPUT structure size differs from Windows ABI")

    @contextmanager
    def dpi_context(self):
        previous = self.user32.SetThreadDpiAwarenessContext(HANDLE(-4))
        if not previous:
            raise NativeDispatchError("cannot establish per-monitor-v2 thread DPI context")
        try:
            yield
        finally:
            self.user32.SetThreadDpiAwarenessContext(previous)

    def inspect(self, hwnd: int) -> WindowStatus:
        if not self.user32.IsWindow(hwnd):
            return WindowStatus(hwnd, 0, False, False, False, (0, 0), (0, 0), 0)
        pid, rect, point = DWORD(), RECT(), POINT()
        if not self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            raise NativeDispatchError("cannot identify target process")
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            raise NativeDispatchError("cannot inspect target client rectangle")
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(point)):
            raise NativeDispatchError("cannot inspect target client screen origin")
        return WindowStatus(hwnd, pid.value, True,
                            bool(self.user32.IsWindowVisible(hwnd)),
                            bool(self.user32.IsIconic(hwnd)),
                            (rect.right - rect.left, rect.bottom - rect.top),
                            (point.x, point.y),
                            int(self.user32.GetForegroundWindow() or 0))

    def window_at(self, point: tuple[int, int]) -> tuple[int, int]:
        hwnd = self.user32.WindowFromPoint(POINT(*point))
        if not hwnd:
            return 0, 0
        pid = DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        root = self.user32.GetAncestor(hwnd, 2)  # GA_ROOT; child client controls allowed.
        return int(root or hwnd), pid.value

    def virtual_desktop(self) -> tuple[int, int, int, int]:
        return tuple(self.user32.GetSystemMetrics(index) for index in (76, 77, 78, 79))

    def modifiers(self) -> tuple[str, ...]:
        return tuple(name for vk, name in _MODIFIERS
                     if self.user32.GetAsyncKeyState(vk) & 0x8000)

    def scan_code(self, key: str, hwnd: int) -> tuple[int, bool]:
        if key not in _VK:
            raise NativeDispatchError("unsupported native key")
        pid = DWORD()
        thread_id = self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        layout = self.user32.GetKeyboardLayout(thread_id)
        scan = int(self.user32.MapVirtualKeyExW(_VK[key], 4, layout))
        if not scan or (scan >> 8) not in (0, 0xE0):
            raise NativeDispatchError("unsupported or missing key scan code")
        return scan & 0xFF, bool(scan & 0xE000)

    def send(self, events: tuple[INPUT, ...]) -> int:
        array = (INPUT * len(events))(*events)
        ctypes.set_last_error(0)
        return int(self.user32.SendInput(len(events), array, ctypes.sizeof(INPUT)))

    @staticmethod
    def last_error() -> int:
        return ctypes.get_last_error()


@dataclass(frozen=True, slots=True)
class _KeyEncoding:
    key: str
    mode: str
    scan: int = 0
    extended: bool = False


def _keyboard(encoding: _KeyEncoding, up: bool, tag: int) -> INPUT:
    flags = (KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if encoding.extended else 0)
             if encoding.mode == "scan_code" else 0)
    if up:
        flags |= KEYEVENTF_KEYUP
    event = INPUT(type=1)
    event.ki = KEYBDINPUT(0 if encoding.mode == "scan_code" else _VK[encoding.key],
                         encoding.scan if encoding.mode == "scan_code" else 0,
                         flags, 0, tag)
    return event


def _mouse(flags: int, tag: int, x: int = 0, y: int = 0, data: int = 0) -> INPUT:
    event = INPUT(type=0)
    event.mi = MOUSEINPUT(x, y, data, flags, 0, tag)
    return event


class NativeInputDispatcher:
    """Consume one recording backend for one pinned HWND/PID/layout session.

    ``_api`` is solely a test seam. Release-only emergency input may bypass the
    foreground guard for keys/buttons this dispatcher inserted, since leaving
    those held would leak input state. A PageDown cleanup press always requires
    the original window to pass every guard. No press is forwarded on focus loss.

    Delivery history is a rolling recent window; eviction never ends control.
    The optional journal must offer nonblocking ``submit(event) -> bool``. Its
    rejection/exception counts describe enqueue gaps, not disk-write durability.
    Native request tags retain the separate explicit 24-bit sequence bound.
    """

    def __init__(self, backend: RecordingInputBackend, hwnd: int, pid: int, *,
                 expected_size: tuple[int, int], expected_origin: tuple[int, int],
                 history_capacity: int | None = None, journal=None, _api=None,
                 minimum_key_hold_seconds: float = 0., keyboard_mode: str = "scan_code",
                 minimum_mouse_hold_seconds: float = 0., minimum_mouse_settle_seconds: float = 0.):
        if keyboard_mode not in ("scan_code", "virtual_key"):
            raise ValueError("keyboard_mode must be scan_code or virtual_key")
        if (type(minimum_key_hold_seconds) not in (int, float)
                or not math.isfinite(minimum_key_hold_seconds)
                or not 0 <= minimum_key_hold_seconds <= .5):
            raise ValueError("minimum_key_hold_seconds must be between zero and .5")
        if (type(minimum_mouse_hold_seconds) not in (int, float)
                or not math.isfinite(minimum_mouse_hold_seconds)
                or not 0 <= minimum_mouse_hold_seconds <= .5):
            raise ValueError("minimum_mouse_hold_seconds must be between zero and .5")
        if (type(minimum_mouse_settle_seconds) not in (int, float)
                or not math.isfinite(minimum_mouse_settle_seconds)
                or not 0 <= minimum_mouse_settle_seconds <= .5):
            raise ValueError("minimum_mouse_settle_seconds must be between zero and .5")
        if type(hwnd) is not int or hwnd <= 0 or type(pid) is not int or pid <= 0:
            raise ValueError("hwnd and pid must be positive integers")
        if (len(expected_size) != 2 or any(type(v) is not int or v <= 0 for v in expected_size)
                or len(expected_origin) != 2 or any(type(v) is not int for v in expected_origin)):
            raise ValueError("invalid expected client geometry")
        if tuple(expected_size) != (backend.width, backend.height):
            raise ValueError("recording frame must match native client pixel dimensions")
        history_capacity = backend.record_capacity if history_capacity is None else history_capacity
        if type(history_capacity) is not int or history_capacity < 1:
            raise ValueError("native delivery history capacity must be a positive integer")
        self.backend, self.hwnd, self.pid = backend, hwnd, pid
        self.expected_size, self.expected_origin = tuple(expected_size), tuple(expected_origin)
        self._session_id, self._layout_id = backend.session_id, backend.layout_id
        self._api = _Win32Api() if _api is None else _api
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._error: str | None = None
        self._deliveries: deque[DeliveryRecord] = deque(maxlen=history_capacity)
        self._last_processed_sequence = 0
        self._delivery_total = 0
        self._delivery_evicted = 0
        self._logging_gaps = 0
        self._journal = journal
        self._keys: dict[str, _KeyEncoding] = {}
        self._key_down_at: dict[str, float] = {}
        self.minimum_key_hold_seconds = minimum_key_hold_seconds
        self.minimum_mouse_hold_seconds = minimum_mouse_hold_seconds
        self.minimum_mouse_settle_seconds = minimum_mouse_settle_seconds
        self.keyboard_mode = keyboard_mode
        self._buttons: set[str] = set()

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def injected_held_keys(self) -> tuple[str, ...]:
        """Tracked inserted downs, not an independent physical-state observation."""
        with self._lock:
            return tuple(sorted(self._keys))

    @property
    def injected_held_buttons(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._buttons))

    def deliveries(self) -> tuple[DeliveryRecord, ...]:
        """Return the recent bounded window, not a complete lifetime audit trail."""
        with self._lock:
            return tuple(self._deliveries)

    def delivery_stats(self) -> dict[str, int]:
        with self._lock:
            return {"total": self._delivery_total, "retained": len(self._deliveries),
                    "evicted": self._delivery_evicted, "logging_gaps": self._logging_gaps,
                    "history_capacity": self._deliveries.maxlen,
                    "last_processed_sequence": self._last_processed_sequence}

    def _delivery(self, record: ActionRecord | None, outcome: str, requested: int,
                  inserted: int, *, reason: str = "", error_code: int = 0) -> DeliveryRecord:
        sequence = 0 if record is None else record.sequence
        tag = input_tag(sequence) if type(sequence) is int and 0 <= sequence <= MAX_INPUT_SEQUENCE else 0
        result = DeliveryRecord(None if record is None else record.sequence, time.monotonic(),
                                "emergency_release" if record is None else record.kind,
                                outcome, requested, inserted,
                                tag,
                                reason[:512], error_code)
        if len(self._deliveries) == self._deliveries.maxlen:
            self._delivery_evicted += 1
        self._deliveries.append(result)
        self._delivery_total += 1
        if self._journal is not None:
            try:
                # The supplied sink must expose a nonblocking enqueue operation.
                # A full or broken journal is evidence loss, not an input fault.
                if not self._journal.submit({"event": "native_delivery", "record": asdict(result)}):
                    self._logging_gaps += 1
            except Exception:
                self._logging_gaps += 1
        return result

    def _guard(self, point: tuple[int, int] | None = None) -> None:
        status = self._api.inspect(self.hwnd)
        if (not status.alive or status.hwnd != self.hwnd or status.pid != self.pid
                or not status.visible or status.minimized):
            raise NativeDispatchError("target is dead, hidden, minimized, or has a different identity")
        if status.foreground != self.hwnd:
            raise NativeDispatchError("target is not the exact foreground window")
        if status.client_size != self.expected_size or status.screen_origin != self.expected_origin:
            raise NativeDispatchError("target client size or screen origin changed")
        for modifier in self._api.modifiers():
            if modifier not in self._keys:
                raise NativeDispatchError(f"external modifier held: {modifier}")
        if point is not None and self._api.window_at(point) != (self.hwnd, self.pid):
            raise NativeDispatchError("client point is covered by a different window or process")

    def _release_injected(self, reason: str) -> None:
        """At most two release attempts; never add any key/button down here."""
        for _ in range(2):
            keys, buttons = tuple(sorted(self._keys)), tuple(sorted(self._buttons))
            if not keys and not buttons:
                return
            events = tuple(_keyboard(self._keys[key], True, input_tag(0)) for key in keys)
            events += tuple(_mouse(_BUTTON_FLAGS[button][1], input_tag(0)) for button in buttons)
            try:
                inserted = self._api.send(events)
                if type(inserted) is not int or not 0 <= inserted <= len(events):
                    raise NativeDispatchError("invalid cleanup SendInput insertion count")
                for index in range(inserted):
                    if index < len(keys):
                        self._keys.pop(keys[index], None)
                        self._key_down_at.pop(keys[index], None)
                    else:
                        self._buttons.discard(buttons[index - len(keys)])
                self._delivery(None, "injected" if inserted == len(events) else "partial",
                               len(events), inserted, reason=reason,
                               error_code=self._api.last_error() if inserted != len(events) else 0)
            except Exception as exc:
                self._error = f"{self._error or reason}; cleanup release failed: {exc}"[:512]
                return
        if self._keys or self._buttons:
            self._error = f"{self._error or reason}; cleanup release remained incomplete"[:512]

    def _fail(self, reason: str) -> None:
        if self._error is None:
            self._error = reason[:512]
        self.backend.cancel(reason=f"native dispatcher: {reason}"[:512])
        self._release_injected(reason)

    def dispatch(self, record: ActionRecord) -> DeliveryRecord:
        """Synchronous test/integration entry; caller must already dequeue record."""
        with self._lock:
            try:
                result = self._dispatch_in_context(record)
            except Exception as exc:
                result = self._delivery(record, "failed", 0, 0, reason=str(exc))
                self._fail(str(exc))
            self.backend.report_delivery(record, result.outcome)
            return result

    def _send_mouse_with_hold(self, events, button, record):
        """Keep an inserted down across render frames; always release our hold.

        A partial/exceptional up is handled by the existing emergency release
        path. Never delay that path or inject a second down after interruption.
        """
        moved = 0
        if self.minimum_mouse_settle_seconds:
            moved = self._api.send(events[:1])
            if type(moved) is not int or not 0 <= moved <= 1:
                raise NativeDispatchError("invalid mouse-move insertion count")
            if moved != 1:
                return moved
            deadline = time.monotonic() + self.minimum_mouse_settle_seconds
            while (remaining := deadline - time.monotonic()) > 0:
                if self.backend.canceled or self._stop_event.wait(min(remaining, .01)):
                    return moved
            self._guard((self.expected_origin[0]+record.x, self.expected_origin[1]+record.y))
            if self.backend.canceled or self._stop_event.is_set() or not self.backend.delivery_allowed(record):
                return moved
        # A failed native down call has uncertain insertion: own its release.
        self._buttons.add(button)
        inserted = self._api.send(events[1:2] if moved else events[:2])
        expected_down = 1 if moved else 2
        if type(inserted) is not int or not 0 <= inserted <= expected_down:
            self._buttons.add(button)
            raise NativeDispatchError("invalid mouse-down insertion count")
        if inserted != expected_down:
            self._buttons.discard(button)
            return moved + inserted
        self._buttons.add(button)
        down_at = time.monotonic()
        deadline = down_at + self.minimum_mouse_hold_seconds
        while (remaining := deadline - time.monotonic()) > 0:
            if self.backend.canceled or self._stop_event.wait(min(remaining, .01)):
                break
        # Release only the button we inserted, even if focus changed meanwhile.
        released = self._api.send(events[2:])
        up_at = time.monotonic()
        if type(released) is not int or not 0 <= released <= 1:
            raise NativeDispatchError("invalid mouse-up insertion count")
        if released:
            self._buttons.discard(button)
        if self._journal is not None:
            try:
                if not self._journal.submit({"event": "native_mouse_hold",
                        "request_sequence": record.sequence, "button": button,
                        "down_at": down_at, "up_at": up_at,
                        "requested_seconds": self.minimum_mouse_hold_seconds,
                        "actual_seconds": up_at-down_at,
                        "interrupted": self.backend.canceled or self._stop_event.is_set(),
                        "up_inserted": released == 1}):
                    self._logging_gaps += 1
            except Exception:
                self._logging_gaps += 1
        return moved + inserted + released

    def _send_wheel_with_settle(self, events, record):
        moved = self._api.send(events[:1])
        if type(moved) is not int or not 0 <= moved <= 1:
            raise NativeDispatchError("invalid wheel pointer-move insertion count")
        if moved != 1:
            return moved
        deadline = time.monotonic() + max(.05, self.minimum_mouse_settle_seconds)
        while (remaining := deadline - time.monotonic()) > 0:
            if self.backend.canceled or self._stop_event.wait(min(remaining, .01)):
                return moved
        point = (self.expected_origin[0] + record.x,
                 self.expected_origin[1] + record.y)
        self._guard(point)
        if self.backend.canceled or self._stop_event.is_set() or not self.backend.delivery_allowed(record):
            return moved
        inserted = self._api.send(events[1:])
        if type(inserted) is not int or not 0 <= inserted <= 1:
            raise NativeDispatchError("invalid wheel insertion count")
        return moved + inserted

    def _dispatch_in_context(self, record: ActionRecord) -> DeliveryRecord:
        with self._lock, self._api.dpi_context():
            cleanup = dict(record.metadata).get("cleanup") is True
            try:
                # Dwell starts at confirmed insertion, not request enqueue.
                # Shutdown interrupts the wait; emergency releases never wait.
                if record.kind == "key_up" and record.key in self._key_down_at:
                    until = self._key_down_at[record.key] + self.minimum_key_hold_seconds
                    while (remaining := until - time.monotonic()) > 0:
                        if self._stop_event.wait(remaining):
                            break
                if type(record.sequence) is not int or not 0 < record.sequence <= MAX_INPUT_SEQUENCE:
                    raise NativeDispatchError("request sequence outside supported tag range")
                if record.sequence <= self._last_processed_sequence:
                    self._fail("replayed or out-of-order request sequence")
                    return self._delivery(record, "rejected_replay", 0, 0)
                self._last_processed_sequence = record.sequence
                if record.session_id != self._session_id or record.layout_id != self._layout_id:
                    raise NativeDispatchError("record session or layout mismatch")
                if self.backend.session_id != self._session_id or self.backend.layout_id != self._layout_id:
                    raise NativeDispatchError("recording backend session or layout changed")
                if record.coordinate_space != "frame":
                    raise NativeDispatchError("record is not in frame coordinates")
                if cleanup and not (record.kind == "key_up"
                                    or (record.kind == "key_down" and record.key == "PageDown")):
                    raise NativeDispatchError("unsupported cleanup action")
                # A press-up can already be dequeued when cancellation empties
                # the queue. Only the dispatcher knows a down was inserted;
                # preserve its matching release even if the queue no longer
                # contains the evidence used by reserved cleanup generation.
                injected_release = record.kind == "key_up" and record.key in self._keys
                if (self.backend.canceled or self._error or self._stop_event.is_set()) and not cleanup and not injected_release:
                    return self._delivery(record, "skipped_canceled", 0, 0)
                if record.kind == "key_up" and record.key not in self._keys:
                    return self._delivery(record, "skipped_not_injected", 0, 0)
                release_only = (cleanup or self.backend.canceled) and injected_release
                if not release_only:
                    if self._error or self._stop_event.is_set():
                        return self._delivery(record, "skipped_shutdown", 0, 0)
                    self._guard()
                tag = input_tag(record.sequence)
                button = None
                if record.kind in ("key_down", "key_up"):
                    if record.key not in _VK:
                        raise NativeDispatchError("unsupported native key")
                    if record.kind == "key_down":
                        if record.key in self._keys:
                            raise NativeDispatchError("duplicate injected key down")
                        if self.keyboard_mode == "scan_code":
                            scan, extended = self._api.scan_code(record.key, self.hwnd)
                            encoding = _KeyEncoding(record.key, "scan_code", scan, extended)
                        else:
                            encoding = _KeyEncoding(record.key, "virtual_key")
                    else:
                        encoding = self._keys[record.key]
                    events = (_keyboard(encoding, record.kind == "key_up", tag),)
                elif record.kind in ("mouse_move", "mouse_click", "mouse_wheel"):
                    if (type(record.x) is not int or type(record.y) is not int
                            or not 0 <= record.x < self.expected_size[0]
                            or not 0 <= record.y < self.expected_size[1]):
                        raise NativeDispatchError("invalid client point")
                    point = (self.expected_origin[0] + record.x, self.expected_origin[1] + record.y)
                    self._guard(point)
                    left, top, width, height = self._api.virtual_desktop()
                    if width < 2 or height < 2 or not left <= point[0] < left + width or not top <= point[1] < top + height:
                        raise NativeDispatchError("point outside valid virtual desktop")
                    x = round((point[0] - left) * 65535 / (width - 1))
                    y = round((point[1] - top) * 65535 / (height - 1))
                    events = (_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                                     tag, x, y),)
                    if record.kind == "mouse_click":
                        button = record.button
                        if button not in _BUTTON_FLAGS:
                            raise NativeDispatchError("unsupported mouse button")
                        down, up = _BUTTON_FLAGS[button]
                        # Optional settle/hold phases split this ordered batch.
                        events += (_mouse(down, tag), _mouse(up, tag))
                    elif record.kind == "mouse_wheel":
                        if type(record.wheel_steps) is not int or record.wheel_steps not in (-1, 1):
                            raise NativeDispatchError("wheel steps must be exactly one notch")
                        events += (_mouse(MOUSEEVENTF_WHEEL, tag,
                                          data=record.wheel_steps * WHEEL_DELTA),)
                else:
                    raise NativeDispatchError("unsupported action kind")
                # Recheck geometry/focus after encoding, then cancellation.
                if not release_only:
                    self._guard(point if record.kind in ("mouse_move", "mouse_click", "mouse_wheel") else None)
                if self.backend.canceled and not cleanup and not injected_release:
                    return self._delivery(record, "skipped_canceled", len(events), 0)
                if not self.backend.delivery_allowed(record) and not injected_release:
                    return self._delivery(record, "skipped_dependency", len(events), 0)
                phased_mouse = button is not None and (self.minimum_mouse_hold_seconds > 0
                                                       or self.minimum_mouse_settle_seconds > 0)
                try:
                    inserted = (self._send_wheel_with_settle(events, record)
                                if record.kind == "mouse_wheel"
                                else self._send_mouse_with_hold(events, button, record)
                                if phased_mouse else self._api.send(events))
                except Exception:
                    # A native exception provides no insertion count. Release
                    # any offered downs conservatively and retain uncertainty.
                    if record.kind == "key_down":
                        self._keys[record.key] = encoding
                    elif button is not None and not phased_mouse:
                        self._buttons.add(button)
                    raise
                if type(inserted) is not int or not 0 <= inserted <= len(events):
                    if record.kind == "key_down":
                        self._keys[record.key] = encoding
                    elif button is not None:
                        self._buttons.add(button)
                    raise NativeDispatchError("invalid SendInput insertion count")
                if record.kind == "key_down" and inserted:
                    self._keys[record.key] = encoding
                    self._key_down_at[record.key] = time.monotonic()
                elif record.kind == "key_up" and inserted:
                    self._keys.pop(record.key, None)
                    self._key_down_at.pop(record.key, None)
                elif button is not None and inserted >= 2:
                    self._buttons.add(button)
                    if inserted == 3:
                        self._buttons.discard(button)
                error_code = self._api.last_error() if inserted != len(events) else 0
                result = self._delivery(record, "injected" if inserted == len(events) else "partial",
                                        len(events), inserted, error_code=error_code)
                if record.kind == "key_down" and inserted == len(events):
                    self._key_down_at[record.key] = result.at
                if inserted != len(events):
                    self._fail(f"SendInput inserted {inserted}/{len(events)} events (error {error_code})")
                return result
            except Exception as exc:
                result = self._delivery(record, "failed", 0, 0, reason=str(exc))
                self._fail(str(exc))
                return result

    def start(self) -> NativeInputDispatcher:
        with self._lock:
            if self._started or self._stop_event.is_set():
                raise NativeDispatchError("dispatcher can only be started once")
            with self._api.dpi_context():
                self._guard()
            self._started = True
            self._thread = threading.Thread(target=self._run, name="bsgo-native-input", daemon=True)
            self._thread.start()
        return self

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                record = self.backend.get_action(timeout=0.02)
                if record is not None:
                    self.dispatch(record)
        except Exception as exc:
            with self._lock:
                self._fail(str(exc))
        finally:
            with self._lock, self._api.dpi_context():
                self.backend.cancel(reason="native dispatcher stopped")
                # Bounded queue now contains cleanup only. Consume its evidence;
                # no new stop press is injected once shutdown has begun.
                for record in self.backend.drain():
                    self.dispatch(record)
                self._release_injected("native dispatcher stopped")

    def stop(self, timeout: float = 2) -> bool:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and nonnegative")
        self.backend.cancel(reason="native dispatcher stop requested")
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            if thread is threading.current_thread():
                return False
            thread.join(timeout)
            if thread.is_alive():
                return False
        else:
            with self._lock, self._api.dpi_context():
                for record in self.backend.drain():
                    self.dispatch(record)
                self._release_injected("native dispatcher stopped")
        with self._lock:
            return not self._keys and not self._buttons
