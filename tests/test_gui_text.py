from hyprlogi.gui.text import PASTE_LIMIT, clean_pasted_text


def test_clean_pasted_text_makes_one_printable_line():
    assert clean_pasted_text(b"  omarchy-notifications\n") == "omarchy-notifications"
    assert clean_pasted_text(b"two\nlines\r\n\tand\x00nul\x1b[31m") == "two lines and nul [31m"
    assert clean_pasted_text("Mekani klik".encode()) == "Mekani klik"
    assert clean_pasted_text(b"\xff\xfeok") == "��ok"


def test_clean_pasted_text_never_exceeds_the_limit():
    assert len(clean_pasted_text(b"x" * (PASTE_LIMIT * 4))) == PASTE_LIMIT
