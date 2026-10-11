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


def test_daemon_waveforms_command():
    class Hidpp:
        name = "hidpp"

        async def _ensure_connected(self):
            return True

        async def capabilities(self):
            return [1, 4]

    class LogiOps:
        name = "logiops"

    daemon = main.Daemon(main.parse_args([]))
    daemon.settings = {}
    daemon.backend = Hidpp()
    assert asyncio.run(daemon.handle_request({"cmd": "waveforms"})) == {"ok": True, "waveforms": [1, 4]}
    daemon.backend = LogiOps()
    assert asyncio.run(daemon.handle_request({"cmd": "waveforms"})) == {"ok": True, "waveforms": None}


def test_gui_without_gtk_explains_what_to_install(monkeypatch, capsys):
    import sys
    from hyprlogi.gui import app
    monkeypatch.setitem(sys.modules, "gi", None)
    assert app.run([]) == 1
    assert "python-gobject gtk4 libadwaita" in capsys.readouterr().err


def test_status_json_reports_the_editor(home, monkeypatch, capsys):
    fake_daemon(monkeypatch, None)
    monkeypatch.setattr(main, "gui_available", lambda: False)
    assert json.loads(run_cli(["status", "--json"], capsys))["gui"] is False


def test_gui_available_needs_gi_and_both_typelibs(tmp_path, monkeypatch):
    from hyprlogi import gui
    monkeypatch.setattr(gui, "TYPELIB_DIRS", ())
    monkeypatch.setenv("GI_TYPELIB_PATH", str(tmp_path))
    monkeypatch.setattr(gui.importlib.util, "find_spec", lambda name: object())
    assert gui.available() is False
    (tmp_path / "Gtk-4.0.typelib").touch()
    assert gui.available() is False
    (tmp_path / "Adw-1.typelib").touch()
    assert gui.available() is True
    monkeypatch.setattr(gui.importlib.util, "find_spec", lambda name: None)
    assert gui.available() is False


def test_hyprlogi_gui_profile_option():
    from hyprlogi.gui import app
    assert app.parse_args(["--profile", "subtle"]).profile == "subtle"
    assert main.parse_args(["gui", "-p", "subtle"]).profile == "subtle"
