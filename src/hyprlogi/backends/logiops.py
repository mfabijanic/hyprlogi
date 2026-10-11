# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""LogiOps backend: logid with the haptic-feedback patch, over the system D-Bus.

Requires https://github.com/kris7t/logiops/tree/haptic-feedback and the
optional `dbus-next` dependency (pip install 'hyprlogi[logiops]').
"""

import logging
import time

BUS_NAME = 'pizza.pixl.LogiOps'
ROOT_PATH = '/pizza/pixl/logiops'
DEVICES_IFACE = 'pizza.pixl.LogiOps.Devices'
HAPTIC_IFACE = 'pizza.pixl.LogiOps.HapticFeedback'

EMPTY_REFRESH_INTERVAL = 5


class LogiOpsUnavailable(Exception):
    pass


class LogiOpsBackend:
    name = "logiops"

    def __init__(self):
        self.bus = None
        self.devices = None
        self._refreshed_at = 0.0

    async def open(self, strict=False):
        try:
            import dbus_next as dbus
        except ImportError as e:
            raise LogiOpsUnavailable("dbus-next is not installed (pip install 'hyprlogi[logiops]')") from e
        self._dbus = dbus
        try:
            self.bus = await dbus.aio.MessageBus(bus_type=dbus.BusType.SYSTEM).connect()
        except (dbus.DBusError, OSError) as e:
            raise LogiOpsUnavailable(f"cannot connect to the system D-Bus: {e}") from e
        logging.debug("Connected to the system D-Bus.")

    async def _call(self, **kwargs):
        reply = await self.bus.call(self._dbus.Message(destination=BUS_NAME, **kwargs))
        if reply.message_type == self._dbus.MessageType.ERROR:
            raise LogiOpsUnavailable(f"{reply.error_name}: {reply.body[0] if reply.body else ''}")
        return reply

    async def refresh(self):
        """Re-enumerates logid's devices."""
        self._refreshed_at = time.monotonic()
        reply = await self._call(path=ROOT_PATH, interface=DEVICES_IFACE, member='Enumerate')
        if reply.signature != 'ao':
            raise LogiOpsUnavailable(f"unexpected D-Bus signature '{reply.signature}'")
        self.devices = list(reply.body[0])
        logging.debug("logid devices: %s", self.devices)
        return self.devices

    async def has_haptics(self):
        """True when logid runs and exposes the haptic-feedback interface."""
        for device_path in await self.refresh():
            try:
                node = await self.bus.introspect(BUS_NAME, device_path)
            except self._dbus.DBusError as e:
                raise LogiOpsUnavailable(f"cannot introspect {device_path}: {e}") from e
            if any(iface.name == HAPTIC_IFACE for iface in node.interfaces):
                return True
        return False

    async def play(self, effect):
        try:
            if self.devices is None or (
                    not self.devices and time.monotonic() - self._refreshed_at > EMPTY_REFRESH_INTERVAL):
                await self.refresh()
            if not self.devices:
                logging.debug("No logid devices found.")
                return
            for device_path in self.devices:
                logging.debug("Sending effect %d to device %s", effect, device_path)
                await self._call(path=device_path, interface=HAPTIC_IFACE, member='PlayEffect',
                                 signature='y', body=[effect & 0xFF])
        except LogiOpsUnavailable as e:
            logging.error("D-Bus error while playing effect: %s", e)
            # Devices may have been re-created by logid; enumerate again next time
            self.devices = None

    async def close(self):
        if self.bus:
            self.bus.disconnect()
            self.bus = None
