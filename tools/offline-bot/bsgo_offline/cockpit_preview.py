"""Isolated desktop cockpit preview; reads an existing local launcher only."""
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import parse_qs, urlsplit


HTML = Path(__file__).with_name("cockpit_preview.html")
MAX_JSON_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_TARGET_LENGTH = 2048
_PLAIN_APIS = {
    "/api/startup", "/api/control-mode", "/api/manual-actions", "/api/live-frame",
    "/api/runs",
}
_QUERIED_APIS = {"/api/live-image", "/api/status", "/api/image", "/api/sidecar"}


def _valid_run(value):
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", value))


def _valid_query(path, query):
    if any(len(values) != 1 for values in query.values()):
        return False
    if path in _PLAIN_APIS or path == "/":
        return not query
    if path == "/api/live-image":
        return (set(query) == {"identity", "version"}
                and bool(re.fullmatch(r"[a-f0-9]{32}", query["identity"][0]))
                and bool(re.fullmatch(r"[1-9][0-9]{0,15}", query["version"][0])))
    if path == "/api/status":
        return not query or set(query) == {"run"} and _valid_run(query["run"][0])
    if path in {"/api/image", "/api/sidecar"}:
        if set(query) != {"run", "path"} or not _valid_run(query["run"][0]):
            return False
        image_path = query["path"][0]
        parsed = PurePosixPath(image_path)
        return (bool(re.fullmatch(r"[A-Za-z0-9_./-]{1,256}", image_path))
                and not parsed.is_absolute() and ".." not in parsed.parts
                and parsed.suffix == (".png" if path == "/api/image" else ".json"))
    return False


class _PreviewServer(ThreadingHTTPServer):
    daemon_threads = True


def make_server(port=8874, *, upstream_port=8765):
    """Bind loopback only; the fixed upstream host cannot come from a request."""
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("invalid_preview_port")
    if (not isinstance(upstream_port, int) or isinstance(upstream_port, bool)
            or not 1 <= upstream_port <= 65535 or port == upstream_port):
        raise ValueError("invalid_upstream_port")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _send(self, code, body, mime="application/json; charset=utf-8"):
            self.send_response(code)
            for name, value in {
                "Content-Type": mime, "Content-Length": str(len(body)),
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "same-origin", "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; img-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'",
            }.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, **payload):
            self._send(code, json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))

        def _local_request(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if (self.headers.get_all("Host") != [f"127.0.0.1:{self.server.server_port}"]
                    or self.headers.get("Sec-Fetch-Site") == "cross-site"
                    or self.headers.get("Origin") not in (None, origin)):
                self._json(403, error="invalid_local_preview_request")
                return False
            return True

        def do_GET(self):
            if not self._local_request():
                return
            if len(self.path) > MAX_TARGET_LENGTH:
                self._json(414, error="preview_request_target_too_long")
                return
            try:
                request = urlsplit(self.path)
                query = parse_qs(request.query, keep_blank_values=True, strict_parsing=True,
                                 max_num_fields=8)
            except ValueError:
                self._json(400, error="invalid_preview_query")
                return
            if (request.scheme or request.netloc or request.fragment
                    or request.path not in _PLAIN_APIS | _QUERIED_APIS | {"/"}):
                self._json(404, error="unknown_preview_route")
                return
            if not _valid_query(request.path, query):
                self._json(400, error="invalid_preview_query")
                return
            if request.path == "/":
                self._send(200, HTML.read_bytes(), "text/html; charset=utf-8")
                return
            limit = MAX_IMAGE_BYTES if request.path in {"/api/image", "/api/live-image"} else MAX_JSON_BYTES
            connection = HTTPConnection("127.0.0.1", upstream_port, timeout=3)
            try:
                connection.request("GET", self.path,
                                   headers={"Host": f"127.0.0.1:{upstream_port}"})
                response = connection.getresponse()
                if 300 <= response.status < 400:
                    self._json(502, error="upstream_redirect_rejected")
                    return
                mime = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
                allowed = {"application/json"}
                if request.path in {"/api/image", "/api/live-image"}:
                    allowed |= {"image/png", "image/jpeg"}
                if mime not in allowed:
                    self._json(502, error="unexpected_upstream_content_type")
                    return
                length = response.getheader("Content-Length")
                if length is not None and (not length.isdecimal() or int(length) > limit):
                    self._json(502, error="upstream_response_exceeds_limit")
                    return
                body = response.read(limit + 1)
                if len(body) > limit:
                    self._json(502, error="upstream_response_exceeds_limit")
                    return
                self._send(response.status, body, response.getheader("Content-Type"))
            except (OSError, ValueError, HTTPException) as error:
                self._json(503, error="preview_upstream_unavailable", cause=type(error).__name__)
            finally:
                connection.close()

        def _reject_write(self):
            self.close_connection = True
            if self._local_request():
                self._json(403, accepted=False, reason="design_preview_controls_are_isolated")

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _reject_write

    return _PreviewServer(("127.0.0.1", port), Handler)
