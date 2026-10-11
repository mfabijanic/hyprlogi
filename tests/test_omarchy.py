import json
import re

import pytest

from hyprlogi import omarchy

USER_MENU = """{
  // Extend the Quickshell Omarchy menu with JSONC.
  // "personal": {"icon":"","label":"Personal"},
  "about": {"icon":"","label":"About","action":"fastfetch"}
}
"""


def strip_jsonc(raw):
    # Same rules as the Omarchy shell (MenuModel.stripJsonc)
    raw = re.sub(r"^\s*//[^\n]*(\n|$)", "", raw, flags=re.M)
    return re.sub(r",(\s*[}\]])", r"\1", raw)


def parse(text):
    return json.loads(strip_jsonc(text))


@pytest.fixture
def block():
    entries = omarchy.menu_entries(["default.json", "omarchy.json", "my_profile.json"], "/usr/bin/hyprlogi")
    return omarchy.render_menu_block(entries)


def test_block_is_inserted_and_keeps_user_entries(block):
    menu = parse(omarchy.update_menu_text(USER_MENU, block))
    assert menu["about"]["action"] == "fastfetch"
    assert menu["setup.hyprlogi"]["label"] == "Haptics"
    assert menu["setup.hyprlogi"]["description"] == "HyprLogi"
    assert menu["setup.hyprlogi.paused"]["action"] == "/usr/bin/hyprlogi toggle"
    item = menu["setup.hyprlogi.profile.my-profile"]
    assert item["label"] == "My Profile"
    assert item["action"] == "/usr/bin/hyprlogi profile set my_profile"
    assert r'"my_profile\.json"' in item["checked"]


def test_reinstall_replaces_and_uninstall_restores(block):
    once = omarchy.update_menu_text(USER_MENU, block)
    assert omarchy.update_menu_text(once, block) == once
    assert omarchy.update_menu_text(once, None) == USER_MENU


def test_empty_or_missing_menu(block):
    assert "setup.hyprlogi.test" in parse(omarchy.update_menu_text("", block))


def test_profile_checked_condition_matches_settings_file(block, tmp_path):
    import subprocess
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"active_profile": "omarchy.json"}, indent=2))
    menu = parse(omarchy.update_menu_text("", block))
    for key, expected in (("omarchy", 0), ("default", 1)):
        cond = menu[f"setup.hyprlogi.profile.{key}"]["checked"].replace('"$HOME/.config/hyprlogi/settings.json"', str(settings))
        assert subprocess.run(["bash", "-c", cond]).returncode == expected
