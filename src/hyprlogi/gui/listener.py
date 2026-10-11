# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reads Hyprland's event socket on the GTK main loop."""

from gi.repository import Gio, GLib

from hyprlogi.hyprland import find_socket, parse_event


class EventListener:
    """Calls on_event(name, args) for each Hyprland event while running."""

    def __init__(self, on_event, on_stopped):
        self.on_event = on_event
        self.on_stopped = on_stopped
        self.connection = None
        self.stream = None
        self.cancellable = None

    @property
    def running(self):
        return self.connection is not None

    def start(self):
        """Connects to Hyprland; raises RuntimeError when that fails."""
        path = find_socket()
        if path is None:
            raise RuntimeError("Hyprland's event socket was not found")
        try:
            self.connection = Gio.SocketClient().connect(Gio.UnixSocketAddress.new(str(path)), None)
        except GLib.Error as e:
            raise RuntimeError(f"Cannot connect to Hyprland: {e.message}") from e
        self.stream = Gio.DataInputStream.new(self.connection.get_input_stream())
        self.cancellable = Gio.Cancellable()
        self._read_next()

    def stop(self):
        if self.cancellable:
            self.cancellable.cancel()
        if self.connection:
            self.connection.close(None)
        self.connection = self.stream = self.cancellable = None

    def _read_next(self):
        self.stream.read_line_async(GLib.PRIORITY_DEFAULT, self.cancellable, self._on_line)

    def _on_line(self, stream, result):
        try:
            line, _length = stream.read_line_finish_utf8(result)
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.stop()
                self.on_stopped(f"Hyprland connection lost: {e.message}")
            return
        if line is None:
            self.stop()
            self.on_stopped("Hyprland closed the event socket")
            return
        event = parse_event(line)
        if event:
            self.on_event(*event)
        if self.stream is not None:
            self._read_next()
