"""Small in-memory execution mailbox; updates perform no I/O or control work."""
from datetime import datetime, timezone
from threading import Lock
import time

from .artifacts import plain
from .state import freeze


class ExecutionStatus:
    def __init__(self):
        self._lock = Lock()
        now, utc = time.monotonic(), datetime.now(timezone.utc).isoformat()
        self._value = {"schema_version": 1, "phase": "initializing",
            "processing": "initializing diagnostic components", "waiting_for": None,
            "expected_next_step": None, "phase_entered_monotonic": now,
            "phase_entered_utc": utc, "updated_monotonic": now, "updated_utc": utc,
            "reason": None, "first_blocker": None, "last_progress": None,
            "last_input_intent": None, "last_input_delivery": None,
            "last_confirmed_effect": None, "terminal": False, "warnings": [], "loop": None}

    def update(self, *, phase=None, processing=None, waiting_for=None,
               expected_next_step=None, reason=None, blocker=None, progress=None,
               confirmed_effect=None, terminal=False, warnings=None, visible_scan=None, loop=None):
        now, utc = time.monotonic(), datetime.now(timezone.utc).isoformat()
        effect = plain(freeze(confirmed_effect)) if confirmed_effect is not None else None
        with self._lock:
            if self._value["terminal"]:
                return
            if phase is not None and phase != self._value["phase"]:
                self._value.update(phase=phase, phase_entered_monotonic=now,
                                   phase_entered_utc=utc)
            self._value.update(processing=processing, waiting_for=waiting_for,
                expected_next_step=expected_next_step, reason=reason,
                updated_monotonic=now, updated_utc=utc, terminal=terminal)
            if blocker and self._value["first_blocker"] is None:
                self._value["first_blocker"] = blocker
            if progress is not None:
                self._value["last_progress"] = {"message": progress,
                    "at_monotonic": now, "at_utc": utc}
            if effect is not None:
                self._value["last_confirmed_effect"] = effect
            if warnings is not None:
                self._value["warnings"] = list(warnings)
            if visible_scan is not None:
                self._value['visible_scan'] = plain(freeze(visible_scan))
            # A repeating routine's current step; persists until replaced.
            if loop is not None:
                self._value["loop"] = plain(freeze(loop))

    def read(self, *, intent=None, delivery=None):
        with self._lock:
            result = plain(freeze(self._value))
        # Intent and insertion evidence are sampled independently. Neither
        # automatically becomes an image-confirmed game effect.
        result["last_input_intent"] = plain(intent)
        result["last_input_delivery"] = plain(delivery)
        return result
