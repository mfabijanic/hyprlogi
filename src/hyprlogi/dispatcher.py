# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Turns Hyprland events into haptic effects."""

import logging
import time

from hyprlogi.config import resolve_effect


class Dispatcher:
    """Resolves events against the active profile and plays them on a backend.

    One user action emits a burst of events (a workspace switch sends
    workspace, workspacev2, activewindow, activewindowv2, focusedmon...).
    Only the first effect of a burst is played: anything resolving to an
    effect within `cooldown_ms` of the last played one is dropped, so the
    pulses do not blur into one long buzz.
    """

    def __init__(self, backend, profile, cooldown_ms=40, clock=time.monotonic, paused=lambda: False):
        self.backend = backend
        self.paused = paused
        self.profile = profile
        self.cooldown = cooldown_ms / 1000
        self.clock = clock
        self.last_window = None
        self.last_played = None

    def effect_for(self, cmd, args):
        """Returns the effect to play for an event, honoring dedupe and cooldown."""
        if cmd == "activewindowv2":
            if args == self.last_window:
                return None
            self.last_window = args

        effect_id = resolve_effect(self.profile, cmd, args)
        if effect_id is None:
            return None

        now = self.clock()
        if self.last_played is not None and now - self.last_played < self.cooldown:
            logging.debug("Event '%s' -> effect %d suppressed by cooldown", cmd, effect_id)
            return None
        self.last_played = now
        return effect_id

    async def handle(self, cmd, args):
        logging.debug("Received event: %s -> %s", cmd, ",".join(args))
        effect_id = self.effect_for(cmd, args)
        if effect_id is not None and not self.paused():
            logging.debug("Event '%s' -> Triggering effect ID: %d", cmd, effect_id)
            await self.backend.play(effect_id)
