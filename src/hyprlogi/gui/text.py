# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pasted-text rules for the editor's fields; no GTK, so they are testable on their own."""

import unicodedata

# Every field takes a short single line: profile, event and effect names, an event argument
PASTE_LIMIT = 1024


def clean_pasted_text(data):
    """Pasted bytes -> one line of printable text (control characters and line breaks become spaces)."""
    text = bytes(data[:PASTE_LIMIT]).decode("utf-8", errors="replace")
    cleaned = "".join(" " if unicodedata.category(c).startswith("C") else c for c in text)
    return " ".join(cleaned.split())
