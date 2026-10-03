"""All SendInput calls are fake; these tests never operate the desktop."""
from contextlib import contextmanager
import ctypes
from dataclasses import FrozenInstanceError, replace
import math
from threading import Thread
import time
import unittest

from bsgo_offline.input import RecordingInputBackend
from bsgo_offline.native_input import (
    INPUT, KEYBDINPUT, MOUSEINPUT, KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP,
    KEYEVENTF_SCANCODE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK,
    MAX_INPUT_SEQUENCE, NativeDispatchError, NativeInputDispatcher, ULONG_PTR,
    WindowStatus, input_tag,
)


class FakeNativeApi:
    def __init__(self):
        self.status = WindowStatus(100, 200, True, True, False, (640, 480), (20, 40), 100)
        self.at_point = (100, 200)
        self.desktop = (-1920, 0, 3840, 1080)
        self.physical_modifiers = ()
        self.sent = []
        self.sent_at = []
        self.send_counts = []
        self.inspections = 0
        self.on_inspect = None
        self.point_calls = []
        self.dpi_entries = 0

    @contextmanager
    def dpi_context(self):
        self.dpi_entries += 1
        yield

    def inspect(self, hwnd):
        self.inspections += 1
        if self.on_inspect:
            self.on_inspect()
        return self.status

    def window_at(self, point):
        self.point_calls.append(point)
        return self.at_point

    def virtual_desktop(self):
        return self.desktop

    def modifiers(self):
        return self.physical_modifiers

    def scan_code(self, key, hwnd):
        if key in ("PageUp", "PageDown"):
            return (0x49 if key == "PageUp" else 0x51), True
        return 0x1E, False

    def send(self, events):
        # Byte copies make these fake native records independent of ctypes objects.
        self.sent_at.append(time.monotonic())
        self.sent.append(tuple(INPUT.from_buffer_copy(bytes(event)) for event in events))
        if self.send_counts:
            count = self.send_counts.pop(0)
            if isinstance(count, Exception):
                raise count
            return count
        return len(events)

    def last_error(self):
        return 5


class NativeInputTests(unittest.TestCase):
    def test_pointer_settles_before_button_down(self):
        backend, api, dispatcher = self.setup_dispatcher(minimum_mouse_settle_seconds=.05,
                                                        minimum_mouse_hold_seconds=.05)
        backend.click_mouse(120, 100)
        self.assertEqual(self.deliver(backend, dispatcher).outcome, 'injected')
        self.assertEqual([len(batch) for batch in api.sent], [1, 1, 1])
        self.assertGreaterEqual(api.sent_at[1]-api.sent_at[0], .045)
        self.assertGreaterEqual(api.sent_at[2]-api.sent_at[1], .045)
        self.assertFalse(dispatcher.injected_held_buttons)

    def test_focus_loss_during_pointer_settle_never_sends_down(self):
        backend, api, dispatcher = self.setup_dispatcher(minimum_mouse_settle_seconds=.01)
        original_send = api.send
        def lose_focus(events):
            result = original_send(events)
            api.status = replace(api.status, foreground=999)
            return result
        api.send = lose_focus
        backend.click_mouse(120, 100)
        self.assertEqual(self.deliver(backend, dispatcher).outcome, 'failed')
        self.assertEqual([len(batch) for batch in api.sent], [1])
        self.assertFalse(dispatcher.injected_held_buttons)

    def test_canceled_pointer_settle_never_sends_down(self):
        backend, api, dispatcher = self.setup_dispatcher(minimum_mouse_settle_seconds=.01)
        original_send = api.send
        def cancel(events):
            result = original_send(events)
            backend.cancel(reason='test')
            return result
        api.send = cancel
        backend.click_mouse(120, 100)
        self.assertEqual(self.deliver(backend, dispatcher).outcome, 'partial')
        self.assertEqual(len(api.sent), 1)
        self.assertFalse(dispatcher.injected_held_buttons)

    def test_mouse_hold_splits_down_and_up_across_render_frames(self):
        backend, api, dispatcher = self.setup_dispatcher(minimum_mouse_hold_seconds=.05)
        backend.click_mouse(120, 100)
        result = self.deliver(backend, dispatcher)
        self.assertEqual(result.outcome, 'injected')
        self.assertEqual([len(batch) for batch in api.sent], [2, 1])
        self.assertGreaterEqual(api.sent_at[1]-api.sent_at[0], .045)
        self.assertFalse(dispatcher.injected_held_buttons)

    def test_failed_mouse_up_retains_release_for_emergency_cleanup(self):
        backend, api, dispatcher = self.setup_dispatcher(minimum_mouse_hold_seconds=.01)
        api.send_counts = [2, 0, 1]
        backend.click_mouse(120, 100)
        result = self.deliver(backend, dispatcher)
        self.assertEqual(result.outcome, 'partial')
        self.assertFalse(dispatcher.injected_held_buttons)
        self.assertEqual(len(api.sent), 3)
        self.assertEqual(api.sent[-1][0].mi.dwFlags, 4)

    def test_cancellation_interrupts_mouse_dwell_and_releases_down(self):
        backend, api, dispatcher = self.setup_dispatcher(minimum_mouse_hold_seconds=.5)
        backend.click_mouse(120, 100)
        def cancel_after_down():
            while not api.sent:
                time.sleep(.001)
            backend.cancel(reason='test_cancel')
        thread = Thread(target=cancel_after_down)
        thread.start()
        self.deliver(backend, dispatcher)
        thread.join(1)
        self.assertFalse(dispatcher.injected_held_buttons)
        self.assertLess(api.sent_at[1]-api.sent_at[0], .2)

    def test_mouse_hold_duration_is_bounded(self):
        for value in (-1, .6, True, float('nan')):
            with self.assertRaises(ValueError):
                self.setup_dispatcher(minimum_mouse_hold_seconds=value)
            with self.assertRaises(ValueError):
                self.setup_dispatcher(minimum_mouse_settle_seconds=value)

    def test_already_dequeued_press_up_releases_injected_down_after_cancel(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.press_key("PageUp")
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        up = backend.get_action(0)
        backend.cancel(reason="cancel_after_up_dequeue")
        api.status = replace(api.status, foreground=999)
        self.assertEqual(dispatcher.dispatch(up).outcome, "injected")
        self.assertFalse(dispatcher.injected_held_keys)
        self.assertEqual(len(api.sent), 2)
        self.assertTrue(api.sent[-1][0].ki.dwFlags & KEYEVENTF_KEYUP)

    def test_delivery_contract_is_rechecked_after_geometry_and_reports_rejection(self):
        backend, api, dispatcher = self.setup_dispatcher()
        allowed = [True]
        feedback = []
        backend.set_delivery_contract(lambda record: allowed[0],
                                      lambda record, outcome: feedback.append(outcome))
        def invalidate():
            allowed[0] = False
        api.on_inspect = invalidate
        backend.press_key("PageUp")
        result = self.deliver(backend, dispatcher)
        self.assertEqual(result.outcome, "skipped_dependency")
        self.assertFalse(api.sent)
        self.assertEqual(feedback, ["skipped_dependency"])

    def setup_dispatcher(self, **options):
        backend = RecordingInputBackend(session_id="native-test", layout_id="receiver",
                                        width=640, height=480)
        api = FakeNativeApi()
        dispatcher = NativeInputDispatcher(backend, 100, 200,
                                           expected_size=(640, 480), expected_origin=(20, 40),
                                           _api=api, **options)
        return backend, api, dispatcher

    def deliver(self, backend, dispatcher):
        action = backend.get_action(timeout=0)
        self.assertIsNotNone(action)
        return dispatcher.dispatch(action)

    def test_input_abi_is_full_union_with_pointer_sized_extra_info(self):
        if ctypes.sizeof(ULONG_PTR) == 8:
            self.assertEqual(ctypes.sizeof(INPUT), 40)
            self.assertEqual(ctypes.sizeof(MOUSEINPUT), 32)
            self.assertEqual(ctypes.sizeof(KEYBDINPUT), 24)
        else:
            self.assertEqual(ctypes.sizeof(INPUT), 28)
        self.assertEqual(INPUT.payload.offset, 8 if ctypes.sizeof(ULONG_PTR) == 8 else 4)

    def test_tag_is_32_bit_with_common_prefix_and_distinct_24_bit_sequences(self):
        self.assertEqual(input_tag(0), 0x42000000)
        self.assertEqual(input_tag(1), 0x42000001)
        self.assertEqual(input_tag(MAX_INPUT_SEQUENCE), 0x42FFFFFF)
        self.assertEqual(ctypes.c_uint32(input_tag(MAX_INPUT_SEQUENCE)).value,
                         input_tag(MAX_INPUT_SEQUENCE))
        for value in (-1, MAX_INPUT_SEQUENCE + 1, True, 1.0):
            with self.assertRaises(ValueError):
                input_tag(value)

    def test_out_of_range_native_sequence_rejected_without_injection(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.click_mouse(20, 20)
        action = backend.get_action(timeout=0)
        result = dispatcher.dispatch(replace(action, sequence=MAX_INPUT_SEQUENCE + 1))
        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.extra_info, 0)
        self.assertTrue(backend.canceled)
        self.assertEqual(api.sent, [])

    def test_extended_scan_code_press_and_release_have_matching_tags(self):
        backend, api, dispatcher = self.setup_dispatcher()
        down, up = backend.press_key("PageUp")
        first = self.deliver(backend, dispatcher)
        second = self.deliver(backend, dispatcher)
        self.assertEqual((first.inserted_count, second.inserted_count), (1, 1))
        self.assertEqual(first.outcome, "injected")
        events = [batch[0] for batch in api.sent]
        self.assertEqual([event.ki.wScan for event in events], [0x49, 0x49])
        self.assertEqual(events[0].ki.dwFlags, KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY)
        self.assertEqual(events[1].ki.dwFlags,
                         KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)
        self.assertEqual([event.ki.wVk for event in events], [0, 0])
        self.assertEqual([event.ki.dwExtraInfo for event in events],
                         [input_tag(down.sequence), input_tag(up.sequence)])
        self.assertEqual(dispatcher.injected_held_keys, ())
        self.assertEqual(first.evidence, "windows_input_stream_insertion")

    def test_virtual_key_mode_presses_and_releases_page_keys_with_matching_tags(self):
        backend, api, dispatcher = self.setup_dispatcher(keyboard_mode="virtual_key",
                                                       minimum_key_hold_seconds=.05)
        api.scan_code = lambda *_: self.fail("virtual key mode requested a scan code")
        up_down, up_up = backend.press_key("PageUp")
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        backend.request_cleanup_stop()
        down_down, down_up = tuple(r for r in backend.records() if r.key == "PageDown")
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        events = [batch[0] for batch in api.sent]
        self.assertEqual([event.ki.wVk for event in events], [0x21, 0x21, 0x22, 0x22])
        self.assertEqual([event.ki.wScan for event in events], [0, 0, 0, 0])
        self.assertEqual([event.ki.dwFlags for event in events],
                         [0, KEYEVENTF_KEYUP, 0, KEYEVENTF_KEYUP])
        self.assertEqual([event.ki.dwExtraInfo for event in events],
                         [input_tag(r.sequence) for r in (up_down, up_up, down_down, down_up)])
        self.assertGreaterEqual(api.sent_at[1] - api.sent_at[0], .045)
        self.assertGreaterEqual(api.sent_at[3] - api.sent_at[2], .045)
        self.assertEqual(dispatcher.injected_held_keys, ())

    def test_virtual_key_emergency_release_preserves_down_encoding(self):
        backend, api, dispatcher = self.setup_dispatcher(keyboard_mode="virtual_key")
        backend.key_down("PageUp")
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        # The stored encoding, not mutable dispatcher configuration, governs up.
        dispatcher.keyboard_mode = "scan_code"
        api.status = replace(api.status, foreground=101)
        backend.click_mouse(20, 20)
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "failed")
        release = api.sent[-1][0].ki
        self.assertEqual((release.wVk, release.wScan, release.dwFlags),
                         (0x21, 0, KEYEVENTF_KEYUP))
        self.assertEqual(dispatcher.injected_held_keys, ())

    def test_virtual_key_uncertain_down_attempts_matching_emergency_release(self):
        backend, api, dispatcher = self.setup_dispatcher(keyboard_mode="virtual_key")
        backend.press_key("PageDown")
        api.send_counts = [RuntimeError("uncertain insertion"), 1]
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "failed")
        self.assertEqual([batch[0].ki.wVk for batch in api.sent], [0x22, 0x22])
        self.assertEqual(api.sent[-1][0].ki.dwFlags, KEYEVENTF_KEYUP)
        self.assertEqual(dispatcher.injected_held_keys, ())

    def test_virtual_key_mode_validation(self):
        for value in (None, "", "vk", "SCAN_CODE", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.setup_dispatcher(keyboard_mode=value)

    def test_configured_key_dwell_starts_at_insertion_for_pageup_and_cleanup_pagedown(self):
        backend, api, dispatcher = self.setup_dispatcher(minimum_key_hold_seconds=.05)
        backend.press_key("PageUp")
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        self.assertGreaterEqual(api.sent_at[1] - api.sent_at[0], .045)
        backend.request_cleanup_stop()
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        self.assertGreaterEqual(api.sent_at[3] - api.sent_at[2], .045)
        self.assertEqual(dispatcher.injected_held_keys, ())

    def test_key_dwell_validation(self):
        for value in (-.01, .501, math.nan, math.inf, True, "0.05"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.setup_dispatcher(minimum_key_hold_seconds=value)
        _, _, dispatcher = self.setup_dispatcher(minimum_key_hold_seconds=.05)
        self.assertEqual(dispatcher.minimum_key_hold_seconds, .05)

    def test_stop_interrupts_dwell_and_releases_injected_key(self):
        backend, api, dispatcher = self.setup_dispatcher(minimum_key_hold_seconds=.5)
        dispatcher.start()
        backend.press_key("PageUp")
        deadline = time.monotonic() + 1
        while len(dispatcher.deliveries()) < 1 and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertEqual(dispatcher.deliveries()[0].outcome, "injected")
        started = time.monotonic()
        self.assertTrue(dispatcher.stop(timeout=1))
        self.assertLess(time.monotonic() - started, .45)
        self.assertEqual(dispatcher.injected_held_keys, ())
        self.assertTrue(api.sent[-1][0].ki.dwFlags & KEYEVENTF_KEYUP)

    def test_focus_change_during_dwell_rechecks_and_emergency_releases(self):
        backend, api, dispatcher = self.setup_dispatcher(minimum_key_hold_seconds=.2)
        backend.press_key("PageUp")
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        up = backend.get_action(timeout=0)
        deliveries = []
        thread = Thread(target=lambda: deliveries.append(dispatcher.dispatch(up)), daemon=True)
        thread.start()
        time.sleep(.04)
        api.status = replace(api.status, foreground=101)
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(deliveries[0].outcome, "failed")
        self.assertEqual(dispatcher.injected_held_keys, ())
        self.assertTrue(api.sent[-1][0].ki.dwFlags & KEYEVENTF_KEYUP)
        self.assertTrue(backend.canceled)

    def test_mouse_click_is_one_batch_and_maps_virtual_desktop(self):
        backend, api, dispatcher = self.setup_dispatcher()
        click = backend.click_mouse(120, 160, button="left")
        delivery = self.deliver(backend, dispatcher)
        self.assertEqual((delivery.requested_count, delivery.inserted_count), (3, 3))
        self.assertEqual(len(api.sent), 1)
        move, down, up = api.sent[0]
        self.assertEqual(api.point_calls, [(140, 200), (140, 200)])
        self.assertEqual(move.mi.dwFlags, 1 | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
        self.assertEqual((move.mi.dx, move.mi.dy),
                         (round((140 + 1920) * 65535 / 3839), round(200 * 65535 / 1079)))
        self.assertEqual((down.mi.dwFlags, up.mi.dwFlags), (2, 4))
        self.assertTrue(all(event.mi.dwExtraInfo == input_tag(click.sequence) for event in api.sent[0]))
        self.assertEqual(dispatcher.injected_held_buttons, ())

    def test_move_has_no_button_side_effect(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.move_mouse(1, 2)
        self.assertEqual(self.deliver(backend, dispatcher).inserted_count, 1)
        self.assertEqual(api.sent[0][0].mi.dwFlags, 1 | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)

    def test_window_guard_rejects_changed_focus_geometry_identity_or_visibility(self):
        changes = [{"foreground": 101}, {"client_size": (641, 480)},
                   {"screen_origin": (21, 40)}, {"pid": 201},
                   {"hwnd": 101}, {"alive": False}, {"minimized": True}, {"visible": False}]
        for change in changes:
            with self.subTest(change=change):
                backend, api, dispatcher = self.setup_dispatcher()
                backend.press_key("A")
                api.status = replace(api.status, **change)
                self.assertEqual(self.deliver(backend, dispatcher).outcome, "failed")
                self.assertEqual(api.sent, [])
                self.assertTrue(backend.canceled)
                self.assertIsNotNone(dispatcher.error)

    def test_covered_click_and_invalid_virtual_desktop_rejected(self):
        for field, value in (("at_point", (101, 200)), ("at_point", (100, 201)),
                             ("desktop", (0, 0, 100, 100))):
            backend, api, dispatcher = self.setup_dispatcher()
            backend.click_mouse(300, 200)
            setattr(api, field, value)
            self.assertEqual(self.deliver(backend, dispatcher).outcome, "failed")
            self.assertEqual(api.sent, [])

    def test_external_modifiers_rejected_without_changing_them(self):
        for modifier in ("Shift", "Control", "Alt", "Win"):
            backend, api, dispatcher = self.setup_dispatcher()
            backend.press_key("A")
            api.physical_modifiers = (modifier,)
            self.assertEqual(self.deliver(backend, dispatcher).outcome, "failed")
            self.assertEqual(api.sent, [])

    def test_own_injected_modifier_can_be_released(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.key_down("Shift")
        self.deliver(backend, dispatcher)
        api.physical_modifiers = ("Shift",)
        backend.key_up("Shift")
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        self.assertEqual(dispatcher.injected_held_keys, ())

    def test_cancel_after_dequeue_skips_ordinary_request(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.click_mouse(20, 20)
        action = backend.get_action(timeout=0)
        backend.cancel()
        self.assertEqual(dispatcher.dispatch(action).outcome, "skipped_canceled")
        self.assertEqual(api.sent, [])

    def test_cancel_during_geometry_inspection_skips_before_sendinput(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.press_key("A")
        api.on_inspect = backend.cancel
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "skipped_canceled")
        self.assertEqual(api.sent, [])

    def test_injected_hold_is_released_on_focus_loss_without_new_press(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.key_down("A")
        self.deliver(backend, dispatcher)
        self.assertEqual(dispatcher.injected_held_keys, ("A",))
        api.status = replace(api.status, foreground=101)
        backend.click_mouse(20, 20)
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "failed")
        self.assertEqual(len(api.sent), 2)
        self.assertTrue(api.sent[1][0].ki.dwFlags & KEYEVENTF_KEYUP)
        self.assertEqual(dispatcher.injected_held_keys, ())

    def test_cleanup_release_bypasses_focus_but_cleanup_stop_does_not(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.key_down("A")
        self.deliver(backend, dispatcher)
        api.status = replace(api.status, foreground=101)
        backend.cancel()
        backend.request_cleanup_stop()
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "failed")
        self.assertEqual(len(api.sent), 2)
        self.assertTrue(all(batch[0].ki.wScan != 0x51 for batch in api.sent))

    def test_cleanup_stop_delivered_only_with_valid_target(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.cancel()
        backend.request_cleanup_stop()
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
        self.assertEqual([batch[0].ki.wScan for batch in api.sent], [0x51, 0x51])

    def test_partial_mouse_insertion_releases_button_and_cancels_no_replay(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.click_mouse(120, 160)
        backend.press_key("PageUp")
        api.send_counts = [2, 1]
        delivery = self.deliver(backend, dispatcher)
        self.assertEqual((delivery.outcome, delivery.inserted_count, delivery.error_code), ("partial", 2, 5))
        self.assertTrue(backend.canceled)
        self.assertEqual(dispatcher.injected_held_buttons, ())
        self.assertEqual(api.sent[1][0].mi.dwFlags, 4)
        self.assertEqual(backend.get_action(timeout=0), None)
        self.assertEqual(dispatcher.deliveries()[1].kind, "emergency_release")

    def test_zero_keyboard_insertion_does_not_claim_hold_or_receipt(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.press_key("A")
        api.send_counts = [0]
        result = self.deliver(backend, dispatcher)
        self.assertEqual(result.inserted_count, 0)
        self.assertTrue(backend.canceled)
        self.assertEqual(dispatcher.injected_held_keys, ())
        self.assertIn("0/1", dispatcher.error)
        self.assertNotIn("UIPI", dispatcher.error)

    def test_native_call_exception_attempts_release_for_uncertain_offered_down(self):
        for request, release_flags in (("key", KEYEVENTF_KEYUP), ("mouse", 4)):
            backend, api, dispatcher = self.setup_dispatcher()
            if request == "key":
                backend.press_key("A")
            else:
                backend.click_mouse(20, 20)
            api.send_counts = [RuntimeError("native call failed after unknown insertion"), 1]
            self.assertEqual(self.deliver(backend, dispatcher).outcome, "failed")
            self.assertTrue(backend.canceled)
            self.assertEqual(len(api.sent), 2)
            release = api.sent[1][0]
            self.assertTrue((release.ki.dwFlags if request == "key" else release.mi.dwFlags)
                            & release_flags)
            self.assertEqual(dispatcher.injected_held_keys, ())
            self.assertEqual(dispatcher.injected_held_buttons, ())

    def test_dpi_context_failure_cancels_before_injection(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.press_key("A")
        @contextmanager
        def invalid_dpi():
            raise NativeDispatchError("DPI context unavailable")
            yield
        api.dpi_context = invalid_dpi
        self.assertEqual(self.deliver(backend, dispatcher).outcome, "failed")
        self.assertTrue(backend.canceled)
        self.assertEqual(api.sent, [])

    def test_failed_release_remains_visible_and_stop_returns_false(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.key_down("A")
        self.deliver(backend, dispatcher)
        api.send_counts = [0] * 10
        self.assertFalse(dispatcher.stop())
        self.assertEqual(dispatcher.injected_held_keys, ("A",))
        self.assertIn("incomplete", dispatcher.error)

    def test_stop_cancels_queue_releases_injected_holds_and_does_not_press_queued_stop(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.key_down("A")
        self.deliver(backend, dispatcher)
        backend.click_mouse(20, 20)
        backend.request_cleanup_stop()
        self.assertTrue(dispatcher.stop())
        self.assertTrue(backend.canceled)
        self.assertEqual(dispatcher.injected_held_keys, ())
        self.assertEqual(len(api.sent), 2)
        self.assertTrue(api.sent[1][0].ki.dwFlags & KEYEVENTF_KEYUP)

    def test_replay_and_stale_session_are_rejected(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.click_mouse(20, 20)
        action = backend.get_action(timeout=0)
        dispatcher.dispatch(action)
        self.assertEqual(dispatcher.dispatch(action).outcome, "rejected_replay")
        self.assertEqual(len(api.sent), 1)
        backend, api, dispatcher = self.setup_dispatcher()
        backend.click_mouse(20, 20)
        action = backend.get_action(timeout=0)
        backend.new_session("changed")
        self.assertEqual(dispatcher.dispatch(action).outcome, "failed")
        self.assertEqual(api.sent, [])

    def test_no_win_key_or_forged_cleanup_mouse_allowed(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.click_mouse(20, 20)
        action = backend.get_action(timeout=0)
        self.assertEqual(dispatcher.dispatch(replace(action, kind="key_down", key="Win")).outcome, "failed")
        self.assertEqual(api.sent, [])
        backend, api, dispatcher = self.setup_dispatcher()
        backend.click_mouse(20, 20)
        action = backend.get_action(timeout=0)
        self.assertEqual(dispatcher.dispatch(replace(action, metadata=(("cleanup", True),))).outcome, "failed")
        self.assertEqual(api.sent, [])

    def test_thread_consumes_bounded_queue_and_shutdown_is_joined(self):
        backend, api, dispatcher = self.setup_dispatcher()
        dispatcher.start()
        backend.press_key("PageUp")
        deadline = time.monotonic() + 1
        while len(dispatcher.deliveries()) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(len(dispatcher.deliveries()), 2)
        self.assertTrue(dispatcher.stop(timeout=1))
        self.assertFalse(dispatcher._thread.is_alive())
        with self.assertRaises(NativeDispatchError):
            dispatcher.start()

    def test_delivery_records_are_immutable_and_do_not_mutate_snapshot(self):
        backend, api, dispatcher = self.setup_dispatcher()
        backend.press_key("A")
        first = self.deliver(backend, dispatcher)
        snapshot = dispatcher.deliveries()
        self.deliver(backend, dispatcher)
        self.assertEqual(snapshot, (first,))
        with self.assertRaises(FrozenInstanceError):
            first.inserted_count = 999

    def test_recent_delivery_history_rolls_without_stopping_native_actions(self):
        backend, api, dispatcher = self.setup_dispatcher(history_capacity=3)
        first = None
        for _ in range(12):
            backend.press_key("A")
            for _ in range(2):
                action = backend.get_action(timeout=0)
                first = action if first is None else first
                self.assertEqual(dispatcher.dispatch(action).outcome, "injected")
        self.assertFalse(backend.canceled)
        self.assertIsNone(dispatcher.error)
        self.assertEqual(len(api.sent), 24)
        self.assertEqual([row.request_sequence for row in dispatcher.deliveries()], [22, 23, 24])
        stats = dispatcher.delivery_stats()
        self.assertEqual((stats["total"], stats["retained"], stats["evicted"], stats["logging_gaps"]),
                         (24, 3, 21, 0))
        self.assertEqual(stats["last_processed_sequence"], 24)
        # Replay protection cannot depend on retained telemetry or an unbounded
        # set of every prior request ID. The evicted first ID remains rejected.
        self.assertEqual(dispatcher.dispatch(first).outcome, "rejected_replay")
        self.assertEqual(len(api.sent), 24)
        self.assertTrue(backend.canceled)
        self.assertFalse(hasattr(dispatcher, "_seen"))

    def test_nonblocking_delivery_journal_preserves_all_events_after_eviction(self):
        class Journal:
            def __init__(self):
                self.events = []

            def submit(self, event):
                self.events.append(event)
                return True

        journal = Journal()
        backend, api, dispatcher = self.setup_dispatcher(history_capacity=2, journal=journal)
        for _ in range(5):
            backend.press_key("PageUp")
            self.deliver(backend, dispatcher)
            self.deliver(backend, dispatcher)
        self.assertEqual(len(dispatcher.deliveries()), 2)
        self.assertEqual(len(journal.events), 10)
        self.assertTrue(all(event["event"] == "native_delivery" for event in journal.events))
        self.assertEqual([event["record"]["request_sequence"] for event in journal.events], list(range(1, 11)))
        self.assertEqual(dispatcher.delivery_stats()["logging_gaps"], 0)

    def test_delivery_logging_failure_never_blocks_hold_release_or_cleanup_stop(self):
        for mode in ("full", "broken"):
            class Journal:
                def submit(self, event):
                    if mode == "broken":
                        raise OSError("journal unavailable")
                    return False

            backend, api, dispatcher = self.setup_dispatcher(history_capacity=1, journal=Journal())
            backend.key_down("A")
            self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
            self.assertIsNone(dispatcher.error)
            self.assertFalse(backend.canceled)
            backend.cancel()
            backend.request_cleanup_stop()
            for _ in range(3):
                self.assertEqual(self.deliver(backend, dispatcher).outcome, "injected")
            self.assertEqual(dispatcher.injected_held_keys, ())
            self.assertIsNone(dispatcher.error)
            stats = dispatcher.delivery_stats()
            self.assertEqual((stats["total"], stats["retained"], stats["evicted"], stats["logging_gaps"]),
                             (4, 1, 3, 4))

    def test_out_of_order_forwarded_sequence_rejected_without_duplicate_input(self):
        backend, api, dispatcher = self.setup_dispatcher(history_capacity=1)
        backend.click_mouse(20, 20)
        action = backend.get_action(timeout=0)
        self.assertEqual(dispatcher.dispatch(replace(action, sequence=10)).outcome, "injected")
        self.assertEqual(dispatcher.dispatch(replace(action, sequence=9)).outcome, "rejected_replay")
        self.assertEqual(len(api.sent), 1)

    def test_history_capacity_validation_and_default(self):
        backend, api, dispatcher = self.setup_dispatcher()
        self.assertEqual(dispatcher.delivery_stats()["history_capacity"], backend.record_capacity)
        for capacity in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                self.setup_dispatcher(history_capacity=capacity)

    def test_constructor_rejects_coordinate_dimension_mismatch(self):
        backend, api, dispatcher = self.setup_dispatcher()
        with self.assertRaises(ValueError):
            NativeInputDispatcher(backend, 100, 200,
                                  expected_size=(640, 481), expected_origin=(20, 40), _api=api)


if __name__ == "__main__":
    unittest.main()
