import asyncio

from hyprlogi.config import _merge, DEFAULT_SETTINGS, resolve_effect
from hyprlogi.dispatcher import Dispatcher
from hyprlogi.hyprland import parse_event

PROFILE = {
    "default_effect": None,
    "events": {
        "activewindowv2": 2,
        "workspace": 5,
        "openlayer": {"default": 1, "args": {"swaync-notification-window": 15}},
    },
}


def test_parse_event_strips_separator():
    assert parse_event("openlayer>>swaync-notification-window") == ("openlayer", ["swaync-notification-window"])
    assert parse_event("openwindow>>5c2d140,1,kitty,") == ("openwindow", ["5c2d140", "1", "kitty", ""])
    assert parse_event("garbage") is None


def test_resolve_effect():
    assert resolve_effect(PROFILE, "workspace", ["1"]) == 5
    assert resolve_effect(PROFILE, "openlayer", ["swaync-notification-window"]) == 15
    assert resolve_effect(PROFILE, "openlayer", ["waybar"]) == 1
    assert resolve_effect(PROFILE, "closewindow", ["x"]) is None
    assert resolve_effect({**PROFILE, "default_effect": 3}, "closewindow", ["x"]) == 3


def test_settings_merge_keeps_nested_defaults():
    merged = _merge(DEFAULT_SETTINGS, {"backend": "hidpp", "hidpp": {"sw_id": 14}})
    assert merged["backend"] == "hidpp"
    assert merged["hidpp"]["sw_id"] == 14 and merged["hidpp"]["device"] == "auto"
    assert DEFAULT_SETTINGS["hidpp"]["sw_id"] == 15


class Recorder:
    def __init__(self):
        self.played = []

    async def play(self, effect):
        self.played.append(effect)


def test_burst_plays_only_first_effect_and_dedupes_focus():
    now = [0.0]
    backend = Recorder()
    d = Dispatcher(backend, PROFILE, cooldown_ms=40, clock=lambda: now[0])

    async def scenario():
        # One workspace switch: workspace, then focus change 10 ms later
        await d.handle("workspace", ["2"])
        now[0] += 0.01
        await d.handle("activewindowv2", ["abc"])
        # Later focus change on the same window is ignored, a new one plays
        now[0] += 1
        await d.handle("activewindowv2", ["abc"])
        await d.handle("activewindowv2", ["def"])

    asyncio.run(scenario())
    assert backend.played == [5, 2]


def test_paused_dispatcher_plays_nothing():
    backend = Recorder()
    d = Dispatcher(backend, PROFILE, paused=lambda: True)
    asyncio.run(d.handle("workspace", ["1"]))
    assert backend.played == []
