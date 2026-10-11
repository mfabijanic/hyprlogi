# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded text input for the profile editor.

GTK's own paste (Ctrl+V, Shift+Insert, the context menu), middle-click paste
and text drops read whatever another client offers into memory, without a
size limit or a deadline. Every editor field only takes short text (profile,
event and effect names, an event argument), so `harden()` replaces paste
with a read capped at PASTE_LIMIT bytes and PASTE_TIMEOUT_MS, and turns
primary paste and text drops off.
"""

from gi.repository import Gio, GLib, Gtk

from hyprlogi.gui.text import PASTE_LIMIT, clean_pasted_text

PASTE_TIMEOUT_MS = 2000
READ_CHUNK = 512
TEXT_MIME_TYPES = ["text/plain;charset=utf-8", "text/plain"]


def disable_primary_paste(display=None):
    """Middle-click pastes the primary selection through GTK's unbounded reader; turn it off."""
    settings = Gtk.Settings.get_for_display(display) if display else Gtk.Settings.get_default()
    if settings is not None:
        settings.set_property("gtk-enable-primary-paste", False)


def _texts(widget):
    if isinstance(widget, Gtk.Text):
        yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _texts(child)
        child = child.get_next_sibling()


def harden(widget, on_rejected=None):
    """Bounds paste and removes text drops in every Gtk.Text inside widget (Entry, EntryRow...).

    on_rejected(message) is called when a paste is refused (too large, too slow, not text).
    """
    for text in _texts(widget):
        if getattr(text, "_hyprlogi_hardened", False):
            continue
        text._hyprlogi_hardened = True
        text.connect("paste-clipboard", _on_paste_clipboard, on_rejected)
        controllers = text.observe_controllers()
        drops = [controllers.get_item(i) for i in range(controllers.get_n_items())
                 if isinstance(controllers.get_item(i), Gtk.DropTarget)]
        for drop in drops:
            text.remove_controller(drop)
    return widget


def _on_paste_clipboard(text, on_rejected):
    # Keyboard shortcuts and the context menu's Paste both emit this signal
    text.stop_emission_by_name("paste-clipboard")
    if text.get_editable():
        BoundedPaste(text, text.get_clipboard(), on_rejected).start()


class BoundedPaste:
    """Reads at most PASTE_LIMIT bytes of text from a clipboard within PASTE_TIMEOUT_MS."""

    def __init__(self, text, clipboard, on_rejected=None):
        self.text = text
        self.clipboard = clipboard
        self.on_rejected = on_rejected
        self.cancellable = Gio.Cancellable()
        self.data = bytearray()
        self.stream = None
        self.timeout = 0
        self.failure = None

    def start(self):
        formats = self.clipboard.get_formats()
        if not any(formats.contain_mime_type(mime) for mime in TEXT_MIME_TYPES):
            return
        self.timeout = GLib.timeout_add(PASTE_TIMEOUT_MS, self._on_timeout)
        self.clipboard.read_async(TEXT_MIME_TYPES, GLib.PRIORITY_DEFAULT, self.cancellable, self._on_stream)

    def _on_timeout(self):
        self.timeout = 0
        self.failure = "The clipboard took too long to answer; nothing was pasted"
        self.cancellable.cancel()
        return GLib.SOURCE_REMOVE

    def _on_stream(self, clipboard, result):
        try:
            self.stream, _mime = clipboard.read_finish(result)
        except GLib.Error as e:
            self._finish(self.failure or f"Could not read the clipboard: {e.message}")
            return
        self._read_more()

    def _read_more(self):
        self.stream.read_bytes_async(READ_CHUNK, GLib.PRIORITY_DEFAULT, self.cancellable, self._on_bytes)

    def _on_bytes(self, stream, result):
        try:
            chunk = stream.read_bytes_finish(result).get_data()
        except GLib.Error as e:
            self._finish(self.failure or f"Could not read the clipboard: {e.message}")
            return
        if not chunk:
            self._finish()
            return
        self.data.extend(chunk)
        if len(self.data) > PASTE_LIMIT:
            self.cancellable.cancel()
            self._finish(f"The clipboard holds more than {PASTE_LIMIT} bytes; nothing was pasted")
            return
        self._read_more()

    def _finish(self, failure=None):
        if self.timeout:
            GLib.source_remove(self.timeout)
            self.timeout = 0
        if self.stream is not None:
            self.stream.close_async(GLib.PRIORITY_DEFAULT, None, None)
            self.stream = None
        if failure:
            if self.on_rejected:
                self.on_rejected(failure)
            return
        pasted = clean_pasted_text(bytes(self.data))
        if pasted:
            self._insert(pasted)

    def _insert(self, pasted):
        text = self.text
        text.delete_selection()
        position = text.get_position()
        position = text.insert_text(pasted, position)
        text.set_position(position)

