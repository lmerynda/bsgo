"""System-wide F8-F11 hotkeys for the side panel; registration never sends input."""
import ctypes
from ctypes import wintypes
from threading import Event, Lock, Thread
import time


MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
VIRTUAL_KEYS = {"F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A}
# F8 is handled by the core so Pause works even when no panel is open. The
# others are published as requests; the panel applies its own start checks.
KEY_ACTIONS = {"F8": "pause", "F9": "scan_view", "F10": "approach", "F11": "auto_scan"}


class HotkeyEvents:
    """Monotonic hotkey sequence read by the panel; old presses are never replayed."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._lock = Lock()
        self._sequence = 0
        self._last = None
        self._claimed = 0
        self._registered = {key: None for key in KEY_ACTIONS}

    def record(self, key, outcome=None):
        with self._lock:
            self._sequence += 1
            self._last = {"sequence": self._sequence, "key": key, "action": KEY_ACTIONS[key],
                          "at": self.clock(), "outcome": outcome}
            return dict(self._last)

    def claim(self, sequence, max_age=3.):
        """Hand the latest press to exactly one panel; every other panel is refused.

        Several panels can be open at once (a reopened window, a panel left over
        from a restarted server). Without a single claim, one press would start a
        routine in one panel and stop it again in another.
        """
        with self._lock:
            last = self._last
            if type(sequence) is not int or not last or last["sequence"] != sequence:
                return {"accepted": False, "reason": "hotkey_press_superseded"}
            if self._claimed >= sequence:
                return {"accepted": False, "reason": "hotkey_press_already_handled"}
            if not 0 <= self.clock() - last["at"] <= max_age:
                return {"accepted": False, "reason": "hotkey_press_expired"}
            self._claimed = sequence
            return {"accepted": True, "reason": "hotkey_press_claimed", "action": last["action"]}

    def set_registered(self, key, value):
        with self._lock:
            self._registered[key] = value

    def snapshot(self):
        with self._lock:
            return {"sequence": self._sequence, "last": dict(self._last) if self._last else None,
                    "registered": dict(self._registered), "actions": dict(KEY_ACTIONS),
                    "server_monotonic": self.clock()}


class GlobalHotkeys:
    """Owns RegisterHotKey on one message-loop thread; stop() unregisters them."""
    def __init__(self, events, on_pause, *, user32=None):
        self.events, self.on_pause = events, on_pause
        self.user32 = user32
        self._thread = None
        self._thread_id = None
        self._ready = Event()

    def start(self):
        self._thread = Thread(target=self._run, daemon=True, name="side-panel-hotkeys")
        self._thread.start()
        self._ready.wait(2)
        return self.events.snapshot()["registered"]

    def _api(self):
        if self.user32 is None:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            user32.RegisterHotKey.restype = wintypes.BOOL
            user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.UnregisterHotKey.restype = wintypes.BOOL
            user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user32.GetMessageW.restype = wintypes.BOOL
            user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.PostThreadMessageW.restype = wintypes.BOOL
            self.user32 = user32
        return self.user32

    def _run(self):
        user32 = self._api()
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        keys = list(KEY_ACTIONS)
        for index, key in enumerate(keys, 1):
            self.events.set_registered(key, bool(user32.RegisterHotKey(None, index, MOD_NOREPEAT,
                                                                       VIRTUAL_KEYS[key])))
        self._ready.set()
        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and 1 <= message.wParam <= len(keys):
                    self.handle(keys[message.wParam - 1])
        finally:
            for index in range(1, len(keys) + 1):
                user32.UnregisterHotKey(None, index)

    def handle(self, key):
        if key == "F8":
            try:
                outcome = self.on_pause()
                reason = outcome.get("reason") if isinstance(outcome, dict) else None
            except Exception as exc:
                reason = "pause_failed:" + type(exc).__name__
            return self.events.record(key, reason)
        return self.events.record(key)

    def stop(self, timeout=2.):
        if self._thread and self._thread_id:
            self._api().PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout)
        return not (self._thread and self._thread.is_alive())
