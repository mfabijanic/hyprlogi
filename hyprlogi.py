#!/usr/bin/env python
# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import argparse
import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

import dbus_next as dbus

# --- Global Variables ---
# Project layout
SOURCE_PROFILES_DIR = Path(__file__).parent / "profiles"

# XDG compliant paths
XDG_CONFIG_HOME = os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")
CONFIG_DIR = Path(XDG_CONFIG_HOME) / "hyprlogi"
PROFILES_DIR = CONFIG_DIR / "profiles"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

# Hyprland connection details
XDG_RUNTIME_DIR = os.getenv("XDG_RUNTIME_DIR")
HYPRLAND_INSTANCE_SIGNATURE = os.getenv("HYPRLAND_INSTANCE_SIGNATURE")
CONFIG = {}

# --- D-Bus Functions ---
async def play_effect(bus, pattern):
    """Sends a haptic feedback effect command via D-Bus."""
    if pattern is None:
        return
    try:
        msg = await bus.call(dbus.Message(
            destination='pizza.pixl.LogiOps',
            path='/pizza/pixl/logiops',
            interface='pizza.pixl.LogiOps.Devices',
            member='Enumerate',
        ))
        
        if msg.signature != 'ao':
            logging.error("Unexpected D-Bus signature: '%s'", msg.signature)
            return

        devices = msg.body[0]
        if not devices:
            logging.debug("No logid devices found.")
            return

        for device_path in devices:
            logging.debug("Sending effect %d to device %s", pattern, device_path)
            await bus.call(dbus.Message(
                destination='pizza.pixl.LogiOps',
                path=device_path,
                interface='pizza.pixl.LogiOps.HapticFeedback',
                member='PlayEffect',
                signature='y',
                body=[pattern]
            ))
    except dbus.DBusError as e:
        logging.error("D-Bus error while playing effect: %s", e)
    except Exception as e:
        logging.error("An unexpected error occurred in play_effect: %s", e)

# --- Hyprland Listener ---
async def hyprland_listener():
    """Connects to the Hyprland socket and yields events asynchronously."""
    socket_path = f"{XDG_RUNTIME_DIR}/hypr/{HYPRLAND_INSTANCE_SIGNATURE}/.socket2.sock"
    if not os.path.exists(socket_path):
        raise FileNotFoundError(f"Hyprland socket not found at {socket_path}")

    reader, _ = await asyncio.open_unix_connection(socket_path)
    logging.info("Connected to Hyprland socket.")
    while True:
        response = await reader.read(2048)
        if not response:
            break
        packages = response.decode(errors="replace").rstrip("\n").split("\n")
        for pkg in packages:
            if ">" in pkg:
                cmd, args_ = pkg.split(">", 1)
                args = args_.split(",")
                yield cmd, args

# --- Configuration Logic ---
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
            json.dump({"active_profile": "default.json"}, f, indent=2)

def load_config(args):
    """Loads the active configuration profile."""
    global CONFIG
    setup_default_config()

    profile_filename = None
    if args.profile:
        profile_filename = args.profile if args.profile.endswith('.json') else f"{args.profile}.json"
        logging.info("Using profile from command-line argument: %s", profile_filename)
    else:
        try:
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
                profile_filename = settings.get("active_profile")
            logging.info("Using active profile from settings: %s", profile_filename)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.error("Could not read settings file %s: %s", SETTINGS_FILE, e)
            return

    if not profile_filename:
        logging.error("No active profile is defined in %s.", SETTINGS_FILE)
        return

    profile_path = PROFILES_DIR / profile_filename
    try:
        with open(profile_path, 'r') as f:
            CONFIG = json.load(f)
        logging.info("Profile '%s' loaded successfully.", profile_filename)
    except FileNotFoundError:
        logging.error("Profile file not found: %s", profile_path)
        CONFIG = {"events": {}}
    except json.JSONDecodeError:
        logging.error("Error decoding profile file: %s", profile_path)
        CONFIG = {"events": {}}

async def main():
    """Main function to set up connections and watch for events."""
    parser = argparse.ArgumentParser(description="Hyprland event watcher for haptic feedback.")
    parser.add_argument('--debug', action='store_true', help="Enable debug logging.")
    parser.add_argument('--profile', '-p', type=str, help="Temporarily use a specific profile from the profiles directory (e.g., 'subtle').")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format='%(levelname)s: %(message)s')

    load_config(args)

    try:
        bus = await dbus.aio.MessageBus(bus_type=dbus.BusType.SYSTEM).connect()
        logging.info("Successfully connected to D-Bus.")
    except (dbus.DBusError, FileNotFoundError) as e:
        logging.error("Failed to connect to D-Bus: %s. Is logid running?", e)
        return

    last_window = None
    try:
        logging.info("Starting Hyprland event listener...")
        async for cmd, cmd_args in hyprland_listener():
            logging.debug("Received event: %s -> %s", cmd, ",".join(cmd_args))

            if cmd == "activewindowv2":
                if cmd_args == last_window:
                    continue
                last_window = cmd_args

            effect_config = CONFIG.get("events", {}).get(cmd)
            effect_id = None

            if isinstance(effect_config, dict):
                # If effect_config is a dictionary, it means we have argument-specific effects
                if cmd_args and cmd_args[0] in effect_config.get("args", {}):
                    effect_id = effect_config["args"][cmd_args[0]]
                else:
                    effect_id = effect_config.get("default")
            else:
                # Otherwise, it's a direct effect ID
                effect_id = effect_config
            
            # If event-specific or argument-specific effect not found, use default_effect
            if effect_id is None:
                effect_id = CONFIG.get("default_effect")

            if effect_id is not None:
                logging.debug("Event '%s' -> Triggering effect ID: %d", cmd, effect_id)
                await play_effect(bus, effect_id)

    except FileNotFoundError as e:
        logging.error(e)
    except asyncio.CancelledError:
        logging.info("Listener cancelled.")
    except Exception as e:
        logging.error("An unexpected error occurred in the main loop: %s", e)
    finally:
        bus.disconnect()
        logging.info("D-Bus connection closed.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Watcher stopped by user.")
