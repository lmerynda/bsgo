"""Native-view provenance, latest-frame fencing and local HTTP boundaries."""
from io import BytesIO
import json
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bsgo_offline.easy_launcher import EasyLauncher
from bsgo_offline.live_frame import (LiveFrameStore, LiveFrameSource,
    LiveFrameDiagnostics, LiveImageError)
from bsgo_offline.native_capture import NativeFrame, WindowGeometry
from bsgo_offline.observer_ui import make_server, UnattachedManualActions
from bsgo_offline.state import Snapshot
from test_trace import scratch_directory


def native(sequence=1, at=10., color=(40, 90, 150)):
    geometry = WindowGeometry(17, 42, "Battlestar Galactica Online", (8, 6), (3840, 0))
    return NativeFrame(sequence, 7, "native-test", at, 8, 6,
        bytes(color) * 48, geometry, "native-window-graphics-capture",
        capture_started_at=at - .05, capture_completed_at=at)


class LiveFrameTests(unittest.TestCase):
    def test_replacement_beyond_archive_budget_keeps_exact_native_provenance(self):
        clock = [10.]
        frames = LiveFrameStore(clock=lambda: clock[0])
        self.assertEqual(frames.snapshot()["status"], "waiting")
        old = None
        for sequence in range(1, 90):
            clock[0] = float(sequence)
            frame = native(sequence, clock[0])
            frames.publish(frame)
            if sequence == 1:
                old = frames.snapshot()
        metadata = frames.snapshot()
        self.assertEqual(metadata["version"], 89)
        self.assertEqual(metadata["acquisition_sequence"], 89)
        self.assertEqual(metadata["acquired_at"], 89.)
        self.assertEqual(metadata["geometry"]["screen_origin"], [3840, 0])
        self.assertEqual(metadata["source_id"], "native-window-graphics-capture")
        self.assertEqual(metadata["frame_size"], [8, 6])
        data, mime = frames.image(metadata["identity"], metadata["version"])
        from PIL import Image
        self.assertEqual(Image.open(BytesIO(data)).size, (8, 6))
        self.assertEqual(mime, "image/jpeg")
        with self.assertRaises(LiveImageError) as replaced:
            frames.image(old["identity"], old["version"])
        self.assertEqual(replaced.exception.code, 409)

    def test_age_failure_and_new_session_never_relabel_last_pixels_current(self):
        clock = [10.]
        frames = LiveFrameStore(clock=lambda: clock[0])
        frames.publish(native())
        first = frames.snapshot()
        self.assertTrue(first["fresh"])
        clock[0] = 13.
        self.assertEqual(frames.snapshot()["status"], "last_known")
        clock[0] = 10.
        frames.disconnect("source_failure: unavailable")
        stale = frames.snapshot()
        self.assertFalse(stale["fresh"])
        self.assertEqual(stale["reason"], "source_failure: unavailable")
        self.assertEqual(stale["acquired_at"], 10.)
        self.assertIsNotNone(frames.image(first["identity"], first["version"])[0])
        new = LiveFrameStore(clock=lambda: clock[0])
        new.publish(native())
        self.assertNotEqual(new.snapshot()["identity"], first["identity"])
        with self.assertRaises(LiveImageError):
            new.image(first["identity"], first["version"])

    def test_source_publishes_before_recognition_and_failure_preserves_original_age(self):
        frames = LiveFrameStore(clock=lambda: 10.)
        sample = native()
        source = Mock()
        source.next_sample.side_effect = [sample, ValueError("window gone")]
        proxy = LiveFrameSource(source, frames)
        self.assertIs(proxy.next_sample(Event()), sample)
        self.assertIsNone(frames.snapshot()["observation_healthy"])
        snapshot = Snapshot(23, 7, 1, 10., 10.1, "native-test", (8, 6), {}, False, "glyph_missing")
        diagnostic = Mock()
        LiveFrameDiagnostics(diagnostic, frames).observation(snapshot, (), sample, {})
        self.assertEqual(frames.snapshot()["observation_sequence"], 23)
        self.assertFalse(frames.snapshot()["observation_healthy"])
        self.assertTrue(frames.snapshot()["fresh"])
        with self.assertRaises(ValueError):
            proxy.next_sample(Event())
        self.assertEqual(frames.snapshot()["status"], "last_known")
        self.assertEqual(frames.snapshot()["acquired_at"], 10.)
        proxy.close()
        source.close.assert_called_once()

    def test_encoding_does_not_hold_capture_lock_or_return_replaced_pixels(self):
        frames = LiveFrameStore(clock=lambda: 10.)
        frames.publish(native())
        metadata = frames.snapshot()
        from PIL import Image
        original = Image.Image.save
        def replacing_save(image, buffer, **options):
            frames.publish(native(2))
            return original(image, buffer, **options)
        with patch.object(Image.Image, "save", replacing_save):
            with self.assertRaises(LiveImageError) as caught:
                frames.image(metadata["identity"], metadata["version"])
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(frames.snapshot()["version"], 2)
        self.assertIsNone(frames._encoded)

    def test_encoder_busy_is_bounded_and_replay_samples_are_not_game_images(self):
        frames = LiveFrameStore(clock=lambda: 10.)
        frames.publish(Mock())
        self.assertFalse(frames.snapshot()["available"])
        frames.publish(native())
        metadata = frames.snapshot()
        frames._encoder.acquire()
        try:
            with self.assertRaises(LiveImageError) as caught:
                frames.image(metadata["identity"], metadata["version"])
            self.assertEqual(caught.exception.code, 503)
            # The acquisition lock remains free while encoding is busy.
            frames.publish(native(2))
            self.assertEqual(frames.snapshot()["version"], 2)
        finally:
            frames._encoder.release()

    def test_launcher_phase_fences_fresh_frame_during_disconnect_and_new_connection(self):
        with scratch_directory() as directory:
            frames = LiveFrameStore(clock=lambda: 10.)
            frames.publish(native())
            controller = Mock(live_frame_snapshot=frames.snapshot, live_frame_image=frames.image)
            launcher = EasyLauncher(directory, directory / "runs", pause_file=directory / "stop")
            launcher._controller, launcher._phase = controller, "connected"
            self.assertTrue(launcher.live_frame_snapshot()["fresh"])
            launcher._phase, launcher._reason = "blocked", "window gone"
            state = launcher.live_frame_snapshot()
            self.assertFalse(state["fresh"])
            self.assertEqual(state["status"], "last_known")
            self.assertEqual(state["reason"], "window gone")
            launcher._controller = None
            self.assertFalse(launcher.live_frame_snapshot()["available"])

    def test_http_metadata_image_and_local_only_query_fences(self):
        with scratch_directory() as directory:
            frames = LiveFrameStore(clock=lambda: 10.)
            frames.publish(native())
            actions = UnattachedManualActions()
            actions.live_frame_snapshot, actions.live_frame_image = frames.snapshot, frames.image
            server = make_server(directory, 0, action_adapter=actions)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base + "/api/live-frame", timeout=3) as response:
                    metadata = json.load(response)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                with urlopen(base + metadata["image_url"], timeout=3) as response:
                    self.assertEqual(response.headers["Content-Type"], "image/jpeg")
                    self.assertEqual(response.headers["Cross-Origin-Resource-Policy"], "same-origin")
                    self.assertTrue(response.read().startswith(b"\xff\xd8"))
                paths = [("/api/live-image", 400), ("/api/live-frame?run=archive", 400),
                    (metadata["image_url"] + "&path=secret", 400),
                    (metadata["image_url"] + "&version=2", 400)]
                frames.publish(native(2))
                paths.append((metadata["image_url"], 409))
                for path, code in paths:
                    with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                        urlopen(base + path, timeout=3)
                    self.assertEqual(caught.exception.code, code)
                for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Host": "untrusted.example"}):
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(Request(base + "/api/live-frame", headers=headers), timeout=3)
                    self.assertEqual(caught.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)


if __name__ == "__main__":
    unittest.main()
