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
import json
import logging
import signal
import sys

from hyprlogi import __version__, control, omarchy
from hyprlogi.backends import BACKENDS, create_backend
from hyprlogi.backends.hidpp import DeviceNotFound, HidppBackend, list_hidraw
from hyprlogi.backends.logiops import LogiOpsUnavailable
from hyprlogi.config import list_profiles, load_config, load_settings, set_active_profile, setup_default_config
from hyprlogi.dispatcher import Dispatcher
from hyprlogi.hyprland import hyprland_listener

PLAY_GAP = 0.6
TEST_GAP = 0.8


def effect_id(value):
    effect = int(value)
    if not 0 <= effect <= 255:
        raise argparse.ArgumentTypeError(f"effect must be 0-255, got {effect}")
    return effect


def parse_args(argv=None):
    # Options are accepted before and after the command
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--debug', action='store_true', default=argparse.SUPPRESS, help="Enable debug logging.")
    common.add_argument('--profile', '-p', type=str, default=argparse.SUPPRESS,
                        help="Temporarily use a specific profile from the profiles directory (e.g., 'subtle').")
    common.add_argument('--backend', '-b', choices=BACKENDS, default=argparse.SUPPRESS,
                        help="Override the backend from settings.json.")

    parser = argparse.ArgumentParser(
        prog="hyprlogi", parents=[common],
        description="Haptic feedback on Logitech mice for Hyprland events.")
    parser.add_argument('--version', action='version', version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest='command', metavar='command')

    commands.add_parser('daemon', parents=[common], help="Watch Hyprland events (default).")
    p = commands.add_parser('play', parents=[common],
                            help="Play effects through the running daemon (directly if it does not run).")
    p.add_argument('effects', nargs='+', type=effect_id)
    p = commands.add_parser('test', parents=[common], help="Play effects directly on the device.")
    p.add_argument('effects', nargs='+', type=effect_id)
    p = commands.add_parser('profile', parents=[common], help="List, show or set the active profile.")
    p.add_argument('action', nargs='?', default='list', choices=('list', 'get', 'set'))
    p.add_argument('name', nargs='?')
    p.add_argument('--json', action='store_true', help="Print the profile list as JSON.")
    commands.add_parser('pause', parents=[common], help="Pause haptic feedback.")
    commands.add_parser('resume', parents=[common], help="Resume haptic feedback.")
    commands.add_parser('toggle', parents=[common], help="Pause or resume haptic feedback.")
    p = commands.add_parser('status', parents=[common], help="Show the daemon state.")
    p.add_argument('--paused', action='store_true', help="Print nothing; exit 0 only if paused.")
    p.add_argument('--json', action='store_true', help="Print the state as JSON (exit 0 even if not running).")
    commands.add_parser('waveforms', parents=[common], help="Show the waveforms the device supports.")
    commands.add_parser('devices', parents=[common], help="List Logitech HID++ devices.")
    p = commands.add_parser('omarchy', parents=[common], help="Set up or remove the Omarchy integration.")
    p.add_argument('action', choices=('install', 'uninstall', 'menu'))
    p.add_argument('--no-hooks', action='store_true', help="Do not install the battery-low and theme-set hooks.")
    p.add_argument('--plugin', action='store_true', help="Also add the HyprLogi bar widget (hyprlogi-omarchy).")

    args = parser.parse_args(argv)
    for name, default in (('debug', False), ('profile', None), ('backend', None)):
        if not hasattr(args, name):
            setattr(args, name, default)
    args.command = args.command or 'daemon'
    if args.command == 'profile' and (args.action == 'set') != bool(args.name):
        parser.error("use 'hyprlogi profile set NAME' (list and get take no name)")
    return args


class Daemon:
    """The Hyprland watcher plus its control socket."""

    def __init__(self, args):
        self.args = args
        self.profile_override = args.profile
        self.backend = None
        self.dispatcher = None
        self.settings = None

    def reload(self, drop_override=False):
        # The backend is kept; changing it needs a restart
        if drop_override:
            self.profile_override = None
        self.settings, profile = load_config(self.profile_override, self.args.backend)
        self.dispatcher.profile = profile
        logging.info("Configuration reloaded.")

    async def handle_request(self, req):
        cmd = req["cmd"]
        if self.backend is None:
            return {"ok": False, "error": "daemon is starting"}
        if cmd == "status":
            reply = {"ok": True, "profile": self.settings.get("active_profile"),
                     "backend": self.backend.name, "paused": control.is_paused()}
            info = getattr(self.backend, "info", None)
            if info:
                reply["device"] = f"{info.name} ({info.path})"
            return reply
        if cmd == "reload":
            self.reload(drop_override=True)
            return {"ok": True, "profile": self.settings.get("active_profile")}
        if cmd == "play":
            effects = [effect_id(e) for e in req["effects"]]
            if control.is_paused():
                return {"ok": True, "played": [], "paused": True}
            for i, effect in enumerate(effects):
                if i:
                    await asyncio.sleep(PLAY_GAP)
                logging.debug("Control request -> Triggering effect ID: %d", effect)
                await self.backend.play(effect)
            return {"ok": True, "played": effects}
        return {"ok": False, "error": f"unknown command '{cmd}'"}

    async def run(self):
        """Watches Hyprland events and plays the profile's effects."""
        # Claim the control socket first: a second daemon must not open the device
        server = await control.serve(self.handle_request)
        try:
            self.settings, profile = load_config(self.profile_override, self.args.backend)
            self.backend = await create_backend(self.settings)
            self.dispatcher = Dispatcher(self.backend, profile, self.settings.get("cooldown_ms", 40),
                                         paused=control.is_paused)

            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGHUP, self.reload)
            loop.add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)

            logging.info("Starting Hyprland event listener...")
            async for cmd, cmd_args in hyprland_listener():
                await self.dispatcher.handle(cmd, cmd_args)
        except asyncio.CancelledError:
            logging.info("Listener cancelled.")
        finally:
            server.close()
            control.SOCKET_PATH.unlink(missing_ok=True)
            if self.backend:
                await self.backend.close()
                logging.info("Backend closed.")


async def play_effects(args, effects, gap):
    """Plays effects on a backend of our own."""
    settings, _ = load_config(args.profile, args.backend)
    backend = await create_backend(settings, strict=True)
    try:
        for i, effect in enumerate(effects):
            if i:
                await asyncio.sleep(gap)
            print(f"Playing effect {effect}")
            await backend.play(effect)
    finally:
        await backend.close()


async def test_effects(args):
    """Plays the given effects directly, ignoring pause."""
    await play_effects(args, args.effects, TEST_GAP)


async def play(args):
    """Plays effects through the daemon, so its device connection is reused."""
    reply = await control.request({"cmd": "play", "effects": args.effects})
    if reply is None:
        if control.is_paused():
            return
        logging.debug("Daemon is not running, playing directly")
        await play_effects(args, args.effects, PLAY_GAP)
    elif not reply.get("ok"):
        raise ValueError(reply.get("error"))


async def notify_daemon():
    reply = await control.request({"cmd": "reload"})
    if reply:
        print(f"Daemon now uses {reply['profile']}")


def profile_name(filename):
    return filename.removesuffix(".json") if filename else filename


async def profile(args):
    setup_default_config()
    active = profile_name(load_settings().get("active_profile"))
    if args.action == 'get':
        print(active)
    elif args.action == 'set':
        filename = set_active_profile(args.name)
        print(f"Active profile: {filename}")
        await notify_daemon()
    elif args.json:
        print(json.dumps({"active": active, "profiles": [profile_name(f) for f in list_profiles()]}))
    else:
        for name in map(profile_name, list_profiles()):
            print(f"{'*' if name == active else ' '} {name}")


async def set_paused(args):
    paused = {"pause": True, "resume": False, "toggle": not control.is_paused()}[args.command]
    control.set_paused(paused)
    print("Haptics paused" if paused else "Haptics resumed")


async def status_data():
    """The daemon state as one dict; profile names have no .json suffix."""
    setup_default_config()
    reply = await control.request({"cmd": "status"})
    data = {
        "version": __version__,
        "running": reply is not None,
        "paused": control.is_paused(),
        "profile": profile_name(load_settings().get("active_profile")),
        "profiles": [profile_name(f) for f in list_profiles()],
    }
    if reply:
        data["profile"] = profile_name(reply.get("profile"))
        for key in ("backend", "device"):
            if key in reply:
                data[key] = reply[key]
    return data


async def status(args):
    if args.paused:
        sys.exit(0 if control.is_paused() else 1)
    data = await status_data()
    if args.json:
        print(json.dumps(data))
        return
    print(f"Daemon:  {'running' if data['running'] else 'not running'}")
    for key in ("profile", "backend", "device"):
        if key in data:
            print(f"{key.capitalize() + ':':<9}{data[key]}")
    print(f"Paused:  {'yes' if data['paused'] else 'no'}")
    if not data["running"]:
        sys.exit(3)


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
            status_ = f"haptics: device index 0x{backend.device_index:02x}, feature index {backend.feature_index}"
        except DeviceNotFound:
            status_ = "no haptics (or no access)"
        finally:
            await backend.close()
        print(f"{node.path}  {node.name}  [{node.bus:04x}:046d:{node.product:04x}]  {status_}")


async def omarchy_integration(args):
    setup_default_config()
    if args.action == 'uninstall':
        omarchy.uninstall()
        return
    if args.action == 'menu':
        omarchy.install_menu(omarchy.hyprlogi_command())
        return
    if omarchy.install(hooks=not args.no_hooks):
        await notify_daemon()
    if args.plugin:
        omarchy.install_plugin()


async def main(args):
    if args.command == 'daemon':
        await Daemon(args).run()
        return
    handlers = {
        "play": play, "test": test_effects, "profile": profile,
        "pause": set_paused, "resume": set_paused, "toggle": set_paused, "status": status,
        "waveforms": show_waveforms, "devices": show_devices, "omarchy": omarchy_integration,
    }
    await handlers[args.command](args)


def run(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='%(levelname)s: %(message)s')
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        logging.info("Watcher stopped by user.")
    except (DeviceNotFound, LogiOpsUnavailable, control.DaemonRunning, ValueError) as e:
        logging.error("%s", e)
        sys.exit(1)


if __name__ == "__main__":
    run()
