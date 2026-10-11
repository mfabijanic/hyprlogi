# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings and profile loading (XDG layout)."""

import copy
import json
import logging
import os
import shutil
from pathlib import Path

# Profiles shipped with the package, copied to the user's config on first run
SOURCE_PROFILES_DIR = Path(__file__).parent / "profiles"

# XDG compliant paths
XDG_CONFIG_HOME = os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")
CONFIG_DIR = Path(XDG_CONFIG_HOME) / "hyprlogi"
PROFILES_DIR = CONFIG_DIR / "profiles"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "active_profile": "default.json",
    # "auto" picks logiops when the patched logid is on D-Bus, otherwise hidpp
    "backend": "auto",
    "hidpp": {
        # "auto", a /dev/hidrawN path, or a device name (e.g. "MX Master 4")
        "device": "auto",
        # HID++ software id; OpenLogi leases ids from 1 upwards, so stay at the top
        "sw_id": 15,
        # Turn firmware haptics on (at this intensity) if they are disabled
        "ensure_enabled": False,
        "intensity": 60,
    },
    # Events arriving within this window after a played effect are not played
    "cooldown_ms": 40,
}


def setup_default_config():
    """Creates the default configuration files and directories if they don't exist."""
    if not CONFIG_DIR.exists():
        logging.info("Creating configuration directory at %s", CONFIG_DIR)
        CONFIG_DIR.mkdir(parents=True)

    if not PROFILES_DIR.exists():
        logging.info("Creating profiles directory at %s", PROFILES_DIR)
        PROFILES_DIR.mkdir()

    # Copy default profiles if they don't exist in the target directory
    for src_profile in SOURCE_PROFILES_DIR.glob("*.json"):
        dest_profile = PROFILES_DIR / src_profile.name
        if not dest_profile.exists():
            logging.info("Copying default profile '%s' to %s", src_profile.name, PROFILES_DIR)
            shutil.copy(src_profile, dest_profile)

    if not SETTINGS_FILE.exists():
        logging.info("Creating default settings file at %s", SETTINGS_FILE)
        with open(SETTINGS_FILE, 'w') as f:
            json.dump({"active_profile": DEFAULT_SETTINGS["active_profile"]}, f, indent=2)


def _merge(defaults, overrides):
    merged = copy.deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings():
    """Loads settings.json merged over DEFAULT_SETTINGS."""
    try:
        with open(SETTINGS_FILE, 'r') as f:
            return _merge(DEFAULT_SETTINGS, json.load(f))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.error("Could not read settings file %s: %s", SETTINGS_FILE, e)
        return copy.deepcopy(DEFAULT_SETTINGS)


def load_profile(profile_filename):
    """Loads a profile from PROFILES_DIR; returns an empty profile on error."""
    if not profile_filename:
        logging.error("No active profile is defined in %s.", SETTINGS_FILE)
        return {"events": {}}
    if not profile_filename.endswith('.json'):
        profile_filename = f"{profile_filename}.json"

    profile_path = PROFILES_DIR / profile_filename
    try:
        with open(profile_path, 'r') as f:
            profile = json.load(f)
        logging.info("Profile '%s' loaded successfully.", profile_filename)
        return profile
    except FileNotFoundError:
        logging.error("Profile file not found: %s", profile_path)
    except json.JSONDecodeError as e:
        logging.error("Error decoding profile file %s: %s", profile_path, e)
    return {"events": {}}


def load_config(profile_override=None, backend_override=None):
    """Returns (settings, profile), applying command-line overrides."""
    setup_default_config()
    settings = load_settings()
    if backend_override:
        settings["backend"] = backend_override

    if profile_override:
        logging.info("Using profile from command-line argument: %s", profile_override)
        profile_filename = profile_override
    else:
        profile_filename = settings.get("active_profile")
        logging.info("Using active profile from settings: %s", profile_filename)

    return settings, load_profile(profile_filename)


def resolve_effect(profile, cmd, args):
    """Maps a Hyprland event to an effect id, or None for no effect."""
    effect_config = profile.get("events", {}).get(cmd)
    effect_id = None

    if isinstance(effect_config, dict):
        # A dictionary means we have argument-specific effects
        if args and args[0] in effect_config.get("args", {}):
            effect_id = effect_config["args"][args[0]]
        else:
            effect_id = effect_config.get("default")
    else:
        # Otherwise, it's a direct effect ID
        effect_id = effect_config

    # If event-specific or argument-specific effect not found, use default_effect
    if effect_id is None:
        effect_id = profile.get("default_effect")
    return effect_id
