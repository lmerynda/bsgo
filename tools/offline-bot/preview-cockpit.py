"""Open an isolated, read-only desktop cockpit design beside the mining launcher."""
import argparse

from bsgo_offline.cockpit_preview import make_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8874)
    parser.add_argument("--upstream-port", type=int, default=8765)
    args = parser.parse_args()
    server = make_server(args.port, upstream_port=args.upstream_port)
    print(f"Read-only cockpit design preview: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
