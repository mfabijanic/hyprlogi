import asyncio
import os
import socket

import pytest

from hyprlogi.backends import hidpp
from hyprlogi.backends.hidpp import (
    HidppBackend, HidppError, HidrawChannel, build_request, list_hidraw, match_response,
)

SW = 15


def test_build_request_is_a_long_report():
    frame = build_request(0xFF, 0x00, 0, SW, b"\x19\xb0")
    assert len(frame) == 20
    assert frame[:6] == bytes([0x11, 0xFF, 0x00, 0x0F, 0x19, 0xB0])
    assert frame[6:] == bytes(14)


def test_play_waveform_frame():
    assert build_request(0xFF, 0x0B, 4, SW, b"\x05")[:5] == bytes([0x11, 0xFF, 0x0B, 0x4F, 0x05])


def test_match_response_payload():
    report = bytes([0x11, 0xFF, 0x0B, 0x4F, 0xAA]) + bytes(15)
    assert match_response(report, 0xFF, 0x0B, 0x4F)[0] == 0xAA


def test_match_response_ignores_other_software_ids_and_devices():
    # OpenLogi's agent uses software id 1: its replies are not ours
    assert match_response(bytes([0x11, 0xFF, 0x0B, 0x41]) + bytes(16), 0xFF, 0x0B, 0x4F) is None
    assert match_response(bytes([0x11, 0x01, 0x0B, 0x4F]) + bytes(16), 0xFF, 0x0B, 0x4F) is None
    # Device notifications carry software id 0
    assert match_response(bytes([0x11, 0xFF, 0x0B, 0x00]) + bytes(16), 0xFF, 0x0B, 0x4F) is None


def test_match_response_errors():
    with pytest.raises(HidppError) as e:
        match_response(bytes([0x11, 0xFF, 0xFF, 0x0B, 0x4F, 0x02]) + bytes(14), 0xFF, 0x0B, 0x4F)
    assert e.value.code == 2 and not e.value.hidpp10
    with pytest.raises(HidppError) as e:
        match_response(bytes([0x10, 0xFF, 0x8F, 0x00, 0x0F, 0x01, 0x00]), 0xFF, 0x00, 0x0F)
    assert e.value.hidpp10


def _fake_sysfs(root, name, hid_id, hid_name, descriptor):
    device = root / name / "device"
    device.mkdir(parents=True)
    (device / "uevent").write_text(f"HID_ID={hid_id}\nHID_NAME={hid_name}\n")
    (device / "report_descriptor").write_bytes(descriptor)


def test_list_hidraw_filters_logitech_hidpp(tmp_path):
    hidpp_desc = b"\x05\x01\x06\x43\xff\x09\x02\x85\x11\x95\x13"  # BLE: page 0xFF43
    _fake_sysfs(tmp_path, "hidraw0", "0018:000004F3:0000320D", "ELAN touchpad", hidpp_desc)
    _fake_sysfs(tmp_path, "hidraw1", "0005:0000046D:0000B042", "Logitech MX Master 4", hidpp_desc)
    _fake_sysfs(tmp_path, "hidraw2", "0003:0000046D:0000C548", "Logitech keyboard iface", b"\x05\x01\x09\x06")
    nodes = list_hidraw(tmp_path)
    assert [(n.path.name, n.bus, n.product) for n in nodes] == [("hidraw1", 0x05, 0xB042)]


class FakeDevice:
    """The device side of a socketpair standing in for /dev/hidrawN."""

    def __init__(self, feature_index=0x0B, waveform_mask=0b10010):
        self.sock, self.host = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        # hidraw is opened O_NONBLOCK
        self.host.setblocking(False)
        self.feature_index = feature_index
        self.waveform_mask = waveform_mask
        self.played = []

    def serve(self):
        loop = asyncio.get_running_loop()
        loop.add_reader(self.sock.fileno(), self._on_request)

    def _on_request(self):
        req = self.sock.recv(64)
        dev, feat, fn_sw = req[1], req[2], req[3]
        fn = fn_sw >> 4
        # Interleave a foreign reply with the same feature/function (another client)
        self.sock.send(bytes([0x11, dev, feat, (fn << 4) | 1]) + bytes(16))
        if feat == 0 and fn == 0:
            index = self.feature_index if req[4:6] == b"\x19\xb0" else 0
            reply = bytes([index])
        elif feat == self.feature_index and fn == 0:
            reply = bytes(4) + self.waveform_mask.to_bytes(4, "big")
        elif feat == self.feature_index and fn == 1:
            reply = bytes([1, 60, 0])
        elif feat == self.feature_index and fn == 4:
            self.played.append(req[4])
            reply = b""
        else:
            self.sock.send(bytes([0x11, dev, 0xFF, feat, fn_sw, 7]) + bytes(14))
            return
        self.sock.send((bytes([0x11, dev, feat, fn_sw]) + reply).ljust(20, b"\x00"))


@pytest.fixture
def fake(monkeypatch):
    device = FakeDevice()
    info = hidpp.HidrawInfo(path="fake", name="MX Master 4", bus=0x05, product=0xB042)
    monkeypatch.setattr(hidpp, "list_hidraw", lambda: [info])

    def fake_open(channel):
        channel.fd = os.dup(device.host.fileno())
        asyncio.get_running_loop().add_reader(channel.fd, channel._on_readable)

    monkeypatch.setattr(HidrawChannel, "open", fake_open)
    return device


def test_backend_probes_and_plays(fake):
    async def scenario():
        fake.serve()
        backend = HidppBackend({"sw_id": SW})
        await backend.open(strict=True)
        assert (backend.device_index, backend.feature_index) == (0xFF, 0x0B)
        assert await backend.capabilities() == [1, 4]
        assert await backend.configuration() == (True, 60)
        await backend.play(5)
        await backend.play(1)
        await backend.close()

    asyncio.run(scenario())
    assert fake.played == [5, 1]


def test_backend_without_haptic_feature(fake):
    fake.feature_index = 0

    async def scenario():
        fake.serve()
        backend = HidppBackend()
        with pytest.raises(hidpp.DeviceNotFound):
            await backend.open(strict=True)
        # Non-strict open must not raise, and play is a no-op until the device shows up
        await backend.open()
        await backend.play(1)

    asyncio.run(scenario())
    assert fake.played == []


def test_sw_id_must_be_valid():
    with pytest.raises(ValueError):
        HidppBackend({"sw_id": 0})
