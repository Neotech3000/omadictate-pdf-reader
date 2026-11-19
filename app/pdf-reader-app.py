#!/usr/bin/env python3
"""Omarchy-themed PDF reader with online neural speech and timed highlights."""

from __future__ import annotations

import os
import queue
import threading
import tomllib
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gdk, Gio, GLib, Gst, Gtk

from pdf_read_aloud_engine import (
    PageAnalysis,
    PageData,
    TimedHighlight,
    WordBox,
    analyze_page,
    load_document,
    render_page,
    stream_page,
)


APP_ID = "io.github.neotech3000.omadictatepdfreader"
THEME_COLORS = Path.home() / ".local/state/omarchy/current/theme/colors.toml"

VOICES = (
    ("Ava — expressive, warm (US)", "en-US-AvaMultilingualNeural"),
    ("Andrew — warm, confident (US)", "en-US-AndrewMultilingualNeural"),
    ("Emma — clear, upbeat (US)", "en-US-EmmaMultilingualNeural"),
    ("Brian — casual, approachable (US)", "en-US-BrianMultilingualNeural"),
    ("Aria — polished narrator (US)", "en-US-AriaNeural"),
    ("Jenny — friendly, clear (US)", "en-US-JennyNeural"),
    ("Guy — lively narrator (US)", "en-US-GuyNeural"),
    ("Christopher — steady, authoritative (US)", "en-US-ChristopherNeural"),
    ("Sonia — natural British English (UK)", "en-GB-SoniaNeural"),
    ("Ryan — friendly British English (UK)", "en-GB-RyanNeural"),
)

HIGHLIGHT_COLORS = (
    ("Gold", "#FFD166"),
    ("Mint", "#67E8B3"),
    ("Sky", "#63B3FF"),
    ("Rose", "#FF7BAC"),
    ("Violet", "#B794F4"),
)

FALLBACK_COLORS = {
    "background": "#101315",
    "foreground": "#cacccc",
    "accent": "#798186",
    "selection": "#343d41",
    "muted": "#70777b",
}


def theme_colors() -> dict[str, str]:
    try:
        with THEME_COLORS.open("rb") as theme_file:
            values = tomllib.load(theme_file)
        return {key: values.get(key, fallback) for key, fallback in FALLBACK_COLORS.items()}
    except (OSError, tomllib.TOMLDecodeError):
        return FALLBACK_COLORS.copy()


class PDFReaderApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        Gst.init(None)

        self.window: Gtk.ApplicationWindow | None = None
        self.pdf_path: Path | None = None
        self.pages: list[PageData] = []
        self.page_analyses: dict[int, PageAnalysis] = {}
        self.analysis_inflight: set[tuple[int, int]] = set()
        self.current_page = 0
        self.page_image_index: int | None = None
        self.page_image_scale = (1.0, 1.0)
        self.render_token = 0
        self.rendering_request: tuple[int, int] | None = None
        self.document_token = 0

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.read_generation = 0
        self.cancel = threading.Event()
        self.reading = False
        self.paused = False
        self.active_page: int | None = None
        self.waiting_page: int | None = None
        self.deferred_analysis: tuple[int, int, int] | None = None
        self.active_highlights: list[TimedHighlight] = []
        self.active_rects: tuple[tuple[float, float, float, float], ...] = ()
        self.last_highlight_index: int | None = None
        self.audio_source: Gst.Element | None = None
        self.player: Gst.Pipeline | None = None
        self.highlight_layer: Gtk.DrawingArea | None = None

        self.css_provider = Gtk.CssProvider()
        self.theme_stamp: int | None = None

    def do_activate(self) -> None:
        if self.window:
            self.window.present()
            return

        self.window = Gtk.ApplicationWindow(application=self, title="Omadictate PDF reader")
        self.window.set_default_size(1000, 900)
        self.window.set_size_request(700, 650)

        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Omadictate PDF reader"))
        self.window.set_titlebar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(16)
        content.set_margin_bottom(14)
        content.set_margin_start(20)
        content.set_margin_end(20)
        self.window.set_child(content)

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label="Listen and read along")
        title.set_xalign(0)
        title.add_css_class("title-1")
        title_box.append(title)
        subtitle = Gtk.Label(label="Neural narration with synchronized PDF highlights")
        subtitle.set_xalign(0)
        subtitle.add_css_class("dim-label")
        title_box.append(subtitle)
        title_box.set_hexpand(True)
        title_row.append(title_box)

        self.browse_button = Gtk.Button(label="Browse with Flea")
        self.browse_button.connect("clicked", self.open_file_picker)
        title_row.append(self.browse_button)
        content.append(title_row)

        self.drop_zone = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.drop_zone.set_halign(Gtk.Align.FILL)
        self.drop_zone.set_valign(Gtk.Align.CENTER)
        self.drop_zone.set_margin_top(2)
        self.drop_zone.set_margin_bottom(2)
        self.drop_zone.add_css_class("drop-zone")
        drop_icon = Gtk.Image.new_from_icon_name("document-open-symbolic")
        drop_icon.set_pixel_size(30)
        drop_icon.add_css_class("drop-icon")
        self.drop_zone.append(drop_icon)
        drop_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        drop_title = Gtk.Label(label="Drop a PDF here")
        drop_title.set_xalign(0)
        drop_title.add_css_class("drop-title")
        drop_text.append(drop_title)
        drop_hint = Gtk.Label(label="or choose one with Flea")
        drop_hint.set_xalign(0)
        drop_hint.add_css_class("dim-label")
        drop_text.append(drop_hint)
        drop_text.set_hexpand(True)
        self.drop_zone.append(drop_text)
        content.append(self.drop_zone)

        self.file_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.file_card.add_css_class("file-card")
        self.file_card.set_visible(False)
        self.file_icon = Gtk.Image.new_from_icon_name("application-pdf-symbolic")
        self.file_icon.set_pixel_size(22)
        self.file_card.append(self.file_icon)
        file_details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        file_details.set_hexpand(True)
        self.file_name = Gtk.Label(label="")
        self.file_name.set_xalign(0)
        self.file_name.set_ellipsize(3)
        self.file_name.add_css_class("file-name")
        file_details.append(self.file_name)
        self.file_location = Gtk.Label(label="")
        self.file_location.set_xalign(0)
        self.file_location.set_ellipsize(3)
        self.file_location.add_css_class("dim-label")
        file_details.append(self.file_location)
        self.file_card.append(file_details)
        content.append(self.file_card)

        options = Gtk.Grid(column_spacing=10, row_spacing=8)
        options.set_margin_top(2)
        options.attach(self.form_label("Voice"), 0, 0, 1, 1)
        self.voice_dropdown = Gtk.DropDown.new_from_strings([label for label, _voice in VOICES])
        self.voice_dropdown.set_selected(0)
        self.voice_dropdown.set_hexpand(True)
        options.attach(self.voice_dropdown, 1, 0, 3, 1)

        options.attach(self.form_label("Speed"), 0, 1, 1, 1)
        self.speed_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 100, 250, 5)
        self.speed_scale.set_value(165)
        self.speed_scale.set_draw_value(False)
        self.speed_scale.set_hexpand(True)
        self.speed_scale.set_tooltip_text("Approximate speaking speed")
        self.speed_scale.connect("value-changed", self.speed_changed)
        options.attach(self.speed_scale, 1, 1, 2, 1)
        self.speed_value = Gtk.Label(label="165 wpm")
        self.speed_value.set_width_chars(8)
        self.speed_value.set_xalign(1)
        self.speed_value.add_css_class("dim-label")
        options.attach(self.speed_value, 3, 1, 1, 1)

        options.attach(self.form_label("Highlight"), 0, 2, 1, 1)
        self.highlight_mode = Gtk.DropDown.new_from_strings(["Word", "Sentence"])
        options.attach(self.highlight_mode, 1, 2, 1, 1)
        options.attach(self.form_label("Color"), 2, 2, 1, 1)
        self.highlight_color = Gtk.DropDown.new_from_strings([name for name, _color in HIGHLIGHT_COLORS])
        self.highlight_color.set_selected(0)
        self.highlight_color.connect("notify::selected", self.highlight_color_changed)
        options.attach(self.highlight_color, 3, 2, 1, 1)
        content.append(options)

        skip_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        skip_label = self.form_label("Skip")
        skip_row.append(skip_label)
        self.skip_graphics = Gtk.CheckButton(label="Charts / figures")
        self.skip_graphics.set_tooltip_text("Skip captions and text detected inside chart or figure areas")
        skip_row.append(self.skip_graphics)
        self.skip_line_numbers = Gtk.CheckButton(label="Line numbers")
        self.skip_line_numbers.set_tooltip_text("Skip aligned line-number columns; ordinary numbered lists are kept")
        skip_row.append(self.skip_line_numbers)
        content.append(skip_row)

        self.analysis_label = Gtk.Label(label="Page layout is analyzed locally as you read.")
        self.analysis_label.set_xalign(0)
        self.analysis_label.set_ellipsize(3)
        self.analysis_label.add_css_class("dim-label")
        content.append(self.analysis_label)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.play_button = Gtk.Button()
        self.play_button.set_child(self.button_content("media-playback-start-symbolic", "Read aloud"))
        self.play_button.add_css_class("suggested-action")
        self.play_button.connect("clicked", self.start_reading)
        controls.append(self.play_button)
        self.pause_button = Gtk.Button(label="Pause")
        self.pause_button.set_sensitive(False)
        self.pause_button.connect("clicked", self.toggle_pause)
        controls.append(self.pause_button)
        self.stop_button = Gtk.Button(label="Stop")
        self.stop_button.set_sensitive(False)
        self.stop_button.add_css_class("flat")
        self.stop_button.connect("clicked", self.stop_reading)
        controls.append(self.stop_button)
        content.append(controls)

        privacy = Gtk.Label(
            label="Layout analysis stays on this device. Online voice sends the page text to Microsoft Edge TTS."
        )
        privacy.set_xalign(0)
        privacy.set_wrap(True)
        privacy.add_css_class("dim-label")
        privacy.add_css_class("privacy-note")
        content.append(privacy)

        viewer_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        viewer_title = Gtk.Label(label="PDF")
        viewer_title.add_css_class("heading")
        viewer_title.set_xalign(0)
        viewer_title.set_hexpand(True)
        viewer_header.append(viewer_title)
        self.prev_button = Gtk.Button.new_from_icon_name("go-up-symbolic")
        self.prev_button.set_tooltip_text("Previous page")
        self.prev_button.set_sensitive(False)
        self.prev_button.connect("clicked", self.previous_page)
        viewer_header.append(self.prev_button)
        self.page_count_label = Gtk.Label(label="No document")
        self.page_count_label.add_css_class("dim-label")
        viewer_header.append(self.page_count_label)
        self.next_button = Gtk.Button.new_from_icon_name("go-down-symbolic")
        self.next_button.set_tooltip_text("Next page")
        self.next_button.set_sensitive(False)
        self.next_button.connect("clicked", self.next_page)
        viewer_header.append(self.next_button)
        content.append(viewer_header)

        self.viewer_scroll = Gtk.ScrolledWindow()
        self.viewer_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.viewer_scroll.set_min_content_height(240)
        self.viewer_scroll.set_vexpand(True)
        self.viewer_scroll.add_css_class("pdf-viewer")
        self.viewer_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.viewer_content.set_halign(Gtk.Align.CENTER)
        self.viewer_content.set_valign(Gtk.Align.START)
        self.viewer_placeholder = Gtk.Label(label="Choose a PDF to open it here.")
        self.viewer_placeholder.set_margin_top(40)
        self.viewer_placeholder.add_css_class("dim-label")
        self.viewer_content.append(self.viewer_placeholder)
        self.viewer_scroll.set_child(self.viewer_content)
        content.append(self.viewer_scroll)

        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_xalign(0)
        self.status_label.set_ellipsize(3)
        self.status_label.add_css_class("status-label")
        content.append(self.status_label)

        self.install_drop_targets()
        self.install_theme()
        self.window.connect("close-request", self.on_close)
        self.window.present()
        GLib.timeout_add(50, self.process_events)
        GLib.timeout_add(40, self.update_timed_highlight)
        GLib.timeout_add_seconds(2, self.refresh_theme)

    @staticmethod
    def form_label(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.set_xalign(0)
        label.add_css_class("dim-label")
        return label

    @staticmethod
    def button_content(icon_name: str, label: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(Gtk.Image.new_from_icon_name(icon_name))
        box.append(Gtk.Label(label=label))
        return box

    def install_drop_targets(self) -> None:
        for widget in (self.drop_zone, self.window):
            target = Gtk.DropTarget.new(Gdk.FileList.__gtype__, Gdk.DragAction.COPY)
            target.connect("drop", self.on_drop)
            if widget is self.drop_zone:
                target.connect("enter", self.on_drag_enter)
                target.connect("leave", self.on_drag_leave)
            widget.add_controller(target)

    def on_drag_enter(self, _target: Gtk.DropTarget, _x: float, _y: float) -> Gdk.DragAction:
        self.drop_zone.add_css_class("drag-over")
        return Gdk.DragAction.COPY

    def on_drag_leave(self, _target: Gtk.DropTarget) -> None:
        self.drop_zone.remove_css_class("drag-over")

    def on_drop(self, _target: Gtk.DropTarget, value: Gdk.FileList, _x: float, _y: float) -> bool:
        self.drop_zone.remove_css_class("drag-over")
        files = value.get_files()
        if not files:
            return False
        path = files[0].get_path()
        if not path:
            self.status_label.set_text("This app currently needs a local PDF file.")
            return True
        self.set_pdf(Path(path))
        return True

    def open_file_picker(self, _button: Gtk.Button) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Choose a PDF")
        file_filter = Gtk.FileFilter()
        file_filter.set_name("PDF documents")
        file_filter.add_mime_type("application/pdf")
        file_filter.add_pattern("*.pdf")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        dialog.set_filters(filters)
        dialog.open(self.window, None, self.file_picker_finished)

    def file_picker_finished(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            selected = dialog.open_finish(result)
        except GLib.Error:
            return
        path = selected.get_path()
        if path:
            self.set_pdf(Path(path))

    def set_pdf(self, path: Path) -> None:
        if path.suffix.lower() != ".pdf" or not path.is_file():
            self.status_label.set_text("Please choose a PDF document.")
            return
        self.stop_reading(None, update_status=False)
        self.document_token += 1
        token = self.document_token
        self.pdf_path = path
        self.pages = []
        self.page_analyses.clear()
        self.analysis_inflight.clear()
        self.current_page = 0
        self.page_image_index = None
        self.rendering_request = None
        self.file_name.set_text(path.name)
        self.file_location.set_text(str(path.parent))
        self.drop_zone.set_visible(False)
        self.file_card.set_visible(True)
        self.page_count_label.set_text("Loading…")
        self.prev_button.set_sensitive(False)
        self.next_button.set_sensitive(False)
        self.analysis_label.set_text("Analyzing page layout as you read…")
        self.show_viewer_message("Opening PDF…")
        self.status_label.set_text("Reading PDF layout and preparing scanned pages…")
        threading.Thread(target=self.load_document_worker, args=(path, token), daemon=True).start()

    def load_document_worker(self, path: Path, token: int) -> None:
        try:
            pages = load_document(
                path,
                progress=lambda current, total: self.events.put(
                    ("load-progress", (token, current, total))
                ),
            )
            self.events.put(("document-loaded", (token, pages, None)))
        except Exception as error:
            self.events.put(("document-loaded", (token, [], str(error))))

    def request_page_analysis(self, page_index: int) -> None:
        if not self.pdf_path or not 0 <= page_index < len(self.pages):
            return
        if page_index in self.page_analyses:
            if self.reading and self.active_page is None and self.waiting_page == page_index:
                self.begin_audio(page_index)
            return
        request = (self.document_token, page_index)
        if request in self.analysis_inflight:
            return
        self.analysis_inflight.add(request)
        self.events.put(("analysis-status", (self.document_token, page_index)))
        threading.Thread(
            target=self.page_analysis_worker,
            args=(self.document_token, page_index, self.pdf_path, self.pages[page_index]),
            daemon=True,
        ).start()

    def page_analysis_worker(
        self, document_token: int, page_index: int, path: Path, page: PageData
    ) -> None:
        try:
            try:
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
            except OSError:
                pass
            result = analyze_page(page, path, page_index)
            self.events.put(("page-analysis-ready", (document_token, page_index, result, None)))
        except Exception as error:
            self.events.put(("page-analysis-ready", (document_token, page_index, None, str(error))))

    def show_viewer_message(self, message: str) -> None:
        self.clear_viewer()
        label = Gtk.Label(label=message)
        label.set_margin_top(40)
        label.add_css_class("dim-label")
        self.viewer_content.append(label)

    def clear_viewer(self) -> None:
        child = self.viewer_content.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.viewer_content.remove(child)
            child = next_child

    def request_page_render(self, page_index: int) -> None:
        if not self.pdf_path or not 0 <= page_index < len(self.pages):
            return
        if self.page_image_index == page_index:
            return
        if self.rendering_request == (self.document_token, page_index):
            return
        self.render_token += 1
        token = self.render_token
        document_token = self.document_token
        self.rendering_request = (document_token, page_index)
        path = self.pdf_path
        self.show_viewer_message(f"Rendering page {page_index + 1}…")
        threading.Thread(
            target=self.render_page_worker,
            args=(path, page_index, token, document_token),
            daemon=True,
        ).start()

    def render_page_worker(self, path: Path, page_index: int, token: int, document_token: int) -> None:
        try:
            image, x_scale, y_scale = render_page(path, page_index)
            payload = (token, document_token, page_index, image, x_scale, y_scale, None)
        except Exception as error:
            payload = (token, document_token, page_index, b"", 1.0, 1.0, str(error))
        self.events.put(("page-rendered", payload))

    def display_page_image(
        self, page_index: int, image: bytes, x_scale: float, y_scale: float
    ) -> None:
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(image))
        picture = Gtk.Picture.new_for_paintable(texture)
        picture.set_can_shrink(False)
        width = texture.get_width()
        height = texture.get_height()
        picture.set_size_request(width, height)

        overlay = Gtk.Overlay()
        overlay.set_size_request(width, height)
        overlay.set_child(picture)
        self.highlight_layer = Gtk.DrawingArea()
        self.highlight_layer.set_content_width(width)
        self.highlight_layer.set_content_height(height)
        self.highlight_layer.set_size_request(width, height)
        self.highlight_layer.set_can_target(False)
        self.highlight_layer.set_draw_func(self.draw_highlights)
        overlay.add_overlay(self.highlight_layer)

        page_frame = Gtk.Frame()
        page_frame.add_css_class("pdf-page")
        page_frame.set_child(overlay)
        self.clear_viewer()
        self.viewer_content.append(page_frame)
        self.page_image_index = page_index
        self.page_image_scale = (x_scale, y_scale)
        self.active_rects = ()
        self.last_highlight_index = None
        self.viewer_scroll.get_vadjustment().set_value(0)

    def draw_highlights(self, _area: Gtk.DrawingArea, context, _width: int, _height: int) -> None:
        color_text = HIGHLIGHT_COLORS[self.highlight_color.get_selected()][1]
        color = Gdk.RGBA()
        color.parse(color_text)
        x_scale, y_scale = self.page_image_scale
        context.set_source_rgba(color.red, color.green, color.blue, 0.43)
        for x0, y0, x1, y1 in self.active_rects:
            context.rectangle(x0 * x_scale, y0 * y_scale, max(2, (x1 - x0) * x_scale), max(3, (y1 - y0) * y_scale))
        context.fill()

    def update_page_controls(self) -> None:
        if not self.pages:
            self.page_count_label.set_text("No document")
            self.prev_button.set_sensitive(False)
            self.next_button.set_sensitive(False)
            return
        self.page_count_label.set_text(f"Page {self.current_page + 1} of {len(self.pages)}")
        can_navigate = not self.reading
        self.prev_button.set_sensitive(can_navigate and self.current_page > 0)
        self.next_button.set_sensitive(can_navigate and self.current_page + 1 < len(self.pages))

    def previous_page(self, _button: Gtk.Button) -> None:
        if self.current_page > 0:
            self.show_page(self.current_page - 1)

    def next_page(self, _button: Gtk.Button) -> None:
        if self.current_page + 1 < len(self.pages):
            self.show_page(self.current_page + 1)

    def show_page(self, page_index: int) -> None:
        if not 0 <= page_index < len(self.pages):
            return
        self.current_page = page_index
        self.update_page_controls()
        self.request_page_render(page_index)

    def speed_changed(self, scale: Gtk.Scale) -> None:
        self.speed_value.set_text(f"{round(scale.get_value())} wpm")

    def highlight_color_changed(self, _dropdown: Gtk.DropDown, _param) -> None:
        if self.highlight_layer:
            self.highlight_layer.queue_draw()

    def start_reading(self, _button: Gtk.Button) -> None:
        if self.reading:
            return
        if not self.pages:
            self.status_label.set_text("Choose a PDF before starting.")
            return
        index = self.next_readable_page(self.current_page)
        if index is None:
            self.status_label.set_text("No readable text was found in this PDF.")
            return

        self.read_generation += 1
        self.cancel = threading.Event()
        self.reading = True
        self.paused = False
        self.active_page = None
        self.waiting_page = index
        self.active_highlights = []
        self.set_read_controls()
        self.status_label.set_text(f"Analyzing page {index + 1}…")
        self.update_page_controls()
        self.show_page(index)
        self.request_page_analysis(index)

    def next_readable_page(self, start: int) -> int | None:
        for index in range(max(0, start), len(self.pages)):
            if self.pages[index].text.strip():
                return index
        return None

    def begin_audio(self, page_index: int) -> None:
        if not self.reading or not self.pdf_path:
            return
        analysis = self.page_analyses.get(page_index)
        if analysis is None:
            self.waiting_page = page_index
            self.request_page_analysis(page_index)
            return
        words = analysis.speech_words(
            self.skip_graphics.get_active(), self.skip_line_numbers.get_active()
        )
        text = " ".join(word.text for word in words)
        if not text.strip():
            self.analysis_label.set_text(f"Page {page_index + 1}: skipped by your filters.")
            self.advance_to_next_page(page_index)
            return
        voice_index = min(self.voice_dropdown.get_selected(), len(VOICES) - 1)
        voice = VOICES[voice_index][1]
        boundary = "SentenceBoundary" if self.highlight_mode.get_selected() == 1 else "WordBoundary"
        rate_value = max(100, min(250, round(self.speed_scale.get_value())))
        rate_percent = max(-50, min(100, round((rate_value / 165 - 1) * 100)))
        rate = f"{rate_percent:+d}%"
        run_id = self.read_generation
        cancelled = self.cancel
        try:
            pipeline, source = self.create_stream_pipeline()
        except RuntimeError as error:
            self.stop_reading(None, update_status=False)
            self.status_label.set_text(str(error))
            return
        self.player = pipeline
        self.audio_source = source
        self.active_page = page_index
        self.waiting_page = None
        next_index = self.next_readable_page(page_index + 1)
        self.deferred_analysis = (
            (run_id, page_index, next_index) if next_index is not None else None
        )
        self.active_highlights = []
        self.active_rects = ()
        self.last_highlight_index = None
        self.pause_button.set_label("Pause")
        self.show_page(page_index)
        self.analysis_label.set_text(f"Page {page_index + 1} layout: {analysis.summary()}")
        self.status_label.set_text(f"Connecting to voice for page {page_index + 1}…")
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self.on_audio_eos)
        bus.connect("message::error", self.on_audio_error)
        self.set_read_controls()
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.status_label.set_text("Could not start audio playback.")
            self.stop_reading(None)
            return
        threading.Thread(
            target=self.stream_worker,
            args=(run_id, page_index, text, words, voice, rate, boundary, source, cancelled),
            daemon=True,
        ).start()

    def start_deferred_analysis(self, run_id: int, current_page: int) -> bool:
        pending = self.deferred_analysis
        if (
            not pending
            or pending[:2] != (run_id, current_page)
            or not self.reading
            or self.active_page != current_page
        ):
            return GLib.SOURCE_REMOVE
        if self.paused:
            return GLib.SOURCE_CONTINUE
        self.deferred_analysis = None
        self.request_page_analysis(pending[2])
        return GLib.SOURCE_REMOVE

    def schedule_deferred_analysis(self, run_id: int, current_page: int) -> None:
        if self.deferred_analysis and self.deferred_analysis[:2] == (run_id, current_page):
            GLib.timeout_add(1800, self.start_deferred_analysis, run_id, current_page)

    def advance_to_next_page(self, finished_page: int) -> None:
        next_index = self.next_readable_page(finished_page + 1)
        if next_index is None:
            self.finish_reading()
            return
        self.current_page = next_index
        self.waiting_page = next_index
        self.update_page_controls()
        self.show_page(next_index)
        self.status_label.set_text(f"Preparing page {next_index + 1}…")
        self.request_page_analysis(next_index)

    @staticmethod
    def create_stream_pipeline() -> tuple[Gst.Pipeline, Gst.Element]:
        pipeline = Gst.Pipeline.new(None)
        source = Gst.ElementFactory.make("appsrc", None)
        decoder = Gst.ElementFactory.make("decodebin", None)
        convert = Gst.ElementFactory.make("audioconvert", None)
        resample = Gst.ElementFactory.make("audioresample", None)
        sink = Gst.ElementFactory.make("autoaudiosink", None)
        if not all((pipeline, source, decoder, convert, resample, sink)):
            raise RuntimeError("GStreamer audio streaming plugins are unavailable")

        source.set_property(
            "caps",
            Gst.Caps.from_string("audio/mpeg,mpegversion=1,layer=3,rate=24000,channels=1"),
        )
        source.set_property("format", Gst.Format.TIME)
        source.set_property("block", True)
        source.set_property("max-bytes", 1024 * 1024)
        pipeline.add(source)
        pipeline.add(decoder)
        pipeline.add(convert)
        pipeline.add(resample)
        pipeline.add(sink)
        if not source.link(decoder) or not convert.link(resample) or not resample.link(sink):
            raise RuntimeError("Could not connect the audio playback pipeline")
        decoder.connect("pad-added", PDFReaderApp.on_decode_pad_added, convert)
        return pipeline, source

    @staticmethod
    def on_decode_pad_added(_decoder: Gst.Element, pad: Gst.Pad, convert: Gst.Element) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        if not caps or not caps.get_size() or not caps.get_structure(0).get_name().startswith("audio/"):
            return
        sink_pad = convert.get_static_pad("sink")
        if not sink_pad.is_linked():
            pad.link(sink_pad)

    def stream_worker(
        self,
        run_id: int,
        page_index: int,
        text: str,
        words: tuple[WordBox, ...],
        voice: str,
        rate: str,
        boundary: str,
        source: Gst.Element,
        cancelled: threading.Event,
    ) -> None:
        byte_count = 0
        started = False

        def push_audio(data: bytes) -> None:
            nonlocal byte_count, started
            if cancelled.is_set():
                raise InterruptedError
            buffer = Gst.Buffer.new_allocate(None, len(data), None)
            buffer.fill(0, data)
            buffer.pts = byte_count * 8 * Gst.SECOND // 48_000
            buffer.dts = buffer.pts
            buffer.duration = len(data) * 8 * Gst.SECOND // 48_000
            buffer.offset = byte_count
            buffer.offset_end = byte_count + len(data)
            flow = source.emit("push-buffer", buffer)
            if flow != Gst.FlowReturn.OK:
                if cancelled.is_set():
                    raise InterruptedError
                raise RuntimeError(f"Audio stream stopped unexpectedly ({flow.value_nick}).")
            byte_count += len(data)
            if not started:
                started = True
                self.events.put(("audio-started", (run_id, page_index)))

        def add_highlight(highlight: TimedHighlight) -> None:
            self.events.put(("timing", (run_id, page_index, highlight)))

        try:
            stream_page(
                text,
                voice,
                rate,
                boundary,
                words,
                cancelled,
                push_audio,
                add_highlight,
            )
            if not cancelled.is_set():
                flow = source.emit("end-of-stream")
                if flow != Gst.FlowReturn.OK:
                    raise RuntimeError(f"Could not finish audio stream ({flow.value_nick}).")
        except InterruptedError:
            return
        except Exception as error:
            if not cancelled.is_set():
                self.events.put(("stream-error", (run_id, page_index, str(error))))

    def release_player(self) -> None:
        player = self.player
        self.player = None
        self.audio_source = None
        if player:
            player.get_bus().remove_signal_watch()
            player.set_state(Gst.State.NULL)

    def on_audio_eos(self, bus: Gst.Bus, _message: Gst.Message) -> None:
        if not self.player or bus != self.player.get_bus():
            return
        finished_page = self.active_page
        self.release_player()
        self.active_page = None
        self.waiting_page = None
        self.active_highlights = []
        self.active_rects = ()
        self.last_highlight_index = None
        if self.highlight_layer:
            self.highlight_layer.queue_draw()
        if not self.reading or finished_page is None:
            return

        self.advance_to_next_page(finished_page)

    def on_audio_error(self, bus: Gst.Bus, message: Gst.Message) -> None:
        if not self.player or bus != self.player.get_bus():
            return
        error, _debug = message.parse_error()
        self.status_label.set_text(f"Audio playback failed: {error.message}")
        self.stop_reading(None)

    def update_timed_highlight(self) -> bool:
        if not self.reading or self.active_page is None or not self.active_highlights:
            return GLib.SOURCE_CONTINUE
        ok, position = self.player.query_position(Gst.Format.TIME)
        if not ok:
            return GLib.SOURCE_CONTINUE
        seconds = position / Gst.SECOND
        active_index = None
        for index, item in enumerate(self.active_highlights):
            if item.start <= seconds < item.end:
                active_index = index
                break
        if active_index != self.last_highlight_index:
            self.last_highlight_index = active_index
            if active_index is None:
                self.active_rects = ()
            else:
                self.active_rects = self.active_highlights[active_index].rects
            if self.highlight_layer:
                self.highlight_layer.queue_draw()
        return GLib.SOURCE_CONTINUE

    def toggle_pause(self, _button: Gtk.Button) -> None:
        if not self.reading or not self.player:
            return
        if self.paused:
            self.player.set_state(Gst.State.PLAYING)
            self.paused = False
            self.pause_button.set_label("Pause")
            self.status_label.set_text("Reading aloud…")
        elif self.active_page is not None:
            self.player.set_state(Gst.State.PAUSED)
            self.paused = True
            self.pause_button.set_label("Resume")
            self.status_label.set_text("Paused")

    def stop_reading(self, _button: Gtk.Button | None, update_status: bool = True) -> None:
        self.read_generation += 1
        self.cancel.set()
        self.release_player()
        self.reading = False
        self.paused = False
        self.active_page = None
        self.waiting_page = None
        self.deferred_analysis = None
        self.active_highlights = []
        self.active_rects = ()
        self.last_highlight_index = None
        self.pause_button.set_label("Pause")
        self.set_read_controls()
        self.update_page_controls()
        if self.highlight_layer:
            self.highlight_layer.queue_draw()
        if update_status:
            self.status_label.set_text("Stopped")

    def finish_reading(self) -> None:
        self.release_player()
        self.reading = False
        self.paused = False
        self.active_page = None
        self.waiting_page = None
        self.deferred_analysis = None
        self.active_highlights = []
        self.active_rects = ()
        self.pause_button.set_label("Pause")
        self.set_read_controls()
        self.update_page_controls()
        if self.highlight_layer:
            self.highlight_layer.queue_draw()
        self.status_label.set_text("Finished reading the PDF")

    def set_read_controls(self) -> None:
        if not self.window:
            return
        self.play_button.set_sensitive(not self.reading)
        self.pause_button.set_sensitive(self.reading and self.active_page is not None)
        self.stop_button.set_sensitive(self.reading)
        self.voice_dropdown.set_sensitive(not self.reading)
        self.highlight_mode.set_sensitive(not self.reading)
        self.speed_scale.set_sensitive(not self.reading)
        self.browse_button.set_sensitive(not self.reading)

    def process_events(self) -> bool:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "load-progress":
                    token, current, total = payload
                    if token == self.document_token:
                        self.status_label.set_text(f"Reading page layout {current} of {total}…")
                elif event == "document-loaded":
                    token, pages, error = payload
                    if token != self.document_token:
                        continue
                    if error:
                        self.pages = []
                        self.page_count_label.set_text("Could not open PDF")
                        self.show_viewer_message("Could not open this PDF.")
                        self.status_label.set_text(str(error))
                    else:
                        self.pages = pages
                        self.current_page = 0
                        self.update_page_controls()
                        self.status_label.set_text(
                            f"Opened {len(pages)} pages. Choose a voice and press Read aloud."
                        )
                        self.show_page(0)
                elif event == "page-rendered":
                    token, document_token, index, image, x_scale, y_scale, error = payload
                    if token == self.render_token:
                        self.rendering_request = None
                    if token != self.render_token or document_token != self.document_token:
                        continue
                    if error:
                        self.show_viewer_message(f"Could not render page: {error}")
                    else:
                        self.display_page_image(index, image, x_scale, y_scale)
                elif event == "analysis-status":
                    document_token, page_index = payload
                    if document_token == self.document_token:
                        self.analysis_label.set_text(f"Analyzing page {page_index + 1} layout in the background…")
                elif event == "page-analysis-ready":
                    document_token, page_index, analysis, error = payload
                    self.analysis_inflight.discard((document_token, page_index))
                    if document_token != self.document_token or not 0 <= page_index < len(self.pages):
                        continue
                    if error or analysis is None:
                        analysis = PageAnalysis(self.pages[page_index], frozenset(), frozenset(), 0)
                        self.analysis_label.set_text(
                            f"Page {page_index + 1}: layout detection unavailable; reading all text."
                        )
                    else:
                        if self.active_page is not None and page_index > self.active_page:
                            self.analysis_label.set_text(
                                f"Next page {page_index + 1} analyzed: {analysis.summary()}"
                            )
                    self.page_analyses[page_index] = analysis
                    if self.reading and self.active_page is None and self.waiting_page == page_index:
                        self.begin_audio(page_index)
                elif event == "audio-started":
                    run_id, page_index = payload
                    if run_id == self.read_generation and self.active_page == page_index:
                        self.status_label.set_text(
                            "Paused" if self.paused else f"Reading page {page_index + 1} of {len(self.pages)}"
                        )
                        self.schedule_deferred_analysis(run_id, page_index)
                elif event == "timing":
                    run_id, page_index, highlight = payload
                    if run_id == self.read_generation and self.active_page == page_index:
                        self.active_highlights.append(highlight)
                elif event == "stream-error":
                    run_id, _page_index, error = payload
                    if run_id == self.read_generation and self.reading:
                        self.stop_reading(None, update_status=False)
                        self.status_label.set_text(f"Voice service error: {error}")
        except queue.Empty:
            pass
        return GLib.SOURCE_CONTINUE

    def install_theme(self) -> None:
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display, self.css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self.refresh_theme()

    def refresh_theme(self) -> bool:
        try:
            stamp = THEME_COLORS.stat().st_mtime_ns
        except OSError:
            stamp = 0
        if stamp != self.theme_stamp:
            self.theme_stamp = stamp
            colors = theme_colors()
            background = colors["background"]
            foreground = colors["foreground"]
            accent = colors["accent"]
            selection = colors["selection"]
            muted = colors["muted"]
            css = f"""
                window {{ background-color: {background}; color: {foreground}; }}
                headerbar {{ background-color: {background}; color: {foreground}; }}
                .dim-label, .status-label {{ color: {muted}; }}
                .drop-zone {{
                    background-color: alpha({accent}, 0.08);
                    border: 1px dashed alpha({accent}, 0.62);
                    border-radius: 14px;
                    padding: 14px;
                }}
                .drop-zone.drag-over {{
                    background-color: alpha({accent}, 0.22);
                    border: 2px solid {accent};
                }}
                .drop-icon {{ color: {accent}; }}
                .drop-title, .file-name {{ font-weight: bold; }}
                .file-card {{
                    background-color: {selection};
                    border-radius: 10px;
                    padding: 8px 10px;
                }}
                .pdf-viewer {{
                    background-color: {selection};
                    border-radius: 10px;
                    padding: 8px;
                }}
                .pdf-page {{ background-color: white; }}
                button.suggested-action {{ background-color: {accent}; color: {background}; }}
                button.suggested-action:hover {{ background-color: {foreground}; }}
                .privacy-note {{ font-size: 0.9em; }}
            """
            self.css_provider.load_from_data(css)
        return GLib.SOURCE_CONTINUE

    def on_close(self, _window: Gtk.Window) -> bool:
        self.stop_reading(None, update_status=False)
        self.window = None
        return False


def main() -> None:
    PDFReaderApp().run(None)


if __name__ == "__main__":
    main()
