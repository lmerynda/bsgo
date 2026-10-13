"""Side-panel window beside the game; placement never focuses or touches the game."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
from threading import Event, Thread


PANEL_TITLE = "Wingman"
PANEL_WIDTH = 320
GAME_TITLE = "Battlestar Galactica Online"
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
BROWSERS = (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe")


def _overlap(a, b):
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    return max(0, width) * max(0, height)


def dock_placement(game, monitors, width=PANEL_WIDTH):
    """Return (x, y, width, height, mode) for rectangles given as (left, top, right, bottom).

    The panel takes the right edge of the screen the game is on, top to bottom of
    that screen's work area (never under the taskbar or off-screen). It overlaps
    the game only if the game reaches that edge. Without a game it waits on the
    primary screen.
    """
    if not monitors:
        raise ValueError("monitor_work_areas_required")
    home = monitors[0] if game is None else max(monitors, key=lambda area: _overlap(game, area))
    width = min(width, home[2] - home[0])
    x = home[2] - width
    mode = "waiting" if game is None else "over_game" if game[2] > x and _overlap(
        game, (x, home[1], home[2], home[3])) else "beside_right"
    return x, home[1], width, home[3] - home[1], mode


def find_browser(environ=os.environ):
    for root in (environ.get("ProgramFiles(x86)"), environ.get("ProgramFiles"), environ.get("LOCALAPPDATA")):
        for relative in BROWSERS:
            if root and Path(root, relative).is_file():
                return Path(root, relative)
    return None


class _Win32:
    def __init__(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32 = user32
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        self.monitor_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                                   ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, self.monitor_callback,
                                               wintypes.LPARAM]

    def scale(self, hwnd):
        dpi = self.user32.GetDpiForWindow(hwnd) if hwnd else 0
        return dpi / 96 if dpi else 1.

    def point_scale(self, x, y):
        """Display scaling of the screen containing physical point (x, y)."""
        try:
            monitor = self.user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
            dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
            if ctypes.windll.shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
                return dpi_x.value / 96
        except (AttributeError, OSError):
            pass
        return 1.

    def per_monitor_dpi(self):
        return self.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))

    def restore_dpi(self, previous):
        if previous:
            self.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))

    def find(self, title):
        return self.user32.FindWindowW(None, title) or None

    def rect(self, hwnd):
        if not hwnd or not self.user32.IsWindow(hwnd) or not self.user32.IsWindowVisible(hwnd) \
                or self.user32.IsIconic(hwnd):
            return None
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return rect.left, rect.top, rect.right, rect.bottom

    def monitors(self):
        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        areas = []
        def visit(monitor, dc, rect, data):
            info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
            if self.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                work = info.rcWork
                # Primary monitor first: the waiting position uses areas[0].
                entry = (work.left, work.top, work.right, work.bottom)
                areas.insert(0, entry) if info.dwFlags & 1 else areas.append(entry)
            return True
        self.user32.EnumDisplayMonitors(None, None, self.monitor_callback(visit), 0)
        return areas

    def place(self, hwnd, x, y, width, height):
        style = self.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if not style & WS_EX_NOACTIVATE:
            # Clicking the panel must not take keyboard focus from the game.
            self.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)
        return bool(self.user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, width, height,
                                             SWP_NOACTIVATE | SWP_SHOWWINDOW))


def open_panel(url, profile_directory, placement, *, browser=None, popen=subprocess.Popen, scale=1.):
    """Launch an app-mode browser window with its own profile; returns the process or None.

    `placement` is in physical pixels; the browser's window switches take scaled
    (DIP) units, so divide by the screen's scale or the window opens too large.
    """
    browser = browser or find_browser()
    if browser is None:
        return None
    x, y, width, height = (round(value / scale) for value in placement[:4])
    Path(profile_directory).mkdir(parents=True, exist_ok=True)
    return popen([str(browser), f"--app={url}", f"--user-data-dir={profile_directory}",
                  f"--window-position={x},{y}", f"--window-size={width},{height}",
                  "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class DockFollower:
    """Keeps the panel snapped beside the game window while both exist."""
    def __init__(self, *, interval=.5, win32=None, width=PANEL_WIDTH):
        self.interval, self.width = interval, width
        self.win32 = win32
        self._stop = Event()
        self._thread = None
        self.last = {"panel": False, "game": False, "mode": None, "rect": None}

    def tick(self):
        win32 = self.win32
        panel = win32.find(PANEL_TITLE)
        game_rect = win32.rect(win32.find(GAME_TITLE))
        monitors = win32.monitors()
        # Physical pixels: keep the panel's CSS width constant across display scaling.
        width = round(self.width * win32.scale(panel)) if panel else self.width
        placement = dock_placement(game_rect, monitors, width) if monitors else None
        if panel and placement and win32.rect(panel) != (placement[0], placement[1],
                placement[0] + placement[2], placement[1] + placement[3]):
            win32.place(panel, *placement[:4])
        self.last = {"panel": bool(panel), "game": game_rect is not None,
                     "mode": placement[4] if placement else None,
                     "rect": list(placement[:4]) if placement else None}
        return self.last

    def placement(self):
        """Physical-pixel placement for a new panel, plus that screen's scale."""
        win32 = self.win32 or _Win32()
        previous = win32.per_monitor_dpi()
        try:
            game_rect = win32.rect(win32.find(GAME_TITLE))
            monitors = win32.monitors()
            home = monitors[0] if game_rect is None else max(monitors, key=lambda a: _overlap(game_rect, a))
            scale = win32.point_scale(home[0] + 1, home[1] + 1)
            return dock_placement(game_rect, monitors, round(self.width * scale)), scale
        finally:
            win32.restore_dpi(previous)

    def start(self):
        if self._thread and self._thread.is_alive():
            return  # one follower per process, however often the panel is (re)opened
        def run():
            self.win32 = self.win32 or _Win32()
            self.win32.per_monitor_dpi()
            while not self._stop.wait(self.interval):
                try:
                    self.tick()
                except OSError:
                    pass
        self._thread = Thread(target=run, daemon=True, name="side-panel-dock")
        self._thread.start()

    def stop(self, timeout=2.):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
