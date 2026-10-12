"""Side panel services: hotkeys, the docked window, and the state the panel reads."""
from .dock_window import DockFollower, open_panel
from .hotkeys import GlobalHotkeys, HotkeyEvents


class SidePanel:
    def __init__(self, profile_directory, *, events=None, follower=None, hotkeys_factory=GlobalHotkeys,
                 opener=open_panel):
        self.profile_directory = profile_directory
        self.events = events or HotkeyEvents()
        self.follower = follower or DockFollower()
        self.hotkeys_factory, self.opener = hotkeys_factory, opener
        self.hotkeys = None
        self.process = None
        self.window_reason = "side_panel_not_started"

    def dock_snapshot(self):
        return {"hotkeys": self.events.snapshot(), "window": dict(self.follower.last),
                "window_reason": self.window_reason}

    def claim_hotkey(self, sequence):
        return self.events.claim(sequence)

    def start_hotkeys(self, on_pause):
        self.hotkeys = self.hotkeys_factory(self.events, on_pause)
        return self.hotkeys.start()

    def open(self, base_url):
        placement = self.follower.placement()
        self.process = self.opener(base_url.rstrip("/") + "/dock", self.profile_directory, placement)
        self.window_reason = None if self.process else "edge_or_chrome_not_found"
        self.follower.start()
        return self.process is not None

    def close(self):
        if self.hotkeys:
            self.hotkeys.stop()
        self.follower.stop()
        if self.process and self.process.poll() is None:
            self.process.terminate()
