# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Talking to the daemon without blocking the GTK main loop.

Each call runs in a worker thread and hands its result back to the main
loop with GLib.idle_add.
"""

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

from gi.repository import GLib

import hyprlogi
from hyprlogi import control

# Lets `python -m hyprlogi` find the package also when the GUI runs from a source checkout
_PACKAGE_ROOT = str(Path(hyprlogi.__file__).resolve().parent.parent)


def _in_background(work, callback):
    def worker():
        try:
            result = work()
        except Exception as e:  # noqa: BLE001 - report every failure to the UI
            result = {"ok": False, "error": str(e)}
        if callback:
            GLib.idle_add(callback, result)
    threading.Thread(target=worker, daemon=True).start()


def _request(req):
    """The daemon's reply, or None when it does not run."""
    return asyncio.run(control.request(req))


def call(req, callback=None):
    """Sends one control request; callback(reply or None)."""
    _in_background(lambda: _request(req), callback)


def play(effects, callback=None):
    """Plays effects through the daemon, or directly when it does not run.

    callback(message or None): a message explains why nothing played.
    """
    def work():
        reply = _request({"cmd": "play", "effects": list(effects)})
        if reply is None:
            # `hyprlogi play` opens the device itself when there is no daemon
            env = dict(os.environ, PYTHONPATH=os.pathsep.join(
                filter(None, [_PACKAGE_ROOT, os.environ.get("PYTHONPATH")])))
            done = subprocess.run([sys.executable, "-m", "hyprlogi", "play", *map(str, effects)],
                                  capture_output=True, text=True, timeout=30, env=env)
            if done.returncode != 0:
                return (done.stderr.strip().splitlines() or ["Could not play the effect"])[-1]
            return None
        if not reply.get("ok"):
            return reply.get("error") or "The daemon could not play the effect"
        if reply.get("paused"):
            return "Haptics are paused; resume them to hear effects"
        return None
    _in_background(work, callback)
