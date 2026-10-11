# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct HID++ 2.0 backend over Linux hidraw.

Plays waveforms with the (reverse-engineered) HapticFeedback feature 0x19B0,
the same feature OpenLogi and Solaar drive on the MX Master 4. It needs no
daemon and runs alongside one: every open hidraw handle sees every input
report, and HID++ pairs replies to requests by (feature, function, software
id). OpenLogi leases software ids from 1 upwards, so we use 15 by default.
"""

import asyncio
import errno
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

LOGITECH_VENDOR = 0x046D
BUS_USB = 0x03

REPORT_SHORT = 0x10
REPORT_LONG = 0x11
LONG_LEN = 20

DEVICE_DIRECT = 0xFF
RECEIVER_SLOTS = range(1, 7)

FEATURE_ROOT_INDEX = 0x00
FEATURE_HAPTIC = 0x19B0

ERROR_HIDPP20 = 0xFF
ERROR_HIDPP10 = 0x8F

# HapticFeedback (0x19B0) functions
FN_GET_CAPABILITIES = 0
FN_GET_CONFIGURATION = 1
FN_SET_CONFIGURATION = 2
FN_PLAY_WAVEFORM = 4

PROBE_TIMEOUT = 0.5
PLAY_TIMEOUT = 0.3
RETRY_DELAYS = (1, 2, 5, 10, 30)

HIDPP20_ERRORS = {
    1: "unknown", 2: "invalid argument", 3: "out of range", 4: "hardware error",
    5: "logitech internal", 6: "invalid feature index", 7: "invalid function id",
    8: "busy", 9: "unsupported",
}


class HidppError(Exception):
    """A HID++ error reply."""

    def __init__(self, code, hidpp10=False):
        self.code = code
        self.hidpp10 = hidpp10
        reason = f"HID++ 1.0 error 0x{code:02x}" if hidpp10 else HIDPP20_ERRORS.get(code, f"0x{code:02x}")
        super().__init__(reason)


class DeviceNotFound(Exception):
    pass


@dataclass
class HidrawInfo:
    path: Path
    name: str
    bus: int
    product: int


_VENDOR_USAGE_PAGE = re.compile(rb"\x06[\x00-\xff]\xff")


def _is_hidpp_descriptor(descriptor):
    # A vendor usage page (0xFF00 on USB, 0xFF43 on BLE) and the long HID++ report (id 0x11)
    return bool(_VENDOR_USAGE_PAGE.search(descriptor)) and bytes([0x85, REPORT_LONG]) in descriptor


def list_hidraw(sys_root=Path("/sys/class/hidraw")):
    """Returns Logitech hidraw nodes that speak HID++."""
    found = []
    for node in sorted(sys_root.glob("hidraw*")):
        try:
            uevent = (node / "device" / "uevent").read_text()
            descriptor = (node / "device" / "report_descriptor").read_bytes()
        except OSError:
            continue
        fields = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line)
        try:
            bus, vendor, product = (int(part, 16) for part in fields.get("HID_ID", "").split(":"))
        except ValueError:
            continue
        if vendor != LOGITECH_VENDOR or not _is_hidpp_descriptor(descriptor):
            continue
        found.append(HidrawInfo(Path("/dev") / node.name, fields.get("HID_NAME", ""), bus, product))
    return found


def build_request(device_index, feature_index, function, sw_id, params=b""):
    """Encodes a long HID++ 2.0 request."""
    header = bytes([REPORT_LONG, device_index, feature_index, (function << 4) | sw_id])
    return (header + bytes(params)).ljust(LONG_LEN, b"\x00")[:LONG_LEN]


def match_response(report, device_index, feature_index, fn_sw):
    """Returns the payload if `report` answers the request, raises HidppError
    if it is an error reply to it, and returns None for unrelated reports."""
    if len(report) < 6 or report[0] not in (REPORT_SHORT, REPORT_LONG) or report[1] != device_index:
        return None
    if report[2] == feature_index and report[3] == fn_sw:
        return bytes(report[4:])
    if report[2] in (ERROR_HIDPP20, ERROR_HIDPP10) and report[3] == feature_index and report[4] == fn_sw:
        raise HidppError(report[5], hidpp10=report[2] == ERROR_HIDPP10)
    return None


class HidrawChannel:
    """Request/response over one hidraw node, using a fixed software id."""

    def __init__(self, path, sw_id):
        self.path = Path(path)
        self.sw_id = sw_id
        self.fd = None
        self._waiters = []

    @property
    def is_open(self):
        return self.fd is not None

    def open(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        asyncio.get_running_loop().add_reader(self.fd, self._on_readable)

    def close(self):
        if self.fd is None:
            return
        try:
            asyncio.get_running_loop().remove_reader(self.fd)
        except RuntimeError:
            pass
        os.close(self.fd)
        self.fd = None

    def _fail(self, error):
        for *_, future in self._waiters:
            if not future.done():
                future.set_exception(error)
        self.close()

    def _on_readable(self):
        while self.fd is not None:
            try:
                report = os.read(self.fd, 64)
            except BlockingIOError:
                return
            except OSError as e:
                self._fail(e)
                return
            if not report:
                self._fail(OSError(errno.ENODEV, "device disconnected"))
                return
            for device_index, feature_index, fn_sw, future in self._waiters:
                if future.done():
                    continue
                try:
                    payload = match_response(report, device_index, feature_index, fn_sw)
                except HidppError as e:
                    future.set_exception(e)
                    break
                if payload is not None:
                    future.set_result(payload)
                    break

    async def request(self, device_index, feature_index, function, params=b"", timeout=PROBE_TIMEOUT):
        if self.fd is None:
            raise OSError(errno.EBADF, "channel is closed")
        fn_sw = (function << 4) | self.sw_id
        future = asyncio.get_running_loop().create_future()
        waiter = (device_index, feature_index, fn_sw, future)
        self._waiters.append(waiter)
        try:
            try:
                os.write(self.fd, build_request(device_index, feature_index, function, self.sw_id, params))
            except OSError:
                self.close()
                raise
            return await asyncio.wait_for(future, timeout)
        finally:
            self._waiters.remove(waiter)


class HidppBackend:
    name = "hidpp"

    def __init__(self, settings=None):
        settings = settings or {}
        self.device = settings.get("device", "auto")
        self.sw_id = int(settings.get("sw_id", 15))
        if not 1 <= self.sw_id <= 15:
            raise ValueError(f"hidpp.sw_id must be in 1..15, got {self.sw_id}")
        self.ensure_enabled = bool(settings.get("ensure_enabled", False))
        self.intensity = int(settings.get("intensity", 60))
        self.channel = None
        self.info = None
        self.device_index = None
        self.feature_index = None
        self._failures = 0
        self._next_retry = 0.0

    def _candidates(self):
        nodes = list_hidraw()
        if self.device in (None, "", "auto"):
            return nodes
        if str(self.device).startswith("/dev/"):
            return [n for n in nodes if str(n.path) == self.device] or [
                HidrawInfo(Path(self.device), self.device, 0, 0)]
        return [n for n in nodes if n.name.lower() == str(self.device).lower()]

    async def _probe(self, channel, info):
        """Returns (device_index, feature_index) of the haptic feature, or None."""
        indexes = [DEVICE_DIRECT]
        if info.bus == BUS_USB:
            indexes += list(RECEIVER_SLOTS)
        for device_index in indexes:
            try:
                payload = await channel.request(
                    device_index, FEATURE_ROOT_INDEX, 0, FEATURE_HAPTIC.to_bytes(2, "big"))
            except (HidppError, asyncio.TimeoutError) as e:
                logging.debug("%s index 0x%02x: no answer (%s)", info.path, device_index, e or "timeout")
                continue
            if payload[0]:
                return device_index, payload[0]
            logging.debug("%s index 0x%02x has no haptic feature", info.path, device_index)
        return None

    async def connect(self):
        """Finds a device with haptics and opens it; raises DeviceNotFound."""
        self.close_channel()
        candidates = self._candidates()
        if not candidates:
            raise DeviceNotFound(f"no Logitech HID++ hidraw device matches '{self.device}'")
        for info in candidates:
            channel = HidrawChannel(info.path, self.sw_id)
            try:
                channel.open()
            except PermissionError:
                logging.warning("No access to %s (%s). Install OpenLogi's or HyprLogi's udev rule "
                                "(contrib/70-hyprlogi.rules).", info.path, info.name)
                continue
            except OSError as e:
                logging.debug("Cannot open %s: %s", info.path, e)
                continue
            try:
                found = await self._probe(channel, info)
            except OSError as e:
                logging.debug("%s failed while probing: %s", info.path, e)
                found = None
            if found:
                self.channel, self.info = channel, info
                self.device_index, self.feature_index = found
                logging.info("Haptics on %s (%s), device index 0x%02x, feature index %d",
                             info.path, info.name, *found)
                if self.ensure_enabled:
                    await self._ensure_enabled()
                return
            channel.close()
        raise DeviceNotFound("no connected Logitech device supports haptic feedback (0x19B0)")

    async def _ensure_enabled(self):
        enabled, intensity = await self.configuration()
        if not enabled or intensity == 0:
            logging.info("Haptics are disabled on the device, enabling at %d%%", self.intensity)
            await self._call(FN_SET_CONFIGURATION, bytes([1, max(1, min(self.intensity, 100)), 0]))

    async def _call(self, function, params=b"", timeout=PROBE_TIMEOUT):
        return await self.channel.request(self.device_index, self.feature_index, function, params, timeout)

    async def capabilities(self):
        """Returns the waveform ids the device reports as playable."""
        payload = await self._call(FN_GET_CAPABILITIES)
        mask = int.from_bytes(payload[4:8], "big")
        return [bit for bit in range(32) if mask & (1 << bit)]

    async def configuration(self):
        """Returns (enabled, intensity percent)."""
        payload = await self._call(FN_GET_CONFIGURATION)
        return bool(payload[0]), payload[1]

    async def open(self, strict=False):
        try:
            await self.connect()
        except DeviceNotFound as e:
            if strict:
                raise
            logging.warning("%s; will retry when an effect is played", e)
            self._schedule_retry()

    def _schedule_retry(self):
        delay = RETRY_DELAYS[min(self._failures, len(RETRY_DELAYS) - 1)]
        self._failures += 1
        self._next_retry = time.monotonic() + delay

    async def _ensure_connected(self):
        if self.channel and self.channel.is_open:
            return True
        if time.monotonic() < self._next_retry:
            return False
        try:
            await self.connect()
        except DeviceNotFound as e:
            logging.debug("%s", e)
            self._schedule_retry()
            return False
        self._failures = 0
        return True

    async def play(self, effect):
        # One retry: the hidraw node is recreated when a sleeping mouse wakes up
        for attempt in range(2):
            if not await self._ensure_connected():
                return
            try:
                await self._call(FN_PLAY_WAVEFORM, bytes([effect & 0xFF]), timeout=PLAY_TIMEOUT)
                return
            except asyncio.TimeoutError:
                logging.debug("Effect %d sent, no acknowledgement", effect)
                return
            except HidppError as e:
                logging.warning("Device rejected effect %d: %s", effect, e)
                return
            except OSError as e:
                logging.info("Lost %s (%s), reconnecting", self.info.path if self.info else "device", e)
                self.close_channel()
                self._next_retry = 0.0

    def close_channel(self):
        if self.channel:
            self.channel.close()
        self.channel = None

    async def close(self):
        self.close_channel()
