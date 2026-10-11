import asyncio
import json

import pytest

from hyprlogi import config, control, main


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "hyprlogi")
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "hyprlogi" / "profiles")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "hyprlogi" / "settings.json")
    monkeypatch.setattr(control, "PAUSED_FLAG", tmp_path / "paused")
    return tmp_path


def fake_daemon(monkeypatch, reply):
    async def request(req, socket_path=None):
        assert req == {"cmd": "status"}
        return reply
    monkeypatch.setattr(control, "request", request)


def run_cli(argv, capsys):
    args = main.parse_args(argv)
    asyncio.run(main.main(args))
    return capsys.readouterr().out


def test_status_json_when_daemon_is_not_running(home, monkeypatch, capsys):
    fake_daemon(monkeypatch, None)
    data = json.loads(run_cli(["status", "--json"], capsys))
    assert data["running"] is False and data["paused"] is False
    assert data["profile"] == "default"
    assert "omarchy" in data["profiles"] and "default" in data["profiles"]
    assert "backend" not in data


def test_status_json_when_daemon_runs(home, monkeypatch, capsys):
    fake_daemon(monkeypatch, {"ok": True, "profile": "subtle.json", "backend": "hidpp",
                              "device": "MX Master 4 (/dev/hidraw1)", "paused": True})
    control.set_paused(True)
    data = json.loads(run_cli(["--debug", "status", "--json"], capsys))
    assert data["running"] is True and data["paused"] is True
    assert (data["profile"], data["backend"]) == ("subtle", "hidpp")


def test_status_text_exits_3_when_not_running(home, monkeypatch, capsys):
    fake_daemon(monkeypatch, None)
    with pytest.raises(SystemExit) as e:
        run_cli(["status"], capsys)
    assert e.value.code == 3


def test_profile_list_json(home, capsys):
    data = json.loads(run_cli(["profile", "list", "--json"], capsys))
    assert data["active"] == "default"
    assert data["profiles"] == sorted(data["profiles"])


def test_options_after_the_command():
    args = main.parse_args(["omarchy", "install", "--plugin", "--no-hooks", "--debug"])
    assert (args.action, args.plugin, args.no_hooks, args.debug) == ("install", True, True, True)
    assert main.parse_args([]).command == "daemon"
