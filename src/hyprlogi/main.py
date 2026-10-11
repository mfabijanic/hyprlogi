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
"""Command-line entry point."""

import argparse
import asyncio
import logging
import signal
import sys

from hyprlogi import __version__
from hyprlogi.backends import BACKENDS, create_backend
from hyprlogi.backends.hidpp import DeviceNotFound, HidppBackend, list_hidraw
from hyprlogi.backends.logiops import LogiOpsUnavailable
from hyprlogi.config import load_config
from hyprlogi.dispatcher import Dispatcher
from hyprlogi.hyprland import hyprland_listener

COMMANDS = ("daemon", "test", "waveforms", "devices")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="hyprlogi",
        description="Haptic feedback on Logitech mice for Hyprland events.",
        epilog="commands: daemon (default) | test EFFECT... | waveforms | devices",
    )
    parser.add_argument('command', nargs='?', default='daemon', choices=COMMANDS, metavar='command')
    parser.add_argument('effects', nargs='*', type=int, help="Effect ids for 'test'.")
    parser.add_argument('--debug', action='store_true', help="Enable debug logging.")
    parser.add_argument('--profile', '-p', type=str,
                        help="Temporarily use a specific profile from the profiles directory (e.g., 'subtle').")
    parser.add_argument('--backend', '-b', choices=BACKENDS, help="Override the backend from settings.json.")
    parser.add_argument('--version', action='version', version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    if args.command == 'test' and not args.effects:
        parser.error("test needs at least one effect id, e.g. 'hyprlogi test 1 4'")
    return args


async def daemon(args):
    """Watches Hyprland events and plays the profile's effects."""
    settings, profile = load_config(args.profile, args.backend)
    backend = await create_backend(settings)
    dispatcher = Dispatcher(backend, profile, settings.get("cooldown_ms", 40))

    def reload():
        # The backend is kept; changing it needs a restart
        _, dispatcher.profile = load_config(args.profile, args.backend)
        logging.info("Configuration reloaded.")

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGHUP, reload)
    main_task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, main_task.cancel)

    try:
        logging.info("Starting Hyprland event listener...")
        async for cmd, cmd_args in hyprland_listener():
            await dispatcher.handle(cmd, cmd_args)
    except asyncio.CancelledError:
        logging.info("Listener cancelled.")
    finally:
        await backend.close()
        logging.info("Backend closed.")


async def test_effects(args):
    """Plays the given effects with a short pause between them."""
    settings, _ = load_config(args.profile, args.backend)
    backend = await create_backend(settings, strict=True)
    try:
        for effect in args.effects:
            print(f"Playing effect {effect}")
            await backend.play(effect)
            await asyncio.sleep(0.8)
    finally:
        await backend.close()


async def show_waveforms(args):
    """Prints the waveforms and haptic settings the device reports."""
    settings, _ = load_config(args.profile)
    backend = HidppBackend(settings.get("hidpp"))
    await backend.open(strict=True)
    try:
        enabled, intensity = await backend.configuration()
        waveforms = await backend.capabilities()
        print(f"{backend.info.name} ({backend.info.path})")
        print(f"  haptics:   {'on' if enabled else 'off'}, intensity {intensity}%")
        print(f"  waveforms: {' '.join(map(str, waveforms)) or '-'}")
        print("  (ids outside this list may still play; check with 'hyprlogi test ID')")
    finally:
        await backend.close()


async def show_devices(args):
    """Lists Logitech HID++ hidraw nodes and whether they have haptics."""
    settings, _ = load_config(args.profile)
    nodes = list_hidraw()
    if not nodes:
        print("No Logitech HID++ hidraw devices found.")
        return
    for node in nodes:
        backend = HidppBackend({**settings.get("hidpp", {}), "device": str(node.path)})
        try:
            await backend.open(strict=True)
            status = f"haptics: device index 0x{backend.device_index:02x}, feature index {backend.feature_index}"
        except DeviceNotFound:
            status = "no haptics (or no access)"
        finally:
            await backend.close()
        print(f"{node.path}  {node.name}  [{node.bus:04x}:046d:{node.product:04x}]  {status}")


async def main(args):
    handlers = {"daemon": daemon, "test": test_effects, "waveforms": show_waveforms, "devices": show_devices}
    await handlers[args.command](args)


def run(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='%(levelname)s: %(message)s')
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        logging.info("Watcher stopped by user.")
    except (DeviceNotFound, LogiOpsUnavailable, ValueError) as e:
        logging.error("%s", e)
        sys.exit(1)


if __name__ == "__main__":
    run()
