"""Isolated preview HTTP boundaries and live image viewer race tests."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import shutil
import subprocess
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bsgo_offline import cockpit_preview
from bsgo_offline.observer_ui import HTML as ORIGINAL_HTML


@contextmanager
def serving(server):
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


class CockpitPreviewTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.reply = (200, b'{"available":false,"status":"waiting"}', "application/json", True)
        owner = self

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                owner.requests.append((self.command, self.path, self.headers.get("Host")))
                code, body, mime, include_length = owner.reply
                self.send_response(code)
                self.send_header("Content-Type", mime)
                if include_length:
                    self.send_header("Content-Length", str(len(body)))
                if 300 <= code < 400:
                    self.send_header("Location", "http://untrusted.example/")
                self.end_headers()
                self.wfile.write(body)

        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)

    def tearDown(self):
        self.upstream.server_close()

    @contextmanager
    def preview(self):
        with serving(self.upstream):
            server = cockpit_preview.make_server(0, upstream_port=self.upstream.server_port)
            with serving(server) as base:
                yield base

    def request(self, base, path, *, method="GET", headers=None):
        request = Request(base + path, headers=headers or {}, method=method,
                          data=b'{}' if method in {"POST", "PUT", "PATCH"} else None)
        try:
            with urlopen(request, timeout=4) as response:
                return response.status, response.headers, response.read()
        except HTTPError as response:
            return response.code, response.headers, response.read()

    def test_page_is_separate_preserves_controls_and_only_get_reads_upstream(self):
        source = ORIGINAL_HTML.read_text(encoding="utf-8")
        page = cockpit_preview.HTML.read_text(encoding="utf-8")
        original_ids = set(re.findall(r'id="([^"]+)"', source))
        candidate_ids = re.findall(r'id="([^"]+)"', page)
        # This preserved design preview predates the production mode selector;
        # its shared action/assessment controls still match the live app.
        shared_controls = {"powerbutton", "scanonce", "autoscan", "approach",
            "startbutton", "cancelbutton", "clearcorridor", "alignedheading",
            "safestopped", "flightpoint", "scanselectedsafe", "autoscansafe",
            "diagnostics", "history", "settings", "startupretry", "startupfocus"}
        self.assertLessEqual(shared_controls, original_ids)
        self.assertLessEqual(shared_controls, set(candidate_ids))
        self.assertEqual(len(candidate_ids), len(set(candidate_ids)))
        self.assertIn("DESIGN PREVIEW · CONTROLS ISOLATED", page)
        self.assertIn("Controls are isolated from the running mining session", page)
        with self.preview() as base:
            code, headers, html = self.request(base, "/")
            self.assertEqual(code, 200)
            self.assertIn(b"MINING COCKPIT", html)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(self.requests, [])
            code, _, body = self.request(base, "/api/live-frame")
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body)["status"], "waiting")
            self.assertEqual(self.requests, [("GET", "/api/live-frame", f"127.0.0.1:{self.upstream.server_port}")])

    def test_all_write_methods_are_rejected_without_upstream_request(self):
        with self.preview() as base:
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                for path in ("/api/startup", "/api/manual-actions", "/"):
                    with self.subTest(method=method, path=path):
                        code, _, body = self.request(base, path, method=method)
                        self.assertEqual(code, 403)
                        self.assertFalse(json.loads(body)["accepted"])
            self.assertEqual(self.requests, [])

    def test_cross_site_hosts_origins_and_unknown_routes_never_proxy(self):
        with self.preview() as base:
            for headers in ({"Host": "untrusted.example"}, {"Host": "localhost"},
                            {"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://untrusted.example"}):
                with self.subTest(headers=headers):
                    self.assertEqual(self.request(base, "/api/live-frame", headers=headers)[0], 403)
            for path in ("/secret.png", "/api/unknown", "/design-capture.png", "/?recorded=1"):
                with self.subTest(path=path):
                    self.assertIn(self.request(base, path)[0], (400, 404))
            self.assertEqual(self.requests, [])

    def test_query_validation_and_live_image_replacement_status(self):
        with self.preview() as base:
            for path in ("/api/live-frame?run=bad", "/api/live-image?identity=bad&version=1",
                         "/api/live-image?identity=" + "a"*32 + "&version=0",
                         "/api/status?run=..%2Fprivate", "/api/image?run=demo&path=..%2Fsecret.png",
                         "/api/live-frame?a", "/api/status?run=demo&run=other"):
                with self.subTest(path=path):
                    self.assertEqual(self.request(base, path)[0], 400)
            self.assertEqual(self.request(base, "/api/live-frame?" + "x"*2100)[0], 414)
            self.assertEqual(self.requests, [])
            self.reply = (409, b'{"error":"live_image_replaced"}', "application/json", True)
            route = "/api/live-image?identity=" + "a"*32 + "&version=1"
            code, _, body = self.request(base, route)
            self.assertEqual(code, 409)
            self.assertEqual(json.loads(body)["error"], "live_image_replaced")
            self.reply = (200, b"jpeg-fixture", "image/jpeg", True)
            code, headers, body = self.request(base, route)
            self.assertEqual((code, headers["Content-Type"], body), (200, "image/jpeg", b"jpeg-fixture"))

    def test_response_bounds_with_and_without_content_length_and_redirects(self):
        with patch.object(cockpit_preview, "MAX_JSON_BYTES", 32), self.preview() as base:
            for length in (True, False):
                self.reply = (200, b"x"*33, "application/json", length)
                code, _, body = self.request(base, "/api/live-frame")
                self.assertEqual(code, 502)
                self.assertEqual(json.loads(body)["error"], "upstream_response_exceeds_limit")
            self.reply = (302, b"redirect", "text/html", True)
            self.assertEqual(self.request(base, "/api/live-frame")[0], 502)
            self.reply = (200, b"<html>wrong resource</html>", "text/html", True)
            self.assertEqual(self.request(base, "/api/live-frame")[0], 502)

    @unittest.skipUnless(shutil.which("node"), "Node is required for actual viewer script checks")
    def test_real_inline_viewer_rejects_races_and_tracks_freshness(self):
        from pathlib import Path
        check = Path(__file__).with_name("cockpit_viewer_checks.js")
        result = subprocess.run([shutil.which("node"), str(check), str(cockpit_preview.HTML)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Viewer checks passed", result.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node is required for actual telemetry script checks")
    def test_preview_uses_same_telemetry_and_expected_target_warning_rules(self):
        # Replay the production display contract against the separate preview page.
        # This includes healthy supplemental readings, stale/wrong-image rejection,
        # malformed energy ratios, and expected missing fields on non-asteroids.
        from test_ship_telemetry_ui import ShipTelemetryUiTests
        check = ShipTelemetryUiTests("test_native_display_fallback_freshness_and_numeric_rejection")
        # The archived preview retains its original display-only issue filter.
        check.expected_issue_count = 0
        with patch("test_ship_telemetry_ui.HTML", cockpit_preview.HTML):
            check.test_native_display_fallback_freshness_and_numeric_rejection()
