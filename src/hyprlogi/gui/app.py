# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Entry point of the profile editor (`hyprlogi-gui`, `hyprlogi gui`)."""

import sys

APP_ID = "io.github.mfabijanic.HyprLogi"
MISSING = ("The HyprLogi profile editor needs GTK 4 and libadwaita for Python:\n"
           "  sudo pacman -S python-gobject gtk4 libadwaita")


def parse_args(argv):
    import argparse
    parser = argparse.ArgumentParser(prog="hyprlogi-gui", description="HyprLogi profile editor.")
    parser.add_argument("--profile", "-p", help="Open this profile instead of the active one.")
    return parser.parse_args(argv)


def run(argv=None, profile=None):
    """Starts the editor. Without argv (the hyprlogi-gui script) the command line is parsed here."""
    if argv is None:
        profile = parse_args(sys.argv[1:]).profile
        argv = sys.argv[:1]
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
        window = application.get_active_window() or Window(application=application, profile=profile)
        window.present()
    app.connect("activate", activate)
    return app.run(argv)


if __name__ == "__main__":
    sys.exit(run())
