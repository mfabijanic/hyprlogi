# HyprLogi - Haptic Feedback for Hyprland Events
# Copyright (C) 2025 mfabijanic
# SPDX-License-Identifier: GPL-3.0-or-later
"""The profile editor window: profiles on the left; Profile, Effects and Listen pages on the right."""

from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from hyprlogi import config
from hyprlogi.gui import daemon, model
from hyprlogi.gui.listener import EventListener

STATUS_INTERVAL_SEC = 5
LISTEN_LIMIT = 50


def _message(result):
    """daemon.play reports a message, or a dict when the worker itself failed."""
    return result.get("error") if isinstance(result, dict) else result


class EffectChoices:
    """The options of an effect drop-down: None, then the device's waveforms (with names)."""

    def __init__(self, waveforms, names, extra=()):
        self.values = [None] + sorted(set(waveforms) | {e for e in extra if e is not None})
        self.labels = [model.effect_label(v, names) if v is None or v in waveforms
                       else f"{v} (not reported by the device)" for v in self.values]

    def model(self):
        return Gtk.StringList.new(self.labels)

    def index(self, effect):
        return self.values.index(effect) if effect in self.values else 0


class Window(Adw.ApplicationWindow):
    def __init__(self, profile=None, **kwargs):
        super().__init__(title="HyprLogi", default_width=1000, default_height=720, **kwargs)
        config.setup_default_config()
        self.doc = None
        self.waveforms = list(model.FALLBACK_WAVEFORMS)
        self.names = config.effect_names()
        self.expanded = set()
        self.captured = {}             # (event, first argument) -> [count, args]
        self.listener = EventListener(self._on_event, self._on_listener_stopped)

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)
        split = Adw.NavigationSplitView(min_sidebar_width=220, max_sidebar_width=280)
        self.toasts.set_child(split)
        split.set_sidebar(self._build_sidebar())
        split.set_content(self._build_content())

        self.connect("close-request", self._on_close_request)
        start = model.active_profile_name()
        if profile:
            profile = profile.removesuffix(".json")
            if profile in model.profile_names():
                start = profile
            else:
                GLib.idle_add(self._toast, f"No profile named “{profile}”; showing the active one")
        self._reload_profiles(select=start)
        self._refresh_status()
        GLib.timeout_add_seconds(STATUS_INTERVAL_SEC, self._refresh_status)
        daemon.call({"cmd": "waveforms"}, self._on_waveforms)

    # Layout

    def _build_sidebar(self):
        self.profile_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.profile_list.connect("row-selected", self._on_profile_selected)
        scroller = Gtk.ScrolledWindow(child=self.profile_list, vexpand=True)

        actions = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6,
                          halign=Gtk.Align.CENTER)
        for icon, tooltip, handler in (
            ("list-add-symbolic", "New profile", self._on_new_profile),
            ("edit-copy-symbolic", "Duplicate profile", self._on_duplicate_profile),
            ("document-edit-symbolic", "Rename profile", self._on_rename_profile),
            ("user-trash-symbolic", "Delete profile", self._on_delete_profile),
        ):
            button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, css_classes=["flat"])
            button.connect("clicked", handler)
            actions.append(button)
        self.activate_button = Gtk.Button(label="Make active", css_classes=["pill"], margin_start=12,
                                          margin_end=12, margin_bottom=12,
                                          tooltip_text="The daemon switches to this profile at once")
        self.activate_button.connect("clicked", self._on_activate_profile)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(scroller)
        box.append(actions)
        box.append(self.activate_button)
        view = Adw.ToolbarView(content=box)
        view.add_top_bar(Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Profiles")))
        return Adw.NavigationPage(title="Profiles", child=view)

    def _build_content(self):
        self.stack = Adw.ViewStack()
        self.editor = Adw.PreferencesPage()
        self.editor_groups = []
        self.stack.add_titled_with_icon(self.editor, "profile", "Profile", "document-properties-symbolic")
        self.effects_page = Adw.PreferencesPage()
        self.effects_group = None
        self.stack.add_titled_with_icon(self.effects_page, "effects", "Effects", "media-playback-start-symbolic")
        self.stack.add_titled_with_icon(self._build_listen_page(), "listen", "Listen", "audio-input-microphone-symbolic")

        self.title = Adw.WindowTitle(title="HyprLogi")
        header = Adw.HeaderBar(title_widget=Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        # Shown only with unsaved changes: themes do not always dim an insensitive suggested button
        self.save_button = Gtk.Button(label="Save", css_classes=["suggested-action"], visible=False)
        self.save_button.connect("clicked", lambda *_: self._save())
        header.pack_end(self.save_button)
        self.status_label = Gtk.Label(css_classes=["dim-label", "caption"])
        header.pack_start(self.status_label)

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        return Adw.NavigationPage(title="HyprLogi", child=view)

    def _build_listen_page(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Hyprland events",
            description="Turn listening on, then do something in Hyprland: switch a workspace, open a "
                        "notification or a menu. Add what shows up to the profile, as an event or as a "
                        "rule for its first argument.")
        self.listen_switch = Adw.SwitchRow(title="Listen")
        self.listen_switch.connect("notify::active", self._on_listen_toggled)
        group.add(self.listen_switch)
        clear = Gtk.Button(label="Clear", css_classes=["flat"], valign=Gtk.Align.CENTER)
        clear.connect("clicked", lambda *_: self._clear_captured())
        group.set_header_suffix(clear)
        page.add(group)
        self.captured_group = Adw.PreferencesGroup(title="Captured")
        self.captured_rows = []
        page.add(self.captured_group)
        return page

    # Profiles

    def _reload_profiles(self, select=None):
        self._loading_profiles = True
        while (row := self.profile_list.get_first_child()) is not None:
            self.profile_list.remove(row)
        active = model.active_profile_name()
        selected = None
        for name in model.profile_names():
            row = Adw.ActionRow(title=name)
            row.profile = name
            if name == active:
                row.add_suffix(Gtk.Image(icon_name="object-select-symbolic", tooltip_text="Active profile"))
                row.set_subtitle("active")
            self.profile_list.append(row)
            if name == select:
                selected = row
        self._loading_profiles = False
        if selected is None:
            selected = self.profile_list.get_row_at_index(0)
        if selected:
            self.profile_list.select_row(selected)

    def _on_profile_selected(self, _list, row):
        if getattr(self, "_loading_profiles", False) or row is None:
            return
        name = getattr(row, "profile", None)
        if self.doc and name == self.doc.name:
            return
        self._confirm_discard(lambda: self._open_profile(name),
                              lambda: self._select_row_for(self.doc.name if self.doc else None))

    def _select_row_for(self, name):
        self._loading_profiles = True
        child = self.profile_list.get_first_child()
        while child is not None:
            if getattr(child, "profile", None) == name:
                self.profile_list.select_row(child)
            child = child.get_next_sibling()
        self._loading_profiles = False

    def _open_profile(self, name):
        try:
            self.doc = model.ProfileDoc.load(model.profile_path(name))
        except (OSError, ValueError) as e:
            self.doc = None
            self._toast(str(e))
        self.expanded = set()
        self._build_editor()
        self._update_dirty()
        self.activate_button.set_sensitive(bool(self.doc) and self.doc.name != model.active_profile_name())

    def _ask_name(self, heading, initial, on_name):
        dialog = Adw.AlertDialog(heading=heading)
        entry = Gtk.Entry(text=initial, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "OK")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")

        def on_response(_dialog, response):
            if response != "ok":
                return
            try:
                on_name(entry.get_text())
            except (OSError, ValueError) as e:
                self._toast(str(e))
        dialog.connect("response", on_response)
        dialog.present(self)

    def _on_new_profile(self, *_):
        def create(name):
            name = model.new_profile(name)
            self._confirm_discard(lambda: self._reload_profiles(select=name))
        self._ask_name("New profile", "", create)

    def _on_duplicate_profile(self, *_):
        source = self._current_name()
        if source is None:
            return
        def duplicate(name):
            name = model.duplicate_profile(source, name)
            self._confirm_discard(lambda: self._reload_profiles(select=name))
        self._ask_name(f"Duplicate “{source}”", f"{source}-copy", duplicate)

    def _on_rename_profile(self, *_):
        source = self._current_name()
        if source is None:
            return
        def rename(name):
            def go():
                new = model.rename_profile(source, name)
                self.doc = None
                self._reload_profiles(select=new)
                if new == model.active_profile_name():
                    self._reload_daemon()
            self._confirm_discard(go)
        self._ask_name(f"Rename “{source}”", source, rename)

    def _on_delete_profile(self, *_):
        name = self._current_name()
        if name is None:
            return
        if name == model.active_profile_name():
            self._toast("The active profile cannot be deleted; make another one active first")
            return
        dialog = Adw.AlertDialog(heading=f"Delete “{name}”?", body="The profile file is removed.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_dialog, response):
            if response == "delete":
                model.delete_profile(name)
                self.doc = None
                self._reload_profiles(select=model.active_profile_name())
        dialog.connect("response", on_response)
        dialog.present(self)

    def _on_activate_profile(self, *_):
        name = self._current_name()
        if name is None:
            return
        def go():
            config.set_active_profile(name)
            self._reload_profiles(select=name)
            self._reload_daemon(f"“{name}” is now the active profile")
        self._confirm_discard(go, save_first=True)

    def _current_name(self):
        return self.doc.name if self.doc else None

    # Unsaved changes

    def _confirm_discard(self, proceed, cancel=None, save_first=False):
        """Runs proceed() once the current profile has no unsaved changes."""
        if not (self.doc and self.doc.dirty):
            proceed()
            return
        dialog = Adw.AlertDialog(heading="Save changes?",
                                 body=f"“{self.doc.name}” has unsaved changes.")
        dialog.add_response("cancel", "Cancel")
        if not save_first:
            dialog.add_response("discard", "Discard")
            dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        def on_response(_dialog, response):
            if response == "save" and self._save():
                proceed()
            elif response == "discard":
                self.doc.dirty = False
                proceed()
            elif cancel:
                cancel()
        dialog.connect("response", on_response)
        dialog.present(self)

    def _on_close_request(self, *_):
        if self.doc and self.doc.dirty:
            self._confirm_discard(lambda: (self.listener.stop(), self.destroy()))
            return True
        self.listener.stop()
        return False

    def _update_dirty(self):
        dirty = bool(self.doc and self.doc.dirty)
        self.save_button.set_visible(dirty)
        name = self.doc.name if self.doc else "HyprLogi"
        self.set_title(f"{'• ' if dirty else ''}{name} — HyprLogi")

    def _changed(self):
        self._update_dirty()

    def _save(self):
        if not self.doc:
            return False
        try:
            self.doc.save()
        except (OSError, ValueError) as e:
            self._toast(f"Not saved: {e}")
            return False
        self._update_dirty()
        if self.doc.name == model.active_profile_name():
            self._reload_daemon(f"Saved “{self.doc.name}”; the daemon uses it now")
        else:
            self._toast(f"Saved “{self.doc.name}”")
        return True

    def _reload_daemon(self, message=None):
        def done(reply):
            if reply is None:
                self._toast((message + " (daemon not running)") if message else "Daemon not running")
            elif message:
                self._toast(message)
            self._refresh_status()
        daemon.call({"cmd": "reload"}, done)

    # Profile editor

    def _choices(self):
        extra = set()
        if self.doc:
            extra.add(self.doc.default_effect)
            for event in self.doc.event_names():
                extra.add(self.doc.event_effect(event))
                extra.update(self.doc.arg_rules(event).values())
        return EffectChoices(self.waveforms, self.names, extra)

    def _effect_row(self, title, subtitle, effect, on_change):
        choices = self._choices()
        row = Adw.ComboRow(title=title, subtitle=subtitle or "", model=choices.model(),
                           selected=choices.index(effect))
        row.connect("notify::selected", lambda r, _p: on_change(choices.values[r.get_selected()]))
        return row, choices

    def _play_button(self, effect_getter, tooltip="Play"):
        button = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text=tooltip,
                            valign=Gtk.Align.CENTER, css_classes=["flat"])

        def clicked(*_):
            effect = effect_getter()
            if effect is None:
                self._toast("No effect selected")
                return
            daemon.play([effect], lambda message: message and self._toast(_message(message)))
        button.connect("clicked", clicked)
        return button

    def _icon_button(self, icon, tooltip, handler):
        button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER, css_classes=["flat"])
        button.connect("clicked", lambda *_: handler())
        return button

    def _build_editor(self):
        for group in self.editor_groups:
            self.editor.remove(group)
        self.editor_groups = []
        if not self.doc:
            group = Adw.PreferencesGroup(title="No profile", description="Create a profile on the left.")
            self._add_group(group)
            return
        doc = self.doc

        path = str(doc.path).replace(str(Path.home()), "~", 1)
        general = Adw.PreferencesGroup(title=doc.name, description=GLib.markup_escape_text(path))
        row, _ = self._effect_row("Default effect", "For events this profile does not list",
                                  doc.default_effect, lambda e: (doc.set_default_effect(e), self._changed()))
        general.add(row)
        self._add_group(general)

        events = Adw.PreferencesGroup(
            title="Events",
            description="Each Hyprland event plays its effect. Rules match the event's first argument.")
        events.set_header_suffix(self._add_event_button())
        if not doc.event_names():
            events.add(Adw.ActionRow(title="No events yet",
                                     subtitle="Add one with the button above, or capture it on the Listen page"))
        for event in doc.event_names():
            events.add(self._event_row(event))
        self._add_group(events)

    def _add_group(self, group):
        self.editor.add(group)
        self.editor_groups.append(group)

    def _event_row(self, event):
        doc = self.doc
        info = model.CATALOG.get(event)
        expander = Adw.ExpanderRow(title=event, subtitle=info.description if info else "Custom event",
                                   expanded=event in self.expanded)
        expander.connect("notify::expanded",
                         lambda r, _p: (self.expanded.add if r.get_expanded() else self.expanded.discard)(event))
        expander.add_suffix(self._play_button(lambda: doc.event_effect(event)))
        expander.add_suffix(self._icon_button("user-trash-symbolic", "Remove event",
                                              lambda: self._edit(lambda: doc.remove_event(event))))

        row, _ = self._effect_row("Effect", None, doc.event_effect(event),
                                  lambda e: (doc.set_event_effect(event, e), self._changed()))
        expander.add_row(row)

        argument = info.argument if info else "first argument"
        for arg, effect in doc.arg_rules(event).items():
            rule, _ = self._effect_row(arg, f"When the {argument} is", effect,
                                       lambda e, arg=arg: (doc.set_arg_effect(event, arg, e), self._changed()))
            rule.add_suffix(self._play_button(lambda arg=arg: doc.arg_rules(event).get(arg)))
            rule.add_suffix(self._icon_button("list-remove-symbolic", "Remove rule",
                                              lambda arg=arg: self._edit(lambda: doc.remove_arg(event, arg))))
            expander.add_row(rule)

        add_rule = Adw.EntryRow(title=f"Add a rule for a {argument}", show_apply_button=True)

        def apply(entry):
            arg = entry.get_text().strip()
            if arg:
                self.expanded.add(event)
                self._edit(lambda: doc.set_arg_effect(event, arg, doc.event_effect(event)))
        add_rule.connect("apply", apply)
        expander.add_row(add_rule)
        return expander

    def _add_event_button(self):
        listbox = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        for info in model.EVENT_CATALOG:
            row = Adw.ActionRow(title=info.name, subtitle=info.description, activatable=True)
            row.connect("activated", lambda _r, name=info.name: self._add_event(name, popover))
            listbox.append(row)
        custom = Gtk.Entry(placeholder_text="Other event name", hexpand=True)
        custom.connect("activate", lambda e: self._add_event(e.get_text(), popover))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(Gtk.ScrolledWindow(child=listbox, min_content_height=360, min_content_width=360,
                                      propagate_natural_height=True))
        box.append(custom)
        popover = Gtk.Popover(child=box)
        return Gtk.MenuButton(icon_name="list-add-symbolic", tooltip_text="Add event",
                              popover=popover, css_classes=["flat"], valign=Gtk.Align.CENTER)

    def _add_event(self, name, popover=None):
        if not self.doc:
            return False
        try:
            added = self.doc.add_event(name, 1)
        except ValueError as e:
            self._toast(str(e))
            return False
        if popover:
            popover.popdown()
        if not added:
            self._toast(f"“{name}” is already in the profile")
        self.expanded.add(name.strip())
        self._build_editor()
        self._changed()
        return added

    def _edit(self, change):
        try:
            change()
        except ValueError as e:
            self._toast(str(e))
            return
        self._build_editor()
        self._changed()

    # Effects page

    def _on_waveforms(self, reply):
        waveforms = reply.get("waveforms") if isinstance(reply, dict) and reply.get("ok") else None
        if waveforms:
            self.waveforms = waveforms
        self._build_effects_page()
        self._build_editor()

    def _build_effects_page(self):
        if self.effects_group:
            self.effects_page.remove(self.effects_group)
        group = Adw.PreferencesGroup(
            title="Waveforms",
            description="Play each waveform your mouse knows and give it a name you will recognise. "
                        "Names are saved in settings.json and shown in the profile editor.")
        for effect in self.waveforms:
            row = Adw.EntryRow(title=f"Waveform {effect}", text=self.names.get(effect, ""),
                               show_apply_button=True)
            row.add_prefix(self._play_button(lambda effect=effect: effect, f"Play waveform {effect}"))
            row.connect("apply", lambda r, effect=effect: self._name_effect(effect, r.get_text()))
            group.add(row)
        self.effects_page.add(group)
        self.effects_group = group

    def _name_effect(self, effect, name):
        try:
            config.set_effect_name(effect, name)
        except OSError as e:
            self._toast(f"Not saved: {e}")
            return
        self.names = config.effect_names()
        self._build_editor()
        self._toast(f"Waveform {effect} is now “{name.strip()}”" if name.strip() else f"Waveform {effect} has no name")

    # Listen page

    def _on_listen_toggled(self, row, _param):
        if row.get_active() and not self.listener.running:
            try:
                self.listener.start()
            except RuntimeError as e:
                self._toast(str(e))
                row.set_active(False)
        elif not row.get_active() and self.listener.running:
            self.listener.stop()

    def _on_listener_stopped(self, message):
        self.listen_switch.set_active(False)
        self._toast(message)

    def _on_event(self, name, args):
        first = args[0] if args else ""
        key = (name, first)
        entry = self.captured.pop(key, [0, args])
        entry[0] += 1
        entry[1] = args
        self.captured = {key: entry, **self.captured}       # most recent first
        while len(self.captured) > LISTEN_LIMIT:
            self.captured.popitem()
        self._build_captured()

    def _clear_captured(self):
        self.captured = {}
        self._build_captured()

    def _build_captured(self):
        for row in self.captured_rows:
            self.captured_group.remove(row)
        self.captured_rows = []
        for (name, first), (count, args) in self.captured.items():
            info = model.CATALOG.get(name)
            subtitle = ",".join(args) or "(no arguments)"
            if info:
                subtitle = f"{subtitle}  ·  {info.argument}"
            row = Adw.ActionRow(title=f"{name}  ×{count}" if count > 1 else name,
                                subtitle=GLib.markup_escape_text(subtitle))
            add_event = Gtk.Button(label="Add event", valign=Gtk.Align.CENTER, css_classes=["flat"])
            add_event.connect("clicked", lambda _b, n=name: self._capture_event(n))
            row.add_suffix(add_event)
            if first:
                add_rule = Gtk.Button(label="Add rule", valign=Gtk.Align.CENTER, css_classes=["flat"],
                                      tooltip_text=f"Play an effect only when the {info.argument if info else 'first argument'} is “{first}”")
                add_rule.connect("clicked", lambda _b, n=name, a=first: self._capture_rule(n, a))
                row.add_suffix(add_rule)
            self.captured_group.add(row)
            self.captured_rows.append(row)

    def _capture_event(self, name):
        if self._add_event(name):
            self._toast(f"Added “{name}” to “{self.doc.name}”; save to keep it")

    def _capture_rule(self, name, arg):
        if not self.doc:
            return
        effect = self.doc.event_effect(name) if name in self.doc.events else 1
        self.expanded.add(name)
        self._edit(lambda: self.doc.set_arg_effect(name, arg, effect if effect is not None else 1))
        self._toast(f"Added a rule “{arg}” to {name}; save to keep it")

    # Status

    def _refresh_status(self):
        def done(reply):
            if not isinstance(reply, dict) or reply.get("ok") is False:
                text = "Daemon not running"
            elif reply.get("paused"):
                text = f"Paused · {str(reply.get('profile', '')).removesuffix('.json')}"
            else:
                text = f"Running · {str(reply.get('profile', '')).removesuffix('.json')}"
            self.status_label.set_text(text)
        daemon.call({"cmd": "status"}, done)
        return GLib.SOURCE_CONTINUE

    def _toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(str(text)), timeout=4))
