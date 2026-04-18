"""Kontext terminal entry point.

Installed by `pip install -e .` as the `kontext` console script. Running
`kontext` starts the local dashboard server and opens it in the default
browser. No arguments needed for the common case.

Usage:
    kontext                # start server + open browser
    kontext --no-browser   # start server only
    kontext --port 9090    # start on a specific port
"""
import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _open_browser(url: str, delay: float) -> None:
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(prog="kontext", description="Open the Kontext dashboard.")
    parser.add_argument("--host", default=None, help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: 8080)")
    parser.add_argument("--db", default=None, help="Path to kontext.db (default: per-user DB)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser")
    args = parser.parse_args()

    if args.host:
        os.environ["KONTEXT_HOST"] = args.host
    if args.port is not None:
        os.environ["KONTEXT_PORT"] = str(args.port)
    if args.db:
        os.environ["KONTEXT_DB_PATH"] = args.db

    sys.path.insert(0, str(Path(__file__).resolve().parent))

    host = os.environ.get("KONTEXT_HOST", "127.0.0.1")
    port = os.environ.get("KONTEXT_PORT", "8080")
    url = f"http://{host if host != '0.0.0.0' else 'localhost'}:{port}/dashboard"

    if not args.no_browser:
        threading.Thread(
            target=_open_browser, args=(url, 1.5), daemon=True
        ).start()

    from cloud.server import main as server_main
    return server_main()


if __name__ == "__main__":
    sys.exit(main())
