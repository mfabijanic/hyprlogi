# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Haptic backends.

A backend has `async open(strict)`, `async play(effect)` and `async close()`.
`play` never raises: it logs and recovers (reconnects) on its own.
"""

import logging

from hyprlogi.backends.hidpp import HidppBackend
from hyprlogi.backends.logiops import LogiOpsBackend, LogiOpsUnavailable

BACKENDS = ("auto", "hidpp", "logiops")


async def create_backend(settings, strict=False):
    """Creates and opens the backend chosen in settings["backend"]."""
    kind = settings.get("backend", "auto")
    if kind not in BACKENDS:
        raise ValueError(f"unknown backend '{kind}', expected one of {', '.join(BACKENDS)}")

    if kind in ("auto", "logiops"):
        backend = LogiOpsBackend()
        try:
            await backend.open()
            if kind == "logiops" or await backend.has_haptics():
                logging.info("Using the logiops backend (logid over D-Bus).")
                return backend
            logging.debug("logid has no haptic-feedback interface")
        except LogiOpsUnavailable as e:
            if kind == "logiops":
                raise
            logging.debug("logiops backend unavailable: %s", e)
        await backend.close()

    logging.info("Using the hidpp backend (direct HID++ over hidraw).")
    backend = HidppBackend(settings.get("hidpp"))
    await backend.open(strict=strict)
    return backend
