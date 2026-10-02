"""An owned Win32 test surface that records received input messages.

No hooks, input injection, game APIs, or third-party GUI libraries are used.
Key/button state describes messages received by this window, not global hardware
state. Lifecycle WM_CLOSE and an output-directory ``receiver-exit`` sentinel are
supported for the driver that owns this process.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import re
import sys
import time


TARGET_RECT = (200, 150, 280, 230)
FIDUCIALS = ({"client": [15, 15], "rgb": [23, 211, 67]},
             {"client": [45, 15], "rgb": [239, 83, 151]})
IDLE_COLOR = (55, 120, 230)
MAXIMUM_COLOR = (245, 200, 55)
STATE_MARKERS = {"selected": {"client": [340, 190],
    "rgb": {"false": [110, 110, 110], "true": [50, 210, 110]}},
    "throttle": {"client": [420, 190],
    "rgb": {"idle": list(IDLE_COLOR), "maximum": list(MAXIMUM_COLOR)}}}
EVENT_LIMIT = 1024


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, allow_nan=False, indent=2), encoding="utf-8")
    # A concurrent Windows reader may briefly deny replacement. Preserve the
    # atomic old/new contract, with a short bounded retry rather than truncation.
    for attempt in range(6):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.005)


def client_point(lparam: int) -> tuple[int, int]:
    """GET_X/Y_LPARAM semantics, including negative coordinates."""
    return ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short((lparam >> 16) & 0xFFFF).value


def key_name(code: int) -> str:
    names = {0x21: "PageUp", 0x22: "PageDown", 0x10: "Shift", 0x11: "Control",
             0x12: "Alt", 0x20: "Space", 0x0D: "Enter", 0x1B: "Escape",
             0x09: "Tab", 0x08: "Backspace", 0x25: "Left", 0x26: "Up",
             0x27: "Right", 0x28: "Down"}
    if code in names:
        return names[code]
    if 0x30 <= code <= 0x39 or 0x41 <= code <= 0x5A:
        return chr(code)
    if 0x70 <= code <= 0x7B:
        return f"F{code - 0x6F}"
    return f"VK_{code:02X}"


class ReceiverJournal:
    """Bounded message log; visible state continues updating after log saturation."""

    def __init__(self, output: Path, token: str, *, limit: int = EVENT_LIMIT):
        if type(limit) is not int or limit < 2:
            raise ValueError("Event limit must reserve an event-limit record")
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.limit = limit
        self.stream = (self.output / "receiver-events.jsonl").open("w", encoding="utf-8", newline="\n")
        self.event_count = 0
        self.logged_count = 0
        self.limit_reached = False
        self.held_keys: set[int] = set()
        self.held_buttons: set[str] = set()
        self.selected = False
        self.throttle = "idle"
        self.focused = False
        self.shutdown = False
        self.shutdown_reason: str | None = None
        self.hwnd = 0
        self.persist()

    def state(self) -> dict:
        return {"pid": os.getpid(), "hwnd": self.hwnd, "token": self.token,
                "held_keys": [key_name(code) for code in sorted(self.held_keys)],
                "held_key_codes": sorted(self.held_keys),
                "held_buttons": sorted(self.held_buttons), "event_count": self.event_count,
                "logged_count": self.logged_count, "limit_reached": self.limit_reached,
                "selected": self.selected, "throttle": self.throttle,
                "color": list(MAXIMUM_COLOR if self.throttle == "maximum" else IDLE_COLOR),
                "focused": self.focused, "shutdown": self.shutdown,
                "shutdown_reason": self.shutdown_reason,
                "evidence": "messages_received_by_owned_window"}

    def persist(self) -> None:
        atomic_json(self.output / "receiver-state.json", self.state())

    def record(self, kind: str, **values) -> None:
        self.event_count += 1
        if self.logged_count < self.limit - 1:
            event = {"sequence": self.logged_count + 1, "at": time.monotonic(),
                     "pid": os.getpid(), "hwnd": self.hwnd, "token": self.token,
                     "kind": kind, "evidence": "received_window_message", **values}
            self.stream.write(json.dumps(event, allow_nan=False) + "\n")
            self.stream.flush()
            self.logged_count += 1
        elif not self.limit_reached:
            self.stream.write(json.dumps({"sequence": self.limit, "at": time.monotonic(),
                "pid": os.getpid(), "hwnd": self.hwnd, "token": self.token,
                "kind": "event_limit", "limit": self.limit,
                "reason": "Further messages update state but are not appended"}) + "\n")
            self.stream.flush()
            self.logged_count += 1
            self.limit_reached = True
        self.persist()

    def key(self, down: bool, code: int, *, repeat: bool = False, system: bool = False,
            extra_info: int = 0) -> None:
        if down:
            self.held_keys.add(code)
        else:
            self.held_keys.discard(code)
        effect = None
        if down and code == 0x21:
            self.throttle = "maximum"
            effect = "maximum_throttle_visible"
        elif down and code == 0x22:
            self.throttle = "idle"
            effect = "idle_throttle_visible"
        self.record("key_down" if down else "key_up", key=key_name(code), vk=code,
                    repeat=repeat, system=system, effect=effect, extra_info=extra_info)

    def mouse(self, down: bool, button: str, x: int, y: int, *, extra_info: int = 0) -> None:
        if down:
            self.held_buttons.add(button)
        else:
            self.held_buttons.discard(button)
        effect = None
        if down and button == "left" and TARGET_RECT[0] <= x < TARGET_RECT[2] and TARGET_RECT[1] <= y < TARGET_RECT[3]:
            self.selected = True
            effect = "target_selected_visible"
        self.record("mouse_down" if down else "mouse_up", button=button, x=x, y=y,
                    coordinate_space="client", effect=effect, extra_info=extra_info)

    def close(self, reason: str) -> None:
        if not self.shutdown:
            self.shutdown = True
            self.shutdown_reason = reason
            self.record("shutdown", reason=reason, held_keys=self.state()["held_keys"],
                        held_buttons=sorted(self.held_buttons))
        self.stream.close()


def run_receiver(output: Path, token: str, lifetime: float = 90.0, *, activate: bool = False) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Native receiver requires Windows")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", token):
        raise ValueError("Token must contain 1..80 ASCII letters, digits, underscores or hyphens")
    if not math.isfinite(lifetime) or not 0 < lifetime <= 600:
        raise ValueError("Lifetime must be finite and within 0..600 seconds")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    LRESULT, WPARAM, LPARAM = ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)

    class WNDCLASSEX(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                    ("hIconSm", wintypes.HICON)]

    class PAINTSTRUCT(ctypes.Structure):
        _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL), ("rcPaint", wintypes.RECT),
                    ("fRestore", wintypes.BOOL), ("fIncUpdate", wintypes.BOOL),
                    ("rgbReserved", wintypes.BYTE * 32)]

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("wParam", WPARAM), ("lParam", LPARAM), ("time", wintypes.DWORD),
                    ("pt", wintypes.POINT), ("lPrivate", wintypes.DWORD)]

    def signature(dll, name, args, result):
        function = getattr(dll, name)
        function.argtypes, function.restype = args, result
        return function

    try:
        signature(user32, "SetProcessDpiAwarenessContext", [wintypes.HANDLE], wintypes.BOOL)(ctypes.c_void_p(-4))
    except AttributeError:
        signature(user32, "SetProcessDPIAware", [], wintypes.BOOL)()
    signature(kernel32, "GetModuleHandleW", [wintypes.LPCWSTR], wintypes.HMODULE)
    signature(user32, "RegisterClassExW", [ctypes.POINTER(WNDCLASSEX)], wintypes.WORD)
    signature(user32, "UnregisterClassW", [wintypes.LPCWSTR, wintypes.HINSTANCE], wintypes.BOOL)
    signature(user32, "CreateWindowExW", [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID], wintypes.HWND)
    signature(user32, "DefWindowProcW", [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM], LRESULT)
    signature(user32, "DestroyWindow", [wintypes.HWND], wintypes.BOOL)
    signature(user32, "GetClientRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL)
    signature(user32, "ClientToScreen", [wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL)
    signature(user32, "AdjustWindowRectEx", [ctypes.POINTER(wintypes.RECT), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.BOOL)
    signature(user32, "GetSystemMetrics", [ctypes.c_int], ctypes.c_int)
    signature(user32, "GetMessageExtraInfo", [], ctypes.c_size_t)
    signature(user32, "SetWindowPos", [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL)
    signature(user32, "ShowWindow", [wintypes.HWND, ctypes.c_int], wintypes.BOOL)
    signature(user32, "SetForegroundWindow", [wintypes.HWND], wintypes.BOOL)
    signature(user32, "UpdateWindow", [wintypes.HWND], wintypes.BOOL)
    signature(user32, "InvalidateRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.BOOL], wintypes.BOOL)
    signature(user32, "SetTimer", [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, wintypes.LPVOID], ctypes.c_size_t)
    signature(user32, "KillTimer", [wintypes.HWND, ctypes.c_size_t], wintypes.BOOL)
    signature(user32, "PostQuitMessage", [ctypes.c_int], None)
    signature(user32, "GetMessageW", [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT], wintypes.BOOL)
    signature(user32, "TranslateMessage", [ctypes.POINTER(MSG)], wintypes.BOOL)
    signature(user32, "DispatchMessageW", [ctypes.POINTER(MSG)], LRESULT)
    signature(user32, "BeginPaint", [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)], wintypes.HDC)
    signature(user32, "EndPaint", [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)], wintypes.BOOL)
    signature(user32, "FillRect", [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH], ctypes.c_int)
    signature(gdi32, "CreateSolidBrush", [wintypes.DWORD], wintypes.HBRUSH)
    signature(gdi32, "DeleteObject", [wintypes.HANDLE], wintypes.BOOL)
    signature(gdi32, "SetTextColor", [wintypes.HDC, wintypes.DWORD], wintypes.DWORD)
    signature(gdi32, "SetBkMode", [wintypes.HDC, ctypes.c_int], ctypes.c_int)
    signature(gdi32, "TextOutW", [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int], wintypes.BOOL)
    journal = ReceiverJournal(output, token)
    title = f"BSGO native input test - {token}"
    class_name = f"BSGONativeReceiver_{token}_{os.getpid()}"
    instance = kernel32.GetModuleHandleW(None)
    deadline = time.monotonic() + lifetime
    callback_errors: list[str] = []
    reason = "owned_window_closed"
    hwnd = None
    last_command_id = None

    def fill(hdc, rectangle, color):
        brush = gdi32.CreateSolidBrush(color[0] | color[1] << 8 | color[2] << 16)
        try:
            user32.FillRect(hdc, ctypes.byref(wintypes.RECT(*rectangle)), brush)
        finally:
            gdi32.DeleteObject(brush)

    def text(hdc, x, y, value):
        gdi32.TextOutW(hdc, x, y, value, len(value))

    def paint(window):
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(window, ctypes.byref(ps))
        try:
            client = wintypes.RECT()
            user32.GetClientRect(window, ctypes.byref(client))
            fill(hdc, (client.left, client.top, client.right, client.bottom), (17, 24, 39))
            fill(hdc, (10, 10, 30, 30), tuple(FIDUCIALS[0]["rgb"]))
            fill(hdc, (40, 10, 60, 30), tuple(FIDUCIALS[1]["rgb"]))
            fill(hdc, TARGET_RECT, (250, 190, 60) if journal.selected else (58, 108, 186))
            fill(hdc, (320, 170, 360, 210), tuple(STATE_MARKERS["selected"]["rgb"][str(journal.selected).lower()]))
            fill(hdc, (400, 170, 440, 210), MAXIMUM_COLOR if journal.throttle == "maximum" else IDLE_COLOR)
            fill(hdc, (500, 150, 560, 210), MAXIMUM_COLOR if journal.throttle == "maximum" else IDLE_COLOR)
            gdi32.SetTextColor(hdc, 0xF6EFE8)
            gdi32.SetBkMode(hdc, 1)
            text(hdc, 80, 12, "Owned native input test: no game connected")
            text(hdc, 30, 60, f"Token: {token}")
            text(hdc, 180, 125, "Click target rectangle")
            text(hdc, 185, 245, "SELECTED" if journal.selected else "Not selected")
            text(hdc, 470, 125, "PageUp / PageDown")
            text(hdc, 475, 225, "MAXIMUM" if journal.throttle == "maximum" else "IDLE")
            text(hdc, 30, 310, f"Received messages: {journal.event_count}")
            text(hdc, 30, 340, "Held keys: " + ", ".join(journal.state()["held_keys"]))
            text(hdc, 30, 370, "Held buttons: " + ", ".join(sorted(journal.held_buttons)))
            text(hdc, 30, 430, "This window closes automatically; title-bar Close is supported.")
        finally:
            user32.EndPaint(window, ctypes.byref(ps))

    @WNDPROC
    def window_proc(window, message, wparam, lparam):
        nonlocal reason, last_command_id
        try:
            if message == 0x000F:  # WM_PAINT
                paint(window)
                return 0
            if message in (0x0100, 0x0101, 0x0104, 0x0105):
                journal.key(message in (0x0100, 0x0104), int(wparam),
                            repeat=message in (0x0100, 0x0104) and bool(lparam & (1 << 30)),
                            system=message in (0x0104, 0x0105),
                            extra_info=int(user32.GetMessageExtraInfo()))
                user32.InvalidateRect(window, None, False)
                # Preserve standard Alt+F4/title-bar lifecycle handling.
                return user32.DefWindowProcW(window, message, wparam, lparam) if message in (0x0104, 0x0105) else 0
            mouse_messages = {0x0201: (True, "left"), 0x0202: (False, "left"),
                              0x0204: (True, "right"), 0x0205: (False, "right"),
                              0x0207: (True, "middle"), 0x0208: (False, "middle")}
            if message in mouse_messages:
                down, button = mouse_messages[message]
                journal.mouse(down, button, *client_point(lparam), extra_info=int(user32.GetMessageExtraInfo()))
                user32.InvalidateRect(window, None, False)
                return 0
            if message in (0x0007, 0x0008):
                journal.focused = message == 0x0007
                journal.record("focus", focused=journal.focused)
            if message == 0x0113:  # WM_TIMER
                if (output / "receiver-exit").exists() or time.monotonic() >= deadline:
                    reason = "exit_sentinel" if (output / "receiver-exit").exists() else "lifetime_expired"
                    user32.DestroyWindow(window)
                    return 0
                command_path = output / "receiver-command.json"
                if command_path.exists():
                    try:
                        command = json.loads(command_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        command = {}
                    command_id = command.get("id")
                    if isinstance(command_id, (str, int)) and command_id != last_command_id:
                        last_command_id = command_id
                        accepted = command.get("command") == "resize"
                        if accepted:
                            client = wintypes.RECT()
                            user32.GetClientRect(window, ctypes.byref(client))
                            rectangle = wintypes.RECT(0, 0, client.right + 40, client.bottom)
                            user32.AdjustWindowRectEx(ctypes.byref(rectangle), style, False, 0)
                            accepted = bool(user32.SetWindowPos(window, None, 0, 0,
                                rectangle.right - rectangle.left, rectangle.bottom - rectangle.top, 0x16))
                            user32.InvalidateRect(window, None, False)
                        client = wintypes.RECT()
                        user32.GetClientRect(window, ctypes.byref(client))
                        atomic_json(output / "receiver-command-result.json", {"id": command_id,
                            "accepted": accepted, "command": command.get("command"),
                            "client_size": [client.right, client.bottom], "pid": os.getpid(), "hwnd": int(window)})
                return 0
            if message == 0x0010:  # WM_CLOSE, only lifecycle control
                reason = "owned_window_closed"
                user32.DestroyWindow(window)
                return 0
            if message == 0x0002:  # WM_DESTROY
                user32.KillTimer(window, 1)
                journal.close(reason)
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(window, message, wparam, lparam)
        except Exception as exc:
            if len(callback_errors) < 8:
                callback_errors.append(f"{type(exc).__name__}: {exc}")
            reason = "callback_failure"
            user32.PostQuitMessage(1)
            return 0

    wc = WNDCLASSEX(cbSize=ctypes.sizeof(WNDCLASSEX), lpfnWndProc=window_proc,
                    hInstance=instance, lpszClassName=class_name)
    if not user32.RegisterClassExW(ctypes.byref(wc)):
        journal.close("class_registration_failed")
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        style = 0x00C00000 | 0x00080000 | 0x00020000  # caption, system menu, minimize
        rectangle = wintypes.RECT(0, 0, 800, 500)
        if not user32.AdjustWindowRectEx(ctypes.byref(rectangle), style, False, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        width, height = rectangle.right - rectangle.left, rectangle.bottom - rectangle.top
        x = max(0, (user32.GetSystemMetrics(0) - width) // 2)
        y = max(0, (user32.GetSystemMetrics(1) - height) // 2)
        hwnd = user32.CreateWindowExW(0, class_name, title, style, x, y, width, height,
                                      None, None, instance, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        journal.hwnd = int(hwnd)
        user32.ShowWindow(hwnd, 5 if activate else 4)  # SW_SHOW / SW_SHOWNOACTIVATE
        foreground_request_accepted = bool(user32.SetForegroundWindow(hwnd)) if activate else None
        user32.UpdateWindow(hwnd)
        client, origin = wintypes.RECT(), wintypes.POINT(0, 0)
        if not user32.GetClientRect(hwnd, ctypes.byref(client)) or not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not user32.SetTimer(hwnd, 1, 100, None):
            raise ctypes.WinError(ctypes.get_last_error())
        journal.record("ready", title=title)
        atomic_json(output / "receiver-ready.json", {"hwnd": int(hwnd), "pid": os.getpid(),
            "token": token, "title": title, "client_size": [client.right, client.bottom],
            "screen_origin": [origin.x, origin.y], "coordinate_space": "client",
            "target_rect": list(TARGET_RECT), "fiducials": FIDUCIALS,
            "state_markers": STATE_MARKERS,
            "activation_requested": activate, "foreground_request_accepted": foreground_request_accepted,
            "dpi_awareness": "per-monitor-v2-requested", "lifetime_seconds": lifetime})
        message = MSG()
        while True:
            status = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if status == -1:
                raise ctypes.WinError(ctypes.get_last_error())
            if status == 0:
                break
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        if callback_errors:
            raise RuntimeError("; ".join(callback_errors))
    finally:
        if hwnd:
            user32.DestroyWindow(hwnd)
        if not journal.shutdown:
            journal.close(reason)
        user32.UnregisterClassW(class_name, instance)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--token", required=True)
    parser.add_argument("--lifetime", type=float, default=90.0)
    parser.add_argument("--activate", action="store_true",
                        help="Request ordinary foreground startup for this newly created test window only")
    args = parser.parse_args()
    run_receiver(args.output.resolve(), args.token, args.lifetime, activate=args.activate)


if __name__ == "__main__":
    main()
