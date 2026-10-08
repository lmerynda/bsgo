"""Serve Astra's isolated design with GET-only reads from the mining dashboard."""
from pathlib import Path
import argparse
import mimetypes
import sys
from urllib.parse import urlsplit

WORKSPACE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WORKSPACE / "tools" / "offline-bot"))
from bsgo_offline import cockpit_preview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8875)
    parser.add_argument("--upstream-port", type=int, default=8765)
    parser.add_argument("--html", default="prototype.html")
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    page = (directory / args.html).resolve()
    if page.parent != directory or not page.is_file():
        parser.error("--html must name an existing HTML file in this design directory")
    cockpit_preview.HTML = page
    cockpit_preview._PLAIN_APIS.update({"/api/observation", "/api/observation/schema"})
    server = cockpit_preview.make_server(args.port, upstream_port=args.upstream_port)
    original = server.RequestHandlerClass

    class DesignHandler(original):
        def do_GET(self):
            request = urlsplit(self.path)
            if request.path.startswith("/assets/"):
                if not self._local_request():
                    return
                filename = request.path.removeprefix("/assets/")
                asset = (directory / filename).resolve()
                if (request.query or request.fragment or asset.parent != directory
                        or asset.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".html"}
                        or not asset.is_file()):
                    self._json(404, error="unknown_design_asset")
                    return
                self._send(200, asset.read_bytes(), mimetypes.guess_type(asset.name)[0]
                           or "application/octet-stream")
                return
            super().do_GET()

    server.RequestHandlerClass = DesignHandler
    print(f"Astra cockpit design: http://127.0.0.1:{server.server_port}/", flush=True)
    print("Read-only live data; prototype interactions cannot control the game.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
