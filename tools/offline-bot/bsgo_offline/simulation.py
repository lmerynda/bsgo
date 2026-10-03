"""A deliberately synthetic receiver. No OS input or game integration."""
from __future__ import annotations
from dataclasses import dataclass
from collections import deque
import math, threading, time
from types import MappingProxyType
from .state import FieldValue
from .recognition import RecognitionResult

class SimulationClock:
    def __init__(self, speedup=20.0):
        if not math.isfinite(speedup) or speedup <= 0:
            raise ValueError('speedup must be finite and positive')
        self.speedup = speedup
        self.origin = time.monotonic()

    def __call__(self):
        return (time.monotonic() - self.origin) * self.speedup

@dataclass(frozen=True)
class SyntheticSample:
    acquisition_sequence: int
    session_epoch: int
    layout: str
    acquired_at: float
    values: object
    source_id: str = 'synthetic-receiver'

class VirtualReceiver:
    """Consume recorded requests and expose resulting synthetic observations.

    Acceleration and deceleration are invented test parameters, not BSGO physics.
    The target hitbox represents frame coordinates in a 2560x1440 fake surface.
    """
    def __init__(self, backend, clock, *, distance=1400.0, max_speed=44.0,
                 acceleration=12.0, deceleration=8.0, ignore_brake=False, journal=None,
                 history_capacity=64000):
        if any(not math.isfinite(v) or v <= 0 for v in (distance,max_speed,acceleration,deceleration)):
            raise ValueError('Synthetic distance and physics parameters must be finite and positive')
        if type(history_capacity) is not int or history_capacity <= 0:
            raise ValueError('Receiver history capacity must be a positive integer')
        self.backend, self.clock = backend, clock
        self.distance, self.speed, self.throttle = distance, 0.0, 0
        self.max_speed, self.acceleration, self.deceleration = max_speed, acceleration, deceleration
        self.ignore_brake = ignore_brake
        self.selected = False
        self.session_epoch = 1
        self.layout = 'synthetic-original'
        self.identity = 'synthetic-asteroid:1'
        self.portrait = (2,) * 144
        self.last = clock()
        self.effects = deque(maxlen=history_capacity)
        self.effects_total = 0
        self.effects_evicted = 0
        self.journal_dropped = 0
        self.journal = journal
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _advance(self, now):
        dt = max(0.0, now-self.last)
        old_speed = self.speed
        rate = self.acceleration if self.throttle else -self.deceleration
        endpoint = self.max_speed if self.throttle else 0.0
        change_time = abs(endpoint-old_speed)/abs(rate)
        changing = min(dt, change_time)
        new_speed = min(self.max_speed, max(0.0, old_speed+rate*changing))
        travel = (old_speed+new_speed)*.5*changing + new_speed*(dt-changing)
        self.distance = max(0.0, self.distance-travel)
        self.speed, self.last = new_speed, now

    def _receive(self):
        while not self._stop.is_set():
            action = self.backend.get_action(timeout=.02)
            if action is None:
                continue
            with self._lock:
                now = self.clock()
                self._advance(now)
                effect = 'no_state_change'
                if not self.backend.delivery_allowed(action):
                    effect = 'cancelled_before_effect'
                elif action.kind == 'mouse_click':
                    if action.button == 'left' and 1160 <= action.x <= 1240 and 560 <= action.y <= 640:
                        self.selected = True
                        effect = 'target_selected'
                    else:
                        effect = 'missed_target'
                elif action.kind == 'key_down' and action.key == 'PageUp':
                    self.throttle = 1
                    effect = 'maximum_throttle_requested'
                elif action.kind == 'key_down' and action.key == 'PageDown':
                    if self.ignore_brake:
                        effect = 'brake_ignored_by_receiver'
                    else:
                        self.throttle = 0
                        effect = 'idle_throttle_requested'
                else:
                    effect = self._extra_action(action, now)
                event = {'action_sequence': action.sequence, 'observed_at': now,
                         'effect': effect, 'speed_at_effect': self.speed}
                self.effects_total += 1
                if len(self.effects) == self.effects.maxlen:
                    self.effects_evicted += 1
                self.effects.append(event)
            self.backend.report_delivery(action, 'rejected' if effect == 'cancelled_before_effect' else 'received')
            # The optional sink promises nonblocking submit. Keep it outside the
            # receiver state lock so capture never waits on telemetry submission.
            if self.journal is not None:
                try:
                    submitted = self.journal.submit({'event': 'receiver_effect', 'record': event})
                except Exception:
                    submitted = False
                if not submitted:
                    with self._lock:
                        self.journal_dropped += 1

    def start(self):
        self._thread = threading.Thread(target=self._receive, name='offline-input-receiver')
        self._thread.start()

    def _extra_action(self, action, now):
        return 'no_state_change'

    def stop(self, timeout=2.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    def sample(self):
        with self._lock:
            now = self.clock()
            self._advance(now)
            values = {'distance': self.distance if self.selected else None,
                      'speed': self.speed, 'asteroid_selected': self.selected,
                      'target_identity': self.identity if self.selected else None,
                      'target_portrait': self.portrait if self.selected else None}
            return now, self.session_epoch, self.layout, MappingProxyType(values)

    def observed_effects(self):
        with self._lock:
            return tuple(dict(event) for event in self.effects)

    def telemetry_stats(self):
        with self._lock:
            return {'effects_total': self.effects_total, 'effects_retained': len(self.effects),
                    'effects_evicted': self.effects_evicted, 'journal_dropped': self.journal_dropped}

    def reconnect(self):
        """Inject a new game session while keeping this fake input surface owned."""
        with self._lock:
            self.session_epoch += 1
            self.selected = False

class SyntheticSource:
    def __init__(self, receiver, interval=.005, *, stall_after=None, fault_after=None):
        if not math.isfinite(interval) or interval<=0:
            raise ValueError('Synthetic source interval must be finite and positive')
        self.receiver, self.interval = receiver, interval
        self.sequence = 0
        self.stall_after, self.fault_after = stall_after, fault_after

    def next_sample(self, stop_event):
        if self.stall_after is not None and self.sequence >= self.stall_after:
            stop_event.wait()
            return None
        if stop_event.wait(self.interval):
            return None
        if self.fault_after is not None and self.sequence >= self.fault_after:
            raise RuntimeError('Injected synthetic capture failure')
        self.sequence += 1
        now, epoch, layout, values = self.receiver.sample()
        return SyntheticSample(self.sequence, epoch, layout, now, values)

def recognize_synthetic(sample):
    fields = {name: FieldValue(value=value, observed_at=sample.acquired_at,
                               valid=value is not None, reason=None if value is not None else 'not_selected')
              for name, value in sample.values.items()}
    return RecognitionResult(frame_size=(2560, 1440), fields=fields)
