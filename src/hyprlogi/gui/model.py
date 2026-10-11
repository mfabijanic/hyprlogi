# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Profile editing logic for the GUI; no GTK, so it is testable on its own."""

import copy
import json
import os
import re
from collections import namedtuple

from hyprlogi import config

Event = namedtuple("Event", "name description argument")

# The socket2 events worth a haptic, with what their first argument means:
# argument rules in a profile match only the first argument (config.resolve_effect).
EVENT_CATALOG = (
    Event("workspace", "Switched to another workspace", "workspace name"),
    Event("activewindow", "Focus moved to another window", "window class (rules per app)"),
    Event("activewindowv2", "Focus moved to another window", "window address"),
    Event("openwindow", "A window opened", "window address"),
    Event("closewindow", "A window closed", "window address"),
    Event("movewindow", "A window moved to another workspace", "window address"),
    Event("fullscreen", "Fullscreen turned on or off", "1 = on, 0 = off"),
    Event("changefloatingmode", "A window started or stopped floating", "window address"),
    Event("urgent", "A window asks for attention", "window address"),
    Event("openlayer", "A layer surface opened (notification, menu, OSD)", "layer namespace"),
    Event("closelayer", "A layer surface closed", "layer namespace"),
    Event("focusedmon", "Focus moved to another monitor", "monitor name"),
    Event("activespecial", "A special workspace was shown or hidden", "special workspace name"),
    Event("createworkspace", "A workspace was created", "workspace name"),
    Event("destroyworkspace", "A workspace was destroyed", "workspace name"),
    Event("submap", "A keybind submap was entered or left", "submap name"),
    Event("screencast", "Screen sharing started or stopped", "1 = on, 0 = off"),
    Event("monitoradded", "A monitor was connected", "monitor name"),
    Event("monitorremoved", "A monitor was disconnected", "monitor name"),
)
CATALOG = {event.name: event for event in EVENT_CATALOG}

FALLBACK_WAVEFORMS = list(range(15))
EMPTY_PROFILE = {"default_effect": None, "events": {}}

_EVENT_NAME = re.compile(r"^[a-z0-9_]+$")
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def effect_label(effect, names):
    """4 -> '4 · Soft tick' when named, else '4'; None -> 'None'."""
    if effect is None:
        return "None"
    name = names.get(effect)
    return f"{effect} · {name}" if name else str(effect)


def check_effect(effect):
    if effect is None:
        return None
    if isinstance(effect, bool) or not isinstance(effect, int) or not 0 <= effect <= 255:
        raise ValueError(f"effect must be 0-255 or None, got {effect!r}")
    return effect


def check_event_name(name):
    name = (name or "").strip()
    if not _EVENT_NAME.match(name):
        raise ValueError(f"'{name}' is not a Hyprland event name (lowercase letters, digits, _)")
    return name


def check_profile_name(name):
    name = (name or "").strip().removesuffix(".json")
    if not _PROFILE_NAME.match(name):
        raise ValueError(f"'{name}' is not a valid profile name (letters, digits, . _ -)")
    return name


class ProfileDoc:
    """One profile file. Unknown keys and the order of events survive a save."""

    def __init__(self, path, data):
        self.path = path
        self.data = data
        self.data.setdefault("events", {})
        self.dirty = False

    @classmethod
    def load(cls, path):
        try:
            with open(path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path.name} is not valid JSON: {e}") from e
        if not isinstance(data, dict) or not isinstance(data.get("events", {}), dict):
            raise ValueError(f"{path.name} is not a HyprLogi profile")
        return cls(path, data)

    @property
    def name(self):
        return self.path.stem

    @property
    def events(self):
        return self.data["events"]

    def _changed(self):
        self.dirty = True

    # Default effect

    @property
    def default_effect(self):
        return self.data.get("default_effect")

    def set_default_effect(self, effect):
        self.data["default_effect"] = check_effect(effect)
        self._changed()

    # Events

    def event_names(self):
        return list(self.events)

    def event_effect(self, event):
        value = self.events.get(event)
        return value.get("default") if isinstance(value, dict) else value

    def arg_rules(self, event):
        value = self.events.get(event)
        return dict(value.get("args", {})) if isinstance(value, dict) else {}

    def add_event(self, event, effect=None):
        """Adds an event; returns False if the profile already has it."""
        event = check_event_name(event)
        if event in self.events:
            return False
        self.events[event] = check_effect(effect)
        self._changed()
        return True

    def remove_event(self, event):
        self.events.pop(event, None)
        self._changed()

    def set_event_effect(self, event, effect):
        effect = check_effect(effect)
        value = self.events.get(event)
        if isinstance(value, dict):
            value["default"] = effect
        else:
            self.events[event] = effect
        self._changed()

    def set_arg_effect(self, event, arg, effect):
        """Adds or changes an argument rule; the event switches to the {default, args} form."""
        arg = (arg or "").strip()
        if not arg:
            raise ValueError("the argument must not be empty")
        effect = check_effect(effect)
        value = self.events.get(event)
        if not isinstance(value, dict):
            value = {"default": value, "args": {}}
            self.events[event] = value
        value.setdefault("args", {})[arg] = effect
        self._changed()

    def remove_arg(self, event, arg):
        """Removes an argument rule; without rules left the event goes back to a plain effect."""
        value = self.events.get(event)
        if not isinstance(value, dict):
            return
        value.get("args", {}).pop(arg, None)
        if not value.get("args") and set(value) <= {"default", "args"}:
            self.events[event] = value.get("default")
        self._changed()

    def validate(self):
        """Returns a list of problems; empty when the profile can be saved."""
        problems = []
        try:
            check_effect(self.default_effect)
        except ValueError as e:
            problems.append(f"default_effect: {e}")
        for event, value in self.events.items():
            try:
                check_event_name(event)
                check_effect(self.event_effect(event))
                for arg, effect in self.arg_rules(event).items():
                    try:
                        check_effect(effect)
                    except ValueError as e:
                        problems.append(f"{event} › {arg}: {e}")
            except ValueError as e:
                problems.append(f"{event}: {e}")
        return problems

    def save(self):
        problems = self.validate()
        if problems:
            raise ValueError("; ".join(problems))
        write_json(self.path, self.data)
        self.dirty = False


def write_json(path, data):
    """Writes atomically, so the daemon never reads half a file."""
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


# Profile files

def profile_path(name):
    return config.PROFILES_DIR / f"{check_profile_name(name)}.json"


def profile_names():
    return [filename.removesuffix(".json") for filename in config.list_profiles()]


def active_profile_name():
    return config.load_settings().get("active_profile", "").removesuffix(".json")


def _new_path(name):
    path = profile_path(name)
    if path.exists():
        raise ValueError(f"profile '{path.stem}' already exists")
    return path


def new_profile(name):
    path = _new_path(name)
    write_json(path, copy.deepcopy(EMPTY_PROFILE))
    return path.stem


def duplicate_profile(source, name):
    path = _new_path(name)
    write_json(path, ProfileDoc.load(profile_path(source)).data)
    return path.stem


def rename_profile(old, new):
    """Renames a profile file; renaming the active one updates settings.json."""
    source, target = profile_path(old), _new_path(new)
    was_active = active_profile_name() == source.stem
    os.replace(source, target)
    if was_active:
        config.set_active_profile(target.stem)
    return target.stem


def delete_profile(name):
    path = profile_path(name)
    if active_profile_name() == path.stem:
        raise ValueError("the active profile cannot be deleted; activate another one first")
    path.unlink()
