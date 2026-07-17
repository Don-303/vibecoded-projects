#!/usr/bin/env python3
"""
QuantLab desktop launcher — runs the FastAPI server in-process on a random
free localhost port and hosts the UI in a native GTK/WebKit window instead of
a browser. Closing the window shuts down the server and the Jupyter kernel.

Run:  .venv/bin/python desktop.py
"""

import logging
import socket
import threading
import time

import uvicorn
import webview

try:  # identify as "quantlab" to the window manager / app launcher
    from gi.repository import GLib
    GLib.set_prgname("quantlab")
except Exception:
    pass

from app import app

log = logging.getLogger("quantlab.desktop")


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(host: str = "127.0.0.1", port: int | None = None) -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)
    port = port or find_free_port()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()

    # Wait for the server (and the kernel it boots) to come up.
    deadline = time.monotonic() + 90
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise SystemExit("QuantLab server failed to start — run "
                             "`.venv/bin/uvicorn app:app` to see the error.")
        time.sleep(0.1)

    url = f"http://{host}:{port}"
    log.info("Serving on %s", url)
    webview.create_window(
        "QuantLab — Notebook + AI Quant Reviewer",
        url,
        width=1560,
        height=980,
        min_size=(1000, 640),
        background_color="#0d1117",
        text_select=True,
        zoomable=True,
    )
    try:
        # GTK backend (webkit2gtk); pywebview falls back to Qt if unavailable.
        webview.start(gui="gtk")
    finally:
        # Window closed -> stop uvicorn; its lifespan hook shuts the kernel down.
        server.should_exit = True
        thread.join(timeout=30)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
