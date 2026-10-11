# HyprLogi - Haptic Feedback for Hyprland Events
# SPDX-License-Identifier: GPL-3.0-or-later
"""Profile editor (GTK 4 / libadwaita). Only gui.app and the modules it loads import gi."""

import importlib.util
import os
from pathlib import Path

TYPELIB_DIRS = ("/usr/lib/girepository-1.0", "/usr/lib64/girepository-1.0",
                "/usr/local/lib/girepository-1.0")
TYPELIBS = ("Gtk-4.0.typelib", "Adw-1.typelib")


def available():
    """True when the editor can start: PyGObject plus the GTK 4 and libadwaita typelibs.

    Cheap enough for `hyprlogi status --json`, which the bar widget polls: gi is not imported.
    """
    if importlib.util.find_spec("gi") is None:
        return False
    dirs = [d for d in os.environ.get("GI_TYPELIB_PATH", "").split(os.pathsep) if d] + list(TYPELIB_DIRS)
    return all(any((Path(d) / typelib).exists() for d in dirs) for typelib in TYPELIBS)
