import json

import pytest

from hyprlogi import config
from hyprlogi.gui import model


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    (tmp_path / "profiles").mkdir()
    (tmp_path / "settings.json").write_text(json.dumps({"active_profile": "main.json", "backend": "hidpp"}))
    (tmp_path / "profiles" / "main.json").write_text(json.dumps({
        "comment": "kept",
        "default_effect": None,
        "events": {"workspace": 5, "openlayer": {"default": 1, "args": {"omarchy-menu": 2}}},
    }))
    return tmp_path


def test_round_trip_keeps_unknown_keys_and_order(home):
    doc = model.ProfileDoc.load(model.profile_path("main"))
    doc.add_event("closewindow", 11)
    doc.save()
    data = json.loads(model.profile_path("main").read_text())
    assert data["comment"] == "kept"
    assert list(data["events"]) == ["workspace", "openlayer", "closewindow"]
    assert not doc.dirty


def test_event_and_argument_rules(home):
    doc = model.ProfileDoc.load(model.profile_path("main"))
    assert doc.event_effect("openlayer") == 1 and doc.arg_rules("openlayer") == {"omarchy-menu": 2}

    doc.set_arg_effect("workspace", "1", 7)
    assert doc.events["workspace"] == {"default": 5, "args": {"1": 7}}
    doc.set_event_effect("workspace", 6)
    assert doc.event_effect("workspace") == 6
    doc.remove_arg("workspace", "1")
    assert doc.events["workspace"] == 6
    assert doc.add_event("workspace") is False
    doc.remove_event("openlayer")
    assert doc.event_names() == ["workspace"]
    assert doc.dirty


def test_resolve_effect_agrees_with_the_editor(home):
    doc = model.ProfileDoc.load(model.profile_path("main"))
    doc.set_arg_effect("activewindow", "kitty", 3)
    assert config.resolve_effect(doc.data, "activewindow", ["kitty", "~"]) == 3
    assert config.resolve_effect(doc.data, "activewindow", ["firefox", "x"]) is None


def test_validation(home):
    doc = model.ProfileDoc.load(model.profile_path("main"))
    with pytest.raises(ValueError):
        doc.set_event_effect("workspace", 300)
    with pytest.raises(ValueError):
        doc.add_event("open window")
    doc.events["bad"] = "x"
    assert doc.validate() and "bad" in doc.validate()[0]
    with pytest.raises(ValueError):
        doc.save()


def test_profile_files(home):
    assert model.profile_names() == ["main"]
    assert model.new_profile("quiet") == "quiet"
    assert json.loads(model.profile_path("quiet").read_text()) == model.EMPTY_PROFILE
    model.duplicate_profile("main", "main-copy")
    assert json.loads(model.profile_path("main-copy").read_text())["comment"] == "kept"
    with pytest.raises(ValueError):
        model.new_profile("quiet")
    with pytest.raises(ValueError):
        model.new_profile("../evil")

    # Renaming the active profile follows it in settings.json, other settings stay
    model.rename_profile("main", "daily")
    settings = json.loads((home / "settings.json").read_text())
    assert settings == {"active_profile": "daily.json", "backend": "hidpp"}
    with pytest.raises(ValueError):
        model.delete_profile("daily")
    model.delete_profile("quiet")
    assert model.profile_names() == ["daily", "main-copy"]


def test_effect_names_and_labels(home):
    config.set_effect_name(4, "Soft tick")
    config.set_effect_name(7, "  ")
    assert config.effect_names() == {4: "Soft tick"}
    assert json.loads((home / "settings.json").read_text())["backend"] == "hidpp"
    assert model.effect_label(4, config.effect_names()) == "4 · Soft tick"
    assert model.effect_label(5, {}) == "5" and model.effect_label(None, {}) == "None"
    config.set_effect_name(4, "")
    assert config.effect_names() == {}


def test_catalog_names_are_valid():
    for event in model.EVENT_CATALOG:
        assert model.check_event_name(event.name) == event.name
