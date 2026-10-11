# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Talking to a running daemon.

The daemon listens on a Unix socket for one JSON request per connection
(`{"cmd": "play", "effects": [1]}`) and answers with one JSON line. Pausing
is a flag file instead, so it works (and is cheap to check from the Omarchy
menu) whether or not the daemon runs.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

RUNTIME_DIR = Path(os.getenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "hyprlogi"
SOCKET_PATH = RUNTIME_DIR / "control.sock"
PAUSED_FLAG = RUNTIME_DIR / "paused"

REQUEST_TIMEOUT = 5


class DaemonRunning(Exception):
    pass


def is_paused():
    return PAUSED_FLAG.exists()


def set_paused(paused):
    RUNTIME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if paused:
        PAUSED_FLAG.touch()
    else:
        PAUSED_FLAG.unlink(missing_ok=True)


async def request(req, socket_path=SOCKET_PATH):
    """Sends a request to the daemon; returns its reply, or None if it does not run."""
    try:
        reader, writer = await asyncio.open_unix_connection(socket_path)
    except (FileNotFoundError, ConnectionRefusedError):
        return None
    try:
        writer.write(json.dumps(req).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), REQUEST_TIMEOUT)
        return json.loads(line) if line else None
    finally:
        writer.close()


async def serve(handler, socket_path=SOCKET_PATH):
    """Starts the control server; `handler(request) -> reply` is awaited per request."""
    socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if socket_path.exists():
        if await request({"cmd": "status"}, socket_path) is not None:
            raise DaemonRunning(f"another hyprlogi daemon is listening on {socket_path}")
        socket_path.unlink()

    async def on_connect(reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), REQUEST_TIMEOUT)
            try:
                reply = await handler(json.loads(line))
            except Exception as e:  # noqa: BLE001 - always answer, or the client assumes no daemon
                logging.debug("Control request %r failed: %s", line, e)
                reply = {"ok": False, "error": f"bad request: {e}"}
            writer.write(json.dumps(reply).encode() + b"\n")
            await writer.drain()
        except (OSError, asyncio.TimeoutError) as e:
            logging.debug("Control connection failed: %s", e)
        finally:
            writer.close()

    server = await asyncio.start_unix_server(on_connect, socket_path)
    os.chmod(socket_path, 0o600)
    return server
