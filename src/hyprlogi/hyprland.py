# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hyprland socket2 event listener."""

import asyncio
import logging
import os
from pathlib import Path

RECONNECT_DELAYS = (0.5, 1, 2, 5)


def find_socket():
    """Returns the socket2 path of the running Hyprland instance, or None.

    Prefers $HYPRLAND_INSTANCE_SIGNATURE, but falls back to the most recently
    started instance: the variable is often missing from the systemd user
    environment, and goes stale when Hyprland restarts.
    """
    runtime_dir = Path(os.getenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    signature = os.getenv("HYPRLAND_INSTANCE_SIGNATURE")
    if signature:
        path = runtime_dir / "hypr" / signature / ".socket2.sock"
        if path.exists():
            return path

    candidates = list((runtime_dir / "hypr").glob("*/.socket2.sock"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def parse_event(line):
    """Splits 'name>>a,b,c' into ('name', ['a', 'b', 'c']), or None."""
    if ">>" not in line:
        return None
    cmd, data = line.split(">>", 1)
    return cmd, data.split(",")


async def hyprland_listener():
    """Yields (event, args) from Hyprland, reconnecting when the socket goes away."""
    attempt = 0
    while True:
        socket_path = find_socket()
        try:
            if socket_path is None:
                raise FileNotFoundError("Hyprland socket not found")
            reader, writer = await asyncio.open_unix_connection(socket_path, limit=1 << 20)
        except OSError as e:
            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            attempt += 1
            logging.warning("Cannot connect to Hyprland (%s), retrying in %ss", e, delay)
            await asyncio.sleep(delay)
            continue

        attempt = 0
        logging.info("Connected to Hyprland socket %s", socket_path)
        try:
            while line := await reader.readline():
                event = parse_event(line.decode(errors="replace").rstrip("\n"))
                if event:
                    yield event
        except (OSError, asyncio.IncompleteReadError, ValueError) as e:
            logging.warning("Hyprland socket error: %s", e)
        finally:
            writer.close()
        logging.warning("Hyprland socket closed, reconnecting...")
        await asyncio.sleep(RECONNECT_DELAYS[0])
