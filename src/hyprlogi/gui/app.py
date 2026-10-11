# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Entry point of the profile editor (`hyprlogi-gui`, `hyprlogi gui`)."""

import sys

APP_ID = "io.github.mfabijanic.HyprLogi"
MISSING = ("The HyprLogi profile editor needs GTK 4 and libadwaita for Python:\n"
           "  sudo pacman -S python-gobject gtk4 libadwaita")


def run(argv=None):
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
    except (ImportError, ValueError) as e:
        print(f"{MISSING}\n\nThis Python ({sys.executable}) cannot load them: {e}", file=sys.stderr)
        if sys.prefix != sys.base_prefix:
            # A virtualenv does not see the system's python-gobject
            print("It runs in a virtual environment: deactivate it, or start the editor with "
                  "/usr/bin/python3.", file=sys.stderr)
        return 1

    from hyprlogi.gui.window import Window

    app = Adw.Application(application_id=APP_ID)

    def activate(application):
        window = application.get_active_window() or Window(application=application)
        window.present()
    app.connect("activate", activate)
    return app.run(argv if argv is not None else sys.argv[:1])


if __name__ == "__main__":
    sys.exit(run())
