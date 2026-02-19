from __future__ import annotations

import ctypes
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import base64
from datetime import datetime
from ctypes import wintypes
from pathlib import Path
from tkinter import END, HORIZONTAL, LEFT, VERTICAL, W, BooleanVar, Canvas, Menu, PhotoImage, StringVar, TclError, Tk, Toplevel, messagebox
from tkinter import ttk

import requests

from retro_tracker.app_meta import APP_NAME, APP_VERSION
from retro_tracker.debug_logger import get_debug_logger
from retro_tracker.db import get_dashboard_data, init_db, save_snapshot
from retro_tracker.mixins import (
    AchievementMixin,
    ConfigPersistenceMixin,
    EmulatorStateMixin,
    ParsingMixin,
    StatusTimerMixin,
    ThemeMixin,
    UiBuildMixin,
)
from retro_tracker.paths import data_dir
from retro_tracker.ra_api import RetroAPIError, RetroAchievementsClient
from retro_tracker.runtime_constants import *  # noqa: F403


if os.name == "nt":
    try:
        _dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        _dwm_set_window_attribute = _dwmapi.DwmSetWindowAttribute
        _dwm_set_window_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
        _dwm_set_window_attribute.restype = ctypes.c_long
    except OSError:
        _dwm_set_window_attribute = None
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        _set_window_rgn = _user32.SetWindowRgn
        _set_window_rgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
        _set_window_rgn.restype = ctypes.c_int
        _create_round_rect_rgn = _gdi32.CreateRoundRectRgn
        _create_round_rect_rgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        _create_round_rect_rgn.restype = wintypes.HANDLE
        _delete_gdi_object = _gdi32.DeleteObject
        _delete_gdi_object.argtypes = [wintypes.HANDLE]
        _delete_gdi_object.restype = wintypes.BOOL
    except OSError:
        _set_window_rgn = None
        _create_round_rect_rgn = None
        _delete_gdi_object = None
else:
    _dwm_set_window_attribute = None
    _set_window_rgn = None
    _create_round_rect_rgn = None
    _delete_gdi_object = None

# Class: TrackerApp - Orchestre l'interface, la synchronisation et les interactions utilisateur.
class TrackerApp(
    ConfigPersistenceMixin,
    EmulatorStateMixin,
    StatusTimerMixin,
    ThemeMixin,
    UiBuildMixin,
    ParsingMixin,
    AchievementMixin,
):
    # Method: __init__ - Initialise l'objet et prépare son état interne.
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self._apply_window_icon()
        self.root.minsize(640, 420)
        self.root.option_add("*BorderWidth", 0)
        self.root.option_add("*HighlightThickness", 0)
        self.root.option_add("*Relief", "flat")
        self.root.after_idle(lambda: self._apply_rounded_window_corners(self.root))

        self.api_key = StringVar()
        self.api_username = StringVar()
        self.tracked_username = StringVar()
        self.db_path = StringVar()
        self.status_text = StringVar(value="Prêt")
        self.performance_timer_text = StringVar(value="Chargement: -")
        self.connection_summary = StringVar(value="-")
        self.dark_mode_enabled = BooleanVar(value=False)
        self.emulator_status_text = StringVar(value=EMULATOR_STATUS_INACTIVE)

        self.stat_points = StringVar(value="-")
        self.stat_softcore = StringVar(value="-")
        self.stat_true = StringVar(value="-")
        self.stat_mastered = StringVar(value="-")
        self.stat_beaten = StringVar(value="-")
        self.stat_games = StringVar(value="-")
        self.stat_snapshot = StringVar(value="-")
        self.current_game_title = StringVar(value="-")
        self.current_game_console = StringVar(value="-")
        self.current_game_progress = StringVar(value="-")
        self.current_game_last_unlock = StringVar(value="-")
        self.current_game_source = StringVar(value="Inconnu")
        self.current_game_note = StringVar(value="Aucun jeu en cours détecté.")
        self.current_game_next_achievement_title = StringVar(value=ACHIEVEMENT_NA_VALUE)
        self.current_game_next_achievement_description = StringVar(value=ACHIEVEMENT_NA_VALUE)
        self.current_game_next_achievement_points = StringVar(value=ACHIEVEMENT_NA_VALUE)
        self.current_game_next_achievement_unlocks = StringVar(value=ACHIEVEMENT_NA_VALUE)
        self.current_game_next_achievement_feasibility = StringVar(value=ACHIEVEMENT_NA_VALUE)
        self.current_game_achievements_note = StringVar(value="Aucun succès à afficher.")
        self.current_game_achievement_order_mode = ACHIEVEMENT_ORDER_NORMAL
        self.current_game_achievement_order_label = StringVar(
            value=ACHIEVEMENT_ORDER_LABELS[ACHIEVEMENT_ORDER_NORMAL]
        )

        self.sync_button: ttk.Button | None = None
        self.refresh_button: ttk.Button | None = None
        self.connection_button: ttk.Button | None = None
        self.profile_button: ttk.Button | None = None
        self.file_menu: Menu | None = None
        self.file_menu_profile_index: int | None = None
        self.summary_label: ttk.Label | None = None
        self.status_label: ttk.Label | None = None
        self.performance_timer_label: ttk.Label | None = None
        self.version_label: ttk.Label | None = None
        self.status_bar: ttk.Frame | None = None
        self.status_muted_reset_job: str | None = None
        self.performance_timer_update_job: str | None = None
        self.theme_toggle_frame: ttk.Frame | None = None
        self.theme_light_label: ttk.Label | None = None
        self.theme_separator_label: ttk.Label | None = None
        self.theme_dark_label: ttk.Label | None = None
        self.emulator_status_tab: ttk.Frame | None = None
        self.emulator_status_label: ttk.Label | None = None
        self.top_bar: ttk.Frame | None = None
        self.stats_frame: ttk.LabelFrame | None = None
        self.stat_cells: list[ttk.Frame] = []
        self.game_tree: ttk.Treeview | None = None
        self.recent_tree: ttk.Treeview | None = None
        self.main_tabs: ttk.Notebook | None = None
        self.main_tab_button_bar: ttk.Frame | None = None
        self.main_tab_buttons: dict[str, ttk.Button] = {}
        self.main_tab_frames: dict[str, ttk.Frame] = {}
        self.main_tab_selected_key = MAIN_TAB_CURRENT
        self.current_game_info_tree: ttk.Treeview | None = None
        self.current_game_title_value_label: ttk.Label | None = None
        self.current_game_next_achievement_desc_label: ttk.Label | None = None
        self.current_game_previous_achievement_button: ttk.Button | None = None
        self.current_game_next_achievement_button: ttk.Button | None = None
        self.current_game_achievement_order_button: ttk.Button | None = None
        self.current_game_source_value_label: ttk.Label | None = None
        self.current_game_tab_container: ttk.Frame | None = None
        self.current_game_loading_overlay: Canvas | None = None
        self.current_game_loading_panel: ttk.Frame | None = None
        self.current_game_loading_label: ttk.Label | None = None
        self.current_game_loading_progress: ttk.Progressbar | None = None
        self.current_game_loading_window_id: int | None = None
        self.current_game_loading_shade_id: int | None = None
        self.current_game_image_labels: dict[str, ttk.Label] = {}
        self.current_game_image_refs: dict[str, PhotoImage] = {}
        self.current_game_achievements_canvas: Canvas | None = None
        self.current_game_achievements_inner: ttk.Frame | None = None
        self.current_game_achievements_window_id: int | None = None
        self.current_game_achievement_tiles: list[ttk.Label] = []
        self.current_game_expected_achievement_tiles_count = 0
        self.current_game_achievement_tile_by_key: dict[str, ttk.Label] = {}
        self.current_game_achievement_data: list[dict[str, str]] = []
        self.current_game_locked_achievements: list[dict[str, str]] = []
        self.current_game_locked_achievement_index = 0
        self.current_game_clicked_achievement_key = ""
        self.current_game_clicked_achievement_persistent = False
        self.current_game_clicked_achievement_restore_job: str | None = None
        self.current_game_last_clicked_achievement_key = ""
        self.current_game_last_clicked_achievement_click_monotonic = 0.0
        self.current_game_achievement_refs: dict[str, PhotoImage] = {}
        self.current_game_badge_loader_token = 0
        self.current_game_badge_loader_in_progress = False
        self.current_game_active_images: dict[str, bytes] = {}
        self.current_game_achievement_tooltip: Toplevel | None = None
        self.current_game_achievement_tooltip_label: ttk.Label | None = None
        self.maintenance_tab_tooltip: Toplevel | None = None
        self.maintenance_tab_tooltip_label: ttk.Label | None = None
        self.profile_maintenance_tooltip: Toplevel | None = None
        self.profile_maintenance_tooltip_label: ttk.Label | None = None
        self.current_game_achievement_scroll_job: str | None = None
        self.current_game_achievement_scroll_direction = 1
        self.current_game_achievement_hovered = False
        self.current_game_achievement_tooltip_left_side = False
        self._current_game_gallery_columns = 0
        self._current_game_gallery_rows = 0
        self.connection_window: Toplevel | None = None
        self.profile_window: Toplevel | None = None
        self.modal_overlay: Canvas | None = None
        self.modal_track_job: str | None = None
        self.startup_loader_frame: ttk.Frame | None = None
        self.startup_loader_label: ttk.Label | None = None
        self.startup_loader_progress: ttk.Progressbar | None = None
        self._last_layout_width = 0
        self._last_profile_layout_width = 0
        self._last_modal_anchor: tuple[int, int, int, int] = (0, 0, 0, 0)
        self.style = ttk.Style(self.root)
        self.theme_colors: dict[str, str] = {}
        self._notebook_tab_image_normal: PhotoImage | None = None
        self._notebook_tab_image_selected: PhotoImage | None = None
        self._notebook_tab_image_disabled: PhotoImage | None = None
        self._tree_column_types: dict[str, dict[str, str]] = {}
        self._tree_headings: dict[str, dict[str, str]] = {}
        self._tree_sort_state: dict[str, tuple[str, bool]] = {}
        self._rounded_widget_bindings: set[str] = set()
        self._rounded_image_widget_bindings: set[str] = set()
        self._current_game_fetch_token = 0
        self.current_game_fetch_in_progress = False
        self.current_game_loading_timeout_job: str | None = None
        self.current_game_loading_hard_timeout_job: str | None = None
        self._loading_timer_started_monotonic = 0.0
        self._transition_timer_started_monotonic = 0.0
        self._loading_timer_active = False
        self._transition_timer_active = False
        self._last_loading_duration_seconds: float | None = None
        self._last_transition_duration_seconds: float | None = None
        self._last_loading_overlay_incomplete_log_monotonic = 0.0
        self.persist_current_game_cache_on_inactive_transition = False
        self.pending_refresh_after_live_game_load = False
        self.prefer_persisted_current_game_on_startup = False
        self._current_game_last_key: tuple[str, int] | None = None
        self._current_game_details_cache: dict[tuple[str, int], dict[str, object]] = {}
        self._current_game_images_cache: dict[tuple[str, int], dict[str, bytes]] = {}
        self._image_bytes_cache: dict[str, bytes] = {}
        self._achievement_translation_pending: dict[str, bool] = {}
        self._achievement_translation_lock = threading.Lock()
        self._http_session = requests.Session()
        self.sync_in_progress = False
        self.auto_sync_job: str | None = None
        self.event_sync_job: str | None = None
        self._last_event_sync_request_monotonic = 0.0
        self.pending_event_sync_reason = ""
        self.startup_init_job: str | None = None
        self.startup_finish_job: str | None = None
        self.startup_connection_job: str | None = None
        self._startup_loader_wait_remaining = 0
        self.saved_window_geometry_apply_job: str | None = None
        self._saved_window_geometry_pending = ""
        self._saved_window_geometry_retry_remaining = 0
        self.emulator_poll_job: str | None = None
        self.emulator_status_refresh_job: str | None = None
        self.emulator_probe_in_progress = False
        self._emulator_probe_candidate_live: bool | None = None
        self._emulator_probe_candidate_count = 0
        self._last_emulator_probe_live = False
        self._pending_emulator_status_force_refresh = False
        self._last_emulator_status_refresh_monotonic = 0.0
        self.event_probe_in_progress = False
        self._event_watch_username = ""
        self._event_watch_game_id = 0
        self._event_watch_unlock_marker = ""
        self._event_pending_game_id = 0
        self._event_pending_unlock_marker = ""
        self.has_saved_connection_record = False
        self.is_closing = False
        self.debug_logger: logging.Logger | None = None
        self.debug_log_file = ""
        self._last_probe_signature_by_name: dict[str, tuple[float, str]] = {}
        probes_flag = os.getenv("PYRA_PROBES", "1").strip().casefold()
        self.probes_enabled = probes_flag not in {"0", "false", "off", "no"}

        self._setup_debug_logger()

        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")
        self._apply_theme("light")

        self._build_menu()
        self._build_ui()
        self.root.after_idle(self._apply_rounded_corners_to_widget_tree)
        self._load_config()
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.bind_all("<Motion>", self._on_global_pointer_motion, add="+")
        self.root.report_callback_exception = self._on_tk_callback_exception
        self.root.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self._show_startup_loader()
        self.startup_init_job = self.root.after(30, self._run_startup_sequence)

    # Method: _show_startup_loader - Affiche une barre de chargement au démarrage.
    def _show_startup_loader(self) -> None:
        if self.startup_loader_frame is not None and self.startup_loader_frame.winfo_exists():
            return
        self._show_modal_overlay()
        container = ttk.Frame(self.root, style="Modal.TFrame", padding=(16, 14, 16, 12))
        container.place(relx=0.5, rely=0.5, anchor="center")
        container.columnconfigure(0, weight=1)
        label = ttk.Label(container, text="Initialisation de PyRA...", style="Modal.TLabel", anchor="center", justify="center")
        label.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        progress = ttk.Progressbar(container, orient=HORIZONTAL, mode="determinate", maximum=100, value=5, length=320)
        progress.grid(row=1, column=0, sticky="ew")
        container.lift()
        self.startup_loader_frame = container
        self.startup_loader_label = label
        self.startup_loader_progress = progress
        self.root.after_idle(lambda: self._apply_rounded_corners_to_widget_tree(container))
        self.status_text.set("Initialisation en cours...")
        self.root.update_idletasks()

    # Method: _set_startup_loader_progress - Met à jour la barre de chargement et le message affiché.
    def _set_startup_loader_progress(self, value: int, message: str) -> None:
        if self.startup_loader_label is not None and self.startup_loader_label.winfo_exists():
            self.startup_loader_label.configure(text=message)
        if self.startup_loader_progress is not None and self.startup_loader_progress.winfo_exists():
            bounded = max(0, min(100, int(value)))
            self.startup_loader_progress.configure(value=bounded)
        self.status_text.set(message)
        self.root.update_idletasks()

    # Method: _hide_startup_loader - Masque le panneau de chargement affiché au lancement.
    def _hide_startup_loader(self) -> None:
        panel = self.startup_loader_frame
        if panel is not None and panel.winfo_exists():
            panel.place_forget()
            panel.destroy()
        self.startup_loader_frame = None
        self.startup_loader_label = None
        self.startup_loader_progress = None
        if self._active_modal_window() is None:
            self._hide_modal_overlay()
        else:
            self._sync_modal_overlay()

    # Method: _run_startup_sequence - Exécute l'initialisation pilotée par la barre de progression.
    def _run_startup_sequence(self) -> None:
        self.startup_init_job = None
        if self.is_closing:
            return
        self._set_startup_loader_progress(20, "Chargement des données locales...")
        self.refresh_dashboard(show_errors=False, sync_before_refresh=False)
        if self.is_closing:
            return
        self._set_startup_loader_progress(55, "Activation de la synchronisation par événement...")
        self._request_event_sync("démarrage", delay_ms=450)
        if self.is_closing:
            return
        self._set_startup_loader_progress(80, "Vérification de l'émulateur...")
        self._prime_emulator_status_on_startup()
        self._restart_emulator_probe(immediate=True)
        self._restart_auto_sync(immediate=True)
        if self.is_closing:
            return
        self._set_startup_loader_progress(100, "Initialisation terminée.")
        self._startup_loader_wait_remaining = 420
        self.startup_finish_job = self.root.after(180, self._finish_startup_sequence)

    # Method: _are_startup_sections_fully_rendered - Vérifie que les sections principales sont entièrement rendues.
    def _are_startup_sections_fully_rendered(self) -> bool:
        if self.current_game_fetch_in_progress:
            return False
        if self.current_game_badge_loader_in_progress:
            return False
        if not self._are_current_game_achievement_tiles_rendered():
            return False
        return True

    # Method: _finish_startup_sequence - Termine l'initialisation du bootstrap visuel.
    def _finish_startup_sequence(self) -> None:
        self.startup_finish_job = None
        if self.is_closing:
            return
        if not self._are_startup_sections_fully_rendered():
            if self._startup_loader_wait_remaining > 0:
                self._startup_loader_wait_remaining -= 1
                self._set_startup_loader_progress(100, "Finalisation de l'affichage...")
                try:
                    self.startup_finish_job = self.root.after(120, self._finish_startup_sequence)
                except TclError:
                    self.startup_finish_job = None
                return
            self._debug_log("_finish_startup_sequence timeout: sections incomplètes, fermeture forcée du loader.")
        self._startup_loader_wait_remaining = 0
        self._hide_startup_loader()
        self.status_text.set("Prêt")
        self.startup_connection_job = self.root.after(120, self._open_connection_if_missing)

    # Method: _setup_debug_logger - Initialise le fichier debug.log pour diagnostiquer les problèmes d'affichage.
    def _setup_debug_logger(self) -> None:
        if self.debug_logger is not None:
            return
        logger = get_debug_logger()
        self.debug_logger = logger
        for handler in logger.handlers:
            filename = getattr(handler, "baseFilename", "")
            if filename:
                self.debug_log_file = str(filename)
                break

    # Method: _debug_log - Écrit un message de diagnostic dans debug.log quand le logger est disponible.
    def _debug_log(self, message: str) -> None:
        logger = self.debug_logger
        if logger is None or not logger.handlers:
            return
        try:
            logger.info(message)
        except Exception:
            return

    # Method: _probe - Écrit une sonde structurée dans debug.log pour diagnostiquer les transitions d'état.
    def _probe(self, name: str, **fields: object) -> None:
        if not self.probes_enabled:
            return
        parts: list[str] = []
        for key in sorted(fields.keys()):
            value = fields.get(key)
            if isinstance(value, str):
                text = " ".join(value.split())
            else:
                text = str(value)
            text = text.replace("|", "/")
            if len(text) > 180:
                text = f"{text[:177]}..."
            parts.append(f"{key}={text}")
        message = f"{PROBE_LOG_PREFIX} {name}"
        if parts:
            message = f"{message} | " + " | ".join(parts)

        now = time.monotonic()
        previous = self._last_probe_signature_by_name.get(name)
        if previous is not None:
            previous_time, previous_signature = previous
            if (
                previous_signature == message
                and (now - previous_time) < PROBE_LOG_REPEAT_MIN_INTERVAL_SECONDS
            ):
                return
        self._last_probe_signature_by_name[name] = (now, message)
        self._debug_log(message)

    # Method: _on_tk_callback_exception - Journalise les exceptions Tk non gerees.
    def _on_tk_callback_exception(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: object,
    ) -> None:
        logger = self.debug_logger or get_debug_logger()
        try:
            logger.exception(
                "Exception non geree (Tk callback)",
                exc_info=(exc_type, exc_value, exc_tb),
            )
        except Exception:
            pass
        self.status_text.set(f"Erreur UI: {exc_value}")

    # Method: _resolve_window_icon_path - Détermine le chemin d'icône utilisable pour la fenêtre.
    def _resolve_window_icon_path(self) -> Path | None:
        search_dirs: list[Path] = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            search_dirs.append(Path(meipass))
        search_dirs.append(Path(sys.executable).resolve().parent)
        search_dirs.append(Path(__file__).resolve().parent)
        search_dirs.append(Path.cwd())
        for directory in search_dirs:
            for icon_name in (
                "icon.ico",
                "app.ico",
                "PyRA.ico",
                "trophy_flames_all_sizes.ico",
                "PyRA.generated.ico",
            ):
                candidate = directory / icon_name
                if candidate.exists():
                    return candidate
        return None

    # Method: _apply_window_icon - Applique l'icône de la fenêtre principale si elle est disponible.
    def _apply_window_icon(self) -> None:
        icon_path = self._resolve_window_icon_path()
        if icon_path is None:
            return
        try:
            self.root.iconbitmap(default=str(icon_path))
        except TclError:
            return

    # Method: _apply_rounded_window_corners - Demande des coins arrondis via DWM quand disponible.
    def _apply_rounded_window_corners(self, window: Tk | Toplevel) -> None:
        if os.name != "nt" or _dwm_set_window_attribute is None:
            return
        try:
            hwnd = wintypes.HWND(window.winfo_id())
        except TclError:
            return

        preference = ctypes.c_int(DWMWCP_ROUND_SMALL)
        try:
            _dwm_set_window_attribute(
                hwnd,
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(preference),
                ctypes.sizeof(preference),
            )
        except Exception:
            return

    # Method: _is_roundable_widget_class - Indique si la classe du widget supporte un arrondi global.
    def _is_roundable_widget_class(self, widget_class: str) -> bool:
        return widget_class in {
            "TButton",
            "Button",
            "TEntry",
            "Entry",
            "TCombobox",
            "TFrame",
            "Frame",
            "TLabelframe",
            "LabelFrame",
            "TNotebook",
            "Treeview",
            "Canvas",
            "TProgressbar",
            "TScrollbar",
        }

    # Method: _corner_radius_for_widget_class - Renvoie le rayon adapté selon la famille de widget.
    def _corner_radius_for_widget_class(self, widget_class: str) -> int:
        if widget_class in {"TButton", "Button", "TEntry", "Entry", "TCombobox"}:
            return 6
        if widget_class in {"TScrollbar", "TProgressbar"}:
            return 5
        if widget_class == "Treeview":
            return 6
        if widget_class in {"TLabelframe", "LabelFrame"}:
            return 10
        if widget_class in {"TFrame", "Frame"}:
            return 8
        return DEFAULT_WIDGET_CORNER_RADIUS

    # Method: _apply_rounded_widget_region - Applique une région arrondie à un widget natif Windows.
    def _apply_rounded_widget_region(self, widget: object) -> None:
        if os.name != "nt" or _set_window_rgn is None or _create_round_rect_rgn is None:
            return
        if isinstance(widget, (Tk, Toplevel)):
            self._apply_rounded_window_corners(widget)
            return
        if not hasattr(widget, "winfo_exists") or not hasattr(widget, "winfo_class"):
            return
        try:
            if not bool(widget.winfo_exists()):
                return
            widget_class = str(widget.winfo_class())
        except TclError:
            return

        if not self._is_roundable_widget_class(widget_class):
            return
        try:
            width = int(widget.winfo_width())
            height = int(widget.winfo_height())
        except (TclError, ValueError, TypeError):
            return
        if width < 4 or height < 4:
            return

        base_radius = self._corner_radius_for_widget_class(widget_class)
        radius = max(2, min(base_radius, min(width, height) // 2))
        diameter = max(2, radius * 2)
        try:
            hwnd = wintypes.HWND(widget.winfo_id())
        except TclError:
            return
        region = _create_round_rect_rgn(0, 0, width + 1, height + 1, diameter, diameter)
        if not region:
            return
        result = _set_window_rgn(hwnd, region, True)
        if result == 0 and _delete_gdi_object is not None:
            try:
                _delete_gdi_object(region)
            except Exception:
                return

    # Method: _apply_borderless_widget_options - Force les options Tk sans bordure quand elles existent.
    def _apply_borderless_widget_options(self, widget: object) -> None:
        if not hasattr(widget, "configure"):
            return
        for option, value in (
            ("borderwidth", 0),
            ("bd", 0),
            ("highlightthickness", 0),
            ("relief", "flat"),
        ):
            try:
                widget.configure(**{option: value})
            except (TclError, TypeError):
                continue

    # Method: _apply_rounded_region_with_radius - Applique un rayon explicite sur un widget.
    def _apply_rounded_region_with_radius(self, widget: object, radius: int) -> None:
        if os.name != "nt" or _set_window_rgn is None or _create_round_rect_rgn is None:
            return
        if not hasattr(widget, "winfo_exists") or not hasattr(widget, "winfo_id"):
            return
        try:
            if not bool(widget.winfo_exists()):
                return
            width = int(widget.winfo_width())
            height = int(widget.winfo_height())
        except (TclError, ValueError, TypeError):
            return
        if width < 4 or height < 4:
            return

        inset = max(0, int(IMAGE_ROUNDING_INSET))
        clipped_width = width - (inset * 2)
        clipped_height = height - (inset * 2)
        if clipped_width < 4 or clipped_height < 4:
            return

        bounded_radius = max(2, min(int(radius), min(width, height) // 2))
        diameter = max(2, bounded_radius * 2)
        try:
            hwnd = wintypes.HWND(widget.winfo_id())
        except TclError:
            return
        region = _create_round_rect_rgn(
            inset,
            inset,
            width - inset + 1,
            height - inset + 1,
            diameter,
            diameter,
        )
        if not region:
            return
        result = _set_window_rgn(hwnd, region, True)
        if result == 0 and _delete_gdi_object is not None:
            try:
                _delete_gdi_object(region)
            except Exception:
                return

    # Method: _track_rounded_image_widget - Enregistre un widget image pour un arrondi persistant au redimensionnement.
    def _track_rounded_image_widget(self, widget: object, radius: int) -> None:
        if os.name != "nt" or not hasattr(widget, "bind"):
            return
        key = f"{widget}|{radius}"
        if key in self._rounded_image_widget_bindings:
            return
        self._rounded_image_widget_bindings.add(key)
        try:
            widget.bind(
                "<Configure>",
                lambda _event, w=widget, r=radius: self._apply_rounded_region_with_radius(w, r),
                add="+",
            )
        except TclError:
            return
        self.root.after_idle(lambda w=widget, r=radius: self._apply_rounded_region_with_radius(w, r))

    # Method: _apply_rounded_corners_to_widget_tree - Active l'arrondi automatique sur tout l'arbre de widgets.
    def _apply_rounded_corners_to_widget_tree(self, root_widget: object | None = None) -> None:
        if os.name != "nt":
            return
        start = self.root if root_widget is None else root_widget
        if not hasattr(start, "winfo_exists") or not hasattr(start, "winfo_children"):
            return

        stack: list[object] = [start]
        while stack:
            current = stack.pop()
            if not hasattr(current, "winfo_exists"):
                continue
            try:
                if not bool(current.winfo_exists()):
                    continue
            except TclError:
                continue

            self._apply_borderless_widget_options(current)
            self._apply_rounded_widget_region(current)

            should_track = isinstance(current, (Tk, Toplevel))
            if not should_track and hasattr(current, "winfo_class"):
                try:
                    should_track = self._is_roundable_widget_class(str(current.winfo_class()))
                except TclError:
                    should_track = False
            key = str(current)
            if should_track and key not in self._rounded_widget_bindings and hasattr(current, "bind"):
                try:
                    current.bind("<Configure>", lambda _event, w=current: self._apply_rounded_widget_region(w), add="+")
                    self._rounded_widget_bindings.add(key)
                except TclError:
                    pass

            if hasattr(current, "winfo_children"):
                try:
                    children = list(current.winfo_children())
                except TclError:
                    continue
                for child in children:
                    stack.append(child)    # Method: _clear_current_game_details - Réinitialise les données ciblées.
    def _clear_current_game_details(self, note: str) -> None:
        self._debug_log(f"_clear_current_game_details note='{note}'")
        self.current_game_title.set("-")
        self.current_game_console.set("-")
        self.current_game_progress.set("-")
        self.current_game_last_unlock.set("-")
        self._set_current_game_source("Inconnu")
        self.current_game_note.set(note)
        self.current_game_next_achievement_title.set(ACHIEVEMENT_NA_VALUE)
        self.current_game_next_achievement_description.set(ACHIEVEMENT_NA_VALUE)
        self.current_game_next_achievement_points.set(ACHIEVEMENT_NA_VALUE)
        self.current_game_next_achievement_unlocks.set(ACHIEVEMENT_NA_VALUE)
        self.current_game_next_achievement_feasibility.set(ACHIEVEMENT_NA_VALUE)
        self.current_game_achievements_note.set("Aucun succès à afficher.")
        self.current_game_locked_achievements = []
        self.current_game_locked_achievement_index = 0
        self._clear_current_game_clicked_achievement_selection()
        self.current_game_last_clicked_achievement_key = ""
        self.current_game_last_clicked_achievement_click_monotonic = 0.0
        self._refresh_achievement_navigation_buttons_state()
        self._current_game_last_key = None
        self.prefer_persisted_current_game_on_startup = False
        self._current_game_fetch_token += 1
        self.current_game_fetch_in_progress = False
        if self.current_game_info_tree is not None:
            self.current_game_info_tree.delete(*self.current_game_info_tree.get_children())
        self.current_game_achievement_data = []
        self._refresh_achievement_navigation_buttons_state()
        self.current_game_active_images = {}
        self._clear_current_game_achievement_gallery()
        self._hide_current_game_achievement_tooltip()
        self.current_game_image_refs = {}
        for label in self.current_game_image_labels.values():
            label.configure(image="", text="Image indisponible")
        self._hide_current_game_loading_overlay()

    # Method: _show_current_game_loading_overlay - Affiche une barre de chargement avant les informations du jeu en cours.
    def _show_current_game_loading_overlay(self, message: str = "Chargement des infos du jeu en cours...") -> None:
        startup_panel = self.startup_loader_frame
        if startup_panel is not None and startup_panel.winfo_exists() and startup_panel.winfo_ismapped():
            self._debug_log("_show_current_game_loading_overlay ignoré: loader de démarrage actif.")
            return
        host = self.current_game_tab_container
        if host is None or not host.winfo_exists():
            self._debug_log("_show_current_game_loading_overlay ignoré: conteneur indisponible.")
            return
        self._begin_loading_timer()
        self._debug_log(f"_show_current_game_loading_overlay message='{message}'")

        overlay = self.current_game_loading_overlay
        if overlay is None or not overlay.winfo_exists():
            overlay = Canvas(host, bg=self.theme_colors.get("root_bg", "#f3f5f8"), highlightthickness=0, bd=0)
            shade_id = overlay.create_rectangle(0, 0, 1, 1, fill="#000000", outline="", stipple="gray25")
            panel = ttk.Frame(overlay, style="Modal.TFrame", padding=(14, 12, 14, 10))
            panel.columnconfigure(0, weight=1)
            label = ttk.Label(panel, text=message, style="Modal.TLabel", anchor="center", justify="center")
            label.grid(row=0, column=0, sticky="ew", pady=(0, 8))
            progress = ttk.Progressbar(panel, orient=HORIZONTAL, mode="indeterminate", length=270)
            progress.grid(row=1, column=0, sticky="ew")
            window_id = overlay.create_window(0, 0, window=panel, anchor="center")
            overlay.bind("<Configure>", self._on_current_game_loading_overlay_configure)
            self.current_game_loading_overlay = overlay
            self.current_game_loading_panel = panel
            self.current_game_loading_label = label
            self.current_game_loading_progress = progress
            self.current_game_loading_window_id = window_id
            self.current_game_loading_shade_id = int(shade_id)
        else:
            if self.current_game_loading_label is not None and self.current_game_loading_label.winfo_exists():
                self.current_game_loading_label.configure(text=message)

        if self.current_game_loading_label is not None and self.current_game_loading_label.winfo_exists():
            self.current_game_loading_label.configure(text=message)
        overlay.place(x=0, y=0, relwidth=1, relheight=1)
        try:
            overlay.tk.call("raise", str(overlay))
        except TclError:
            pass
        overlay.update_idletasks()
        self._reposition_current_game_loading_overlay()
        if self.current_game_loading_progress is not None and self.current_game_loading_progress.winfo_exists():
            self.current_game_loading_progress.start(16)
        self._arm_current_game_loading_hard_timeout()

    # Method: _cancel_current_game_loading_timeout - Annule la temporisation de secours du loader Jeu en cours.
    def _cancel_current_game_loading_timeout(self) -> None:
        job = self.current_game_loading_timeout_job
        if job is None:
            return
        self.current_game_loading_timeout_job = None
        try:
            self.root.after_cancel(job)
        except TclError:
            return

    # Method: _arm_current_game_loading_timeout - Arme un garde-fou pour éviter un loader bloqué.
    def _arm_current_game_loading_timeout(self, fetch_token: int, timeout_ms: int = 20_000) -> None:
        self._cancel_current_game_loading_timeout()
        try:
            self.current_game_loading_timeout_job = self.root.after(
                timeout_ms,
                lambda token=fetch_token: self._on_current_game_loading_timeout(token),
            )
        except TclError:
            self.current_game_loading_timeout_job = None

    # Method: _on_current_game_loading_timeout - Débloque l'UI si le chargement dépasse la durée attendue.
    def _on_current_game_loading_timeout(self, fetch_token: int) -> None:
        self.current_game_loading_timeout_job = None
        if fetch_token != self._current_game_fetch_token:
            return
        self._debug_log(f"_on_current_game_loading_timeout token={fetch_token}")
        self.current_game_fetch_in_progress = False
        self._end_transition_timer()
        self.current_game_note.set("Chargement trop long: affichage des données disponibles.")
        self._finalize_current_game_loading_overlay_after_gallery()

    # Method: _cancel_current_game_loading_hard_timeout - Annule le timeout de sécurité visuelle du loader.
    def _cancel_current_game_loading_hard_timeout(self) -> None:
        job = self.current_game_loading_hard_timeout_job
        if job is None:
            return
        self.current_game_loading_hard_timeout_job = None
        try:
            self.root.after_cancel(job)
        except TclError:
            return

    # Method: _arm_current_game_loading_hard_timeout - Arme un timeout global pour éviter un loader bloqué.
    def _arm_current_game_loading_hard_timeout(self, timeout_ms: int = CURRENT_GAME_LOADING_OVERLAY_MAX_MS) -> None:
        self._cancel_current_game_loading_hard_timeout()
        try:
            self.current_game_loading_hard_timeout_job = self.root.after(
                timeout_ms,
                self._on_current_game_loading_hard_timeout,
            )
        except TclError:
            self.current_game_loading_hard_timeout_job = None

    # Method: _on_current_game_loading_hard_timeout - Coupe l'overlay si aucun chemin de fin n'a été exécuté.
    def _on_current_game_loading_hard_timeout(self) -> None:
        self.current_game_loading_hard_timeout_job = None
        overlay = self.current_game_loading_overlay
        if overlay is None or not overlay.winfo_exists() or not overlay.winfo_ismapped():
            return
        self._debug_log("_on_current_game_loading_hard_timeout déclenché")
        self.current_game_fetch_in_progress = False
        self._end_transition_timer()
        note = self.current_game_note.get().strip()
        if not note or note == "-" or "Détection du jeu en cours" in note:
            self.current_game_note.set("Chargement interrompu: affichage partiel (timeout sécurité).")
        self._finalize_current_game_loading_overlay_after_gallery()

    # Method: _on_current_game_loading_overlay_configure - Ajuste la zone sombre et le centrage du panneau de chargement.
    def _on_current_game_loading_overlay_configure(self, event: object) -> None:
        overlay = self.current_game_loading_overlay
        if overlay is None or not overlay.winfo_exists():
            return
        width = int(getattr(event, "width", 0))
        height = int(getattr(event, "height", 0))
        if self.current_game_loading_shade_id is not None:
            overlay.coords(self.current_game_loading_shade_id, 0, 0, width, height)
        self._reposition_current_game_loading_overlay(width=width, height=height)

    # Method: _reposition_current_game_loading_overlay - Centre le panneau de chargement dans l'onglet Jeu en cours.
    def _reposition_current_game_loading_overlay(self, width: int | None = None, height: int | None = None) -> None:
        overlay = self.current_game_loading_overlay
        if overlay is None or not overlay.winfo_exists() or self.current_game_loading_window_id is None:
            return
        w = width if width is not None else overlay.winfo_width()
        h = height if height is not None else overlay.winfo_height()
        if w <= 0 or h <= 0:
            return
        overlay.coords(self.current_game_loading_window_id, w / 2.0, h / 2.0)

    # Method: _hide_current_game_loading_overlay - Masque le chargement affiché au-dessus de l'onglet Jeu en cours.
    def _hide_current_game_loading_overlay(self) -> None:
        self._debug_log("_hide_current_game_loading_overlay")
        self._end_loading_timer()
        self._cancel_current_game_loading_timeout()
        self._cancel_current_game_loading_hard_timeout()
        if self.current_game_loading_progress is not None and self.current_game_loading_progress.winfo_exists():
            self.current_game_loading_progress.stop()
        overlay = self.current_game_loading_overlay
        if overlay is None or not overlay.winfo_exists():
            return
        overlay.place_forget()

    # Method: _finalize_current_game_loading_overlay - Termine le cycle de chargement après rendu complet des sections.
    def _finalize_current_game_loading_overlay(self) -> None:
        self._finalize_current_game_loading_overlay_after_gallery()

    # Method: _are_current_game_achievement_tiles_rendered - Vérifie que chaque tuile de succès est effectivement rendue.
    def _are_current_game_achievement_tiles_rendered(self) -> bool:
        expected_count = max(0, int(self.current_game_expected_achievement_tiles_count))
        if expected_count > 0 and len(self.current_game_achievement_tiles) < expected_count:
            return False
        if not self.current_game_achievement_tiles:
            return True
        for tile in self.current_game_achievement_tiles:
            if not tile.winfo_exists() or not tile.winfo_ismapped():
                return False
            try:
                image_name = str(tile.cget("image")).strip()
                text_value = str(tile.cget("text")).strip()
            except TclError:
                return False
            if not image_name and text_value != "N/A":
                return False
        return True

    # Method: _count_rendered_current_game_achievement_tiles - Retourne le nombre de tuiles de succès effectivement rendues.
    def _count_rendered_current_game_achievement_tiles(self) -> int:
        rendered = 0
        for tile in self.current_game_achievement_tiles:
            if not tile.winfo_exists() or not tile.winfo_ismapped():
                continue
            try:
                image_name = str(tile.cget("image")).strip()
                text_value = str(tile.cget("text")).strip()
            except TclError:
                continue
            if image_name or text_value == "N/A":
                rendered += 1
        return rendered

    # Method: _has_missing_current_game_achievement_badges - Indique si des tuiles restent en N/A sans image.
    def _has_missing_current_game_achievement_badges(self) -> bool:
        for tile in self.current_game_achievement_tiles:
            if not tile.winfo_exists():
                continue
            try:
                image_name = str(tile.cget("image")).strip()
                text_value = str(tile.cget("text")).strip()
            except TclError:
                continue
            if not image_name and text_value == "N/A":
                return True
        return False

    # Method: _finalize_current_game_loading_overlay_after_gallery - Attend le rendu complet de la galerie (y compris badges différés) avant de masquer l'overlay.
    def _finalize_current_game_loading_overlay_after_gallery(self, remaining_checks: int = 420) -> None:
        try:
            self.root.update_idletasks()
        except TclError:
            self._hide_current_game_loading_overlay()
            return
        # Tant que le worker de badges est actif, la galerie n'est pas prête pour un affichage complet.
        if self.current_game_badge_loader_in_progress:
            self.root.after(
                60,
                lambda: self._finalize_current_game_loading_overlay_after_gallery(max(0, remaining_checks - 1)),
            )
            return
        if self._are_current_game_achievement_tiles_rendered():
            self._hide_current_game_loading_overlay()
            return
        rendered_count = self._count_rendered_current_game_achievement_tiles()
        expected_count = max(
            int(self.current_game_expected_achievement_tiles_count),
            len(self.current_game_achievement_tiles),
        )
        self._probe(
            "loading_overlay_wait_incomplete_gallery",
            rendered=rendered_count,
            expected=expected_count,
            badge_loader=self.current_game_badge_loader_in_progress,
            remaining_checks=remaining_checks,
        )
        now = time.monotonic()
        if (now - self._last_loading_overlay_incomplete_log_monotonic) >= 1.0:
            self._last_loading_overlay_incomplete_log_monotonic = now
            self._debug_log(
                f"modal gardé actif: rendu incomplet {rendered_count}/{expected_count}"
            )
        if remaining_checks <= 0:
            self._debug_log("_finalize_current_game_loading_overlay_after_gallery attente prolongée: rendu incomplet.")
        self.root.after(
            60,
            lambda: self._finalize_current_game_loading_overlay_after_gallery(max(0, remaining_checks - 1)),
        )

    # Method: _source_label_style - Détermine le style à appliquer selon la source détectée.
    def _source_label_style(self, source_value: str) -> str:
        if self._is_live_source_label(source_value):
            return "CurrentSourceLive.TLabel"
        if self._is_fallback_source_label(source_value):
            return "CurrentSourceFallback.TLabel"
        return "CurrentSourceUnknown.TLabel"

    # Method: _is_live_source_label - Indique si la source correspond à une détection directe du jeu en cours.
    def _is_live_source_label(self, source_value: str) -> bool:
        lowered = source_value.strip().lower()
        return lowered.startswith("direct") or lowered.startswith("live")

    # Method: _is_fallback_source_label - Indique si la source correspond à un repli local.
    def _is_fallback_source_label(self, source_value: str) -> bool:
        lowered = source_value.strip().lower()
        return lowered.startswith("secours") or lowered.startswith("fallback")

    # Method: _set_current_game_source - Met à jour la source de détection et son style visuel.
    def _set_current_game_source(self, source_value: str) -> None:
        normalized = source_value.strip() or "Inconnu"
        self.current_game_source.set(normalized)
        if self.current_game_source_value_label is not None:
            self.current_game_source_value_label.configure(style=self._source_label_style(normalized))

    # Method: _set_current_game_info_rows - Met à jour la valeur ou l'état associé.
    def _set_current_game_info_rows(self, rows: list[tuple[str, str]]) -> None:
        if self.current_game_info_tree is None:
            return
        self.current_game_info_tree.delete(*self.current_game_info_tree.get_children())
        for field, value in rows:
            self.current_game_info_tree.insert("", END, values=(field, value))

    # Method: _apply_current_game_cached_details - Réapplique les détails/images depuis le cache mémoire pour éviter un écran vide.
    def _apply_current_game_cached_details(self, key: tuple[str, int]) -> bool:
        details = self._current_game_details_cache.get(key)
        if not isinstance(details, dict):
            return False

        raw_next = details.get("next_achievement")
        next_achievement = dict(raw_next) if isinstance(raw_next, dict) else None
        raw_achievements = details.get("achievements")
        achievements = [dict(item) for item in raw_achievements if isinstance(item, dict)] if isinstance(raw_achievements, list) else []
        raw_images = self._current_game_images_cache.get(key, {})
        images = raw_images if isinstance(raw_images, dict) else {}

        self._set_current_game_achievement_rows(next_achievement, has_achievements=bool(achievements))
        self._set_current_game_achievement_gallery(achievements, images)
        self._set_current_game_images(images)
        preferred_next = (
            next_achievement if self.current_game_achievement_order_mode == ACHIEVEMENT_ORDER_NORMAL else None
        )
        self._sync_locked_achievement_navigation(achievements, preferred_next)
        return True

    # Method: _clear_current_game_achievement_gallery - Réinitialise la galerie de succès du jeu courant.
    def _clear_current_game_achievement_gallery(self) -> None:
        self._stop_current_game_achievement_auto_scroll()
        self.current_game_achievement_refs = {}
        self.current_game_achievement_tiles = []
        self.current_game_expected_achievement_tiles_count = 0
        self.current_game_achievement_tile_by_key = {}
        self.current_game_badge_loader_token += 1
        self.current_game_badge_loader_in_progress = False
        self.current_game_achievement_scroll_direction = 1
        self.current_game_achievement_hovered = False
        self.current_game_achievement_tooltip_left_side = False
        self._current_game_gallery_columns = 0
        self._current_game_gallery_rows = 0
        if self.current_game_achievements_inner is None:
            return
        for child in self.current_game_achievements_inner.winfo_children():
            child.destroy()
        if self.current_game_achievements_canvas is not None:
            self.current_game_achievements_canvas.yview_moveto(0.0)
            self.current_game_achievements_canvas.configure(scrollregion=(0, 0, 0, 0))

    # Method: _stop_current_game_achievement_auto_scroll - Arrête le défilement automatique de la galerie.
    def _stop_current_game_achievement_auto_scroll(self) -> None:
        if self.current_game_achievement_scroll_job is None:
            return
        try:
            self.root.after_cancel(self.current_game_achievement_scroll_job)
        except TclError:
            pass
        self.current_game_achievement_scroll_job = None

    # Method: _should_auto_scroll_current_game_achievements - Détermine si le défilement automatique doit être actif.
    def _should_auto_scroll_current_game_achievements(self) -> bool:
        if not self.current_game_achievement_tiles:
            return False
        canvas = self.current_game_achievements_canvas
        if canvas is None or not canvas.winfo_exists():
            return False
        try:
            first, last = canvas.yview()
        except TclError:
            return False
        visible_span = max(0.0, float(last) - float(first))
        return visible_span < 0.999

    # Method: _restart_current_game_achievement_auto_scroll - Planifie le prochain déplacement automatique.
    def _restart_current_game_achievement_auto_scroll(self, immediate: bool = False) -> None:
        if self.is_closing:
            return
        if not self._should_auto_scroll_current_game_achievements():
            self._stop_current_game_achievement_auto_scroll()
            return
        self._stop_current_game_achievement_auto_scroll()
        delay = 220 if immediate else ACHIEVEMENT_SCROLL_INTERVAL_MS
        self.current_game_achievement_scroll_job = self.root.after(delay, self._tick_current_game_achievement_auto_scroll)

    # Method: _tick_current_game_achievement_auto_scroll - Fait défiler la galerie de haut en bas puis inverse le sens.
    def _tick_current_game_achievement_auto_scroll(self) -> None:
        self.current_game_achievement_scroll_job = None
        if self.is_closing:
            return
        canvas = self.current_game_achievements_canvas
        if canvas is None or not canvas.winfo_exists():
            return
        if not self.current_game_achievement_tiles:
            return
        if not self._should_auto_scroll_current_game_achievements():
            return
        if self.current_game_achievement_hovered:
            self._restart_current_game_achievement_auto_scroll(immediate=False)
            return

        before_first, before_last = canvas.yview()
        canvas.yview_scroll(self.current_game_achievement_scroll_direction, "units")
        after_first, after_last = canvas.yview()
        no_movement = abs(after_first - before_first) < 1e-9 and abs(after_last - before_last) < 1e-9
        if no_movement:
            self.current_game_achievement_scroll_direction *= -1
            canvas.yview_scroll(self.current_game_achievement_scroll_direction, "units")
            after_first, after_last = canvas.yview()

        if after_last >= 0.999:
            self.current_game_achievement_scroll_direction = -1
        elif after_first <= 0.001:
            self.current_game_achievement_scroll_direction = 1
        self._restart_current_game_achievement_auto_scroll(immediate=False)

    # Method: _on_current_game_gallery_canvas_configure - Ajuste la largeur interne et réorganise la grille des succès.
    def _on_current_game_gallery_canvas_configure(self, event: object) -> None:
        if self.current_game_achievements_canvas is None or self.current_game_achievements_window_id is None:
            return
        width = int(getattr(event, "width", 0))
        if width > 0:
            self.current_game_achievements_canvas.itemconfigure(self.current_game_achievements_window_id, width=width)
        self._layout_current_game_achievement_gallery(width_hint=width)
        region = self.current_game_achievements_canvas.bbox("all")
        self.current_game_achievements_canvas.configure(scrollregion=region if region is not None else (0, 0, 0, 0))
        if self._should_auto_scroll_current_game_achievements():
            if not self.current_game_achievement_hovered and self.current_game_achievement_scroll_job is None:
                self._restart_current_game_achievement_auto_scroll(immediate=False)
        else:
            self._stop_current_game_achievement_auto_scroll()
            self.current_game_achievements_canvas.yview_moveto(0.0)

    # Method: _layout_current_game_achievement_gallery - Organise les badges selon la largeur disponible.
    def _layout_current_game_achievement_gallery(self, width_hint: int | None = None) -> None:
        if self.current_game_achievements_inner is None or not self.current_game_achievement_tiles:
            self._current_game_gallery_columns = 0
            self._current_game_gallery_rows = 0
            return
        width = width_hint if width_hint and width_hint > 0 else 0
        if width <= 0 and self.current_game_achievements_canvas is not None:
            width = self.current_game_achievements_canvas.winfo_width()
        if width <= 0:
            width = max(220, self.root.winfo_width() - 80)

        tile_span = 74
        spacing = 3
        columns = max(1, width // tile_span)
        if columns != self._current_game_gallery_columns:
            for col in range(self._current_game_gallery_columns):
                self.current_game_achievements_inner.columnconfigure(col, weight=0, minsize=0, uniform="")
            for col in range(columns):
                self.current_game_achievements_inner.columnconfigure(
                    col,
                    weight=1,
                    minsize=tile_span,
                    uniform="achievement_col",
                )
            self._current_game_gallery_columns = columns

        rows = (len(self.current_game_achievement_tiles) + columns - 1) // columns
        if rows != self._current_game_gallery_rows:
            for row in range(self._current_game_gallery_rows):
                self.current_game_achievements_inner.rowconfigure(row, weight=0, minsize=0)
            for row in range(rows):
                self.current_game_achievements_inner.rowconfigure(row, weight=0, minsize=tile_span)
            self._current_game_gallery_rows = rows

        for idx, tile in enumerate(self.current_game_achievement_tiles):
            row = idx // columns
            col = idx % columns
            tile.grid(row=row, column=col, padx=spacing, pady=spacing, sticky="n")

    # Method: _show_current_game_achievement_tooltip - Affiche une infobulle au survol d'un badge avec placement contextuel.
    def _show_current_game_achievement_tooltip(self, text: str, prefer_left: bool = False) -> None:
        tooltip_text = text.strip()
        if not tooltip_text:
            return

        if self.current_game_achievement_tooltip is None or not self.current_game_achievement_tooltip.winfo_exists():
            tip = Toplevel(self.root)
            tip.overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except TclError:
                pass
            label = ttk.Label(tip, style="Tooltip.TLabel", justify="left", anchor="w")
            label.grid(row=0, column=0, sticky="nsew")
            self.current_game_achievement_tooltip = tip
            self.current_game_achievement_tooltip_label = label
            self.root.after_idle(lambda: self._apply_rounded_corners_to_widget_tree(tip))

        if self.current_game_achievement_tooltip_label is not None:
            self.current_game_achievement_tooltip_label.configure(text=tooltip_text)
        self.current_game_achievement_tooltip_left_side = prefer_left
        if self.current_game_achievement_tooltip is not None:
            self.current_game_achievement_tooltip.deiconify()
        self._move_current_game_achievement_tooltip(prefer_left=prefer_left)

    # Method: _should_show_achievement_tooltip_left - Indique si l'infobulle doit passer à gauche (4 colonnes de droite).
    def _should_show_achievement_tooltip_left(self, tile_index: int) -> bool:
        columns = max(1, self._current_game_gallery_columns)
        column = tile_index % columns
        start_right_zone = max(0, columns - 4)
        return column >= start_right_zone

    # Method: _on_current_game_achievement_enter - Met en pause le défilement et affiche l'infobulle du bon côté.
    def _on_current_game_achievement_enter(self, tooltip_text: str, tile_index: int) -> None:
        self.current_game_achievement_hovered = True
        self._stop_current_game_achievement_auto_scroll()
        if tooltip_text.strip():
            prefer_left = self._should_show_achievement_tooltip_left(tile_index)
            tooltip_display = self._get_translated_achievement_tooltip_text(tooltip_text)
            self._show_current_game_achievement_tooltip(tooltip_display, prefer_left=prefer_left)

    # Method: _on_current_game_achievement_motion - Met à jour l'infobulle pendant le survol avec placement adapté.
    def _on_current_game_achievement_motion(self, tooltip_text: str, tile_index: int) -> None:
        if not self.current_game_achievement_hovered:
            return
        if tooltip_text.strip():
            prefer_left = self._should_show_achievement_tooltip_left(tile_index)
            tooltip_display = self._get_translated_achievement_tooltip_text(tooltip_text)
            self._show_current_game_achievement_tooltip(tooltip_display, prefer_left=prefer_left)

    # Method: _get_translated_achievement_tooltip_text - Traduit la description d'infobulle à la demande avec cache.
    def _get_translated_achievement_tooltip_text(self, tooltip_text: str) -> str:
        normalized = tooltip_text.strip()
        if not normalized:
            return ""
        cache = getattr(self, "_achievement_tooltip_translation_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_achievement_tooltip_translation_cache", cache)
        cached = cache.get(normalized)
        if isinstance(cached, str):
            return cached
        if "\n" not in normalized:
            cache[normalized] = normalized
            return normalized
        title_line, description_block = normalized.split("\n", 1)
        description = " ".join(description_block.split())
        if not description:
            cache[normalized] = title_line.strip()
            return cache[normalized]
        translated = self._translate_achievement_description_cached_only(description)
        if translated == description:
            self._schedule_achievement_description_translation(
                description,
                refresh_visible_summary=False,
            )
        wrapped = self._format_tooltip_description_three_lines(translated or description)
        result = f"{title_line.strip()}\n{wrapped}"
        cache[normalized] = result
        return result

    # Method: _on_current_game_achievement_leave - Relance le défilement après le survol.
    def _on_current_game_achievement_leave(self) -> None:
        self.current_game_achievement_hovered = False
        self.current_game_achievement_tooltip_left_side = False
        self._hide_current_game_achievement_tooltip()
        if self._should_auto_scroll_current_game_achievements() and self.current_game_achievement_scroll_job is None:
            self._restart_current_game_achievement_auto_scroll(immediate=False)

    # Method: _move_current_game_achievement_tooltip - Repositionne l'infobulle près du pointeur, à droite ou à gauche.
    def _move_current_game_achievement_tooltip(self, prefer_left: bool | None = None) -> None:
        tooltip = self.current_game_achievement_tooltip
        if tooltip is None or not tooltip.winfo_exists():
            return
        tooltip.update_idletasks()
        use_left = self.current_game_achievement_tooltip_left_side if prefer_left is None else prefer_left
        tooltip_width = max(1, tooltip.winfo_reqwidth())
        pointer_x = self.root.winfo_pointerx()
        pointer_y = self.root.winfo_pointery()
        if use_left:
            x = pointer_x - tooltip_width - 14
        else:
            x = pointer_x + 14
        y = pointer_y + 18
        tooltip.geometry(f"+{x}+{y}")

    # Method: _hide_current_game_achievement_tooltip - Masque l'infobulle de succès si elle est visible.
    def _hide_current_game_achievement_tooltip(self) -> None:
        tooltip = self.current_game_achievement_tooltip
        if tooltip is None:
            return
        if tooltip.winfo_exists():
            tooltip.withdraw()

    # Method: _show_maintenance_tab_tooltip - Affiche une infobulle de maintenance au survol des onglets désactivés.
    def _show_maintenance_tab_tooltip(self, text: str) -> None:
        tooltip_text = text.strip()
        if not tooltip_text:
            return
        if self.maintenance_tab_tooltip is None or not self.maintenance_tab_tooltip.winfo_exists():
            tip = Toplevel(self.root)
            tip.overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except TclError:
                pass
            label = ttk.Label(tip, style="Tooltip.TLabel", justify="left", anchor="w")
            label.grid(row=0, column=0, sticky="nsew")
            self.maintenance_tab_tooltip = tip
            self.maintenance_tab_tooltip_label = label
            self.root.after_idle(lambda: self._apply_rounded_corners_to_widget_tree(tip))
        if self.maintenance_tab_tooltip_label is not None:
            self.maintenance_tab_tooltip_label.configure(text=tooltip_text)
        self._move_maintenance_tab_tooltip()
        if self.maintenance_tab_tooltip is not None:
            self.maintenance_tab_tooltip.deiconify()

    # Method: _move_maintenance_tab_tooltip - Repositionne l'infobulle de maintenance près du pointeur.
    def _move_maintenance_tab_tooltip(self) -> None:
        tooltip = self.maintenance_tab_tooltip
        if tooltip is None or not tooltip.winfo_exists():
            return
        x = self.root.winfo_pointerx() + 12
        y = self.root.winfo_pointery() + 16
        tooltip.geometry(f"+{x}+{y}")

    # Method: _hide_maintenance_tab_tooltip - Masque l'infobulle de maintenance.
    def _hide_maintenance_tab_tooltip(self) -> None:
        tooltip = self.maintenance_tab_tooltip
        if tooltip is None:
            return
        if tooltip.winfo_exists():
            tooltip.withdraw()

    # Method: _show_profile_maintenance_tooltip - Affiche l'infobulle de maintenance pour le bouton/menu Profil.
    def _show_profile_maintenance_tooltip(self, text: str) -> None:
        tooltip_text = text.strip()
        if not tooltip_text:
            return
        if self.profile_maintenance_tooltip is None or not self.profile_maintenance_tooltip.winfo_exists():
            tip = Toplevel(self.root)
            tip.overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except TclError:
                pass
            label = ttk.Label(tip, style="Tooltip.TLabel", justify="left", anchor="w")
            label.grid(row=0, column=0, sticky="nsew")
            self.profile_maintenance_tooltip = tip
            self.profile_maintenance_tooltip_label = label
            self.root.after_idle(lambda: self._apply_rounded_corners_to_widget_tree(tip))
        if self.profile_maintenance_tooltip_label is not None:
            self.profile_maintenance_tooltip_label.configure(text=tooltip_text)
        self._move_profile_maintenance_tooltip()
        if self.profile_maintenance_tooltip is not None:
            self.profile_maintenance_tooltip.deiconify()

    # Method: _move_profile_maintenance_tooltip - Repositionne l'infobulle de maintenance Profil près du pointeur.
    def _move_profile_maintenance_tooltip(self) -> None:
        tooltip = self.profile_maintenance_tooltip
        if tooltip is None or not tooltip.winfo_exists():
            return
        x = self.root.winfo_pointerx() + 12
        y = self.root.winfo_pointery() + 16
        tooltip.geometry(f"+{x}+{y}")

    # Method: _hide_profile_maintenance_tooltip - Masque l'infobulle de maintenance Profil.
    def _hide_profile_maintenance_tooltip(self) -> None:
        tooltip = self.profile_maintenance_tooltip
        if tooltip is None:
            return
        if tooltip.winfo_exists():
            tooltip.withdraw()

    # Method: _on_profile_button_enter - Affiche l'infobulle lors du survol du bouton Profil.
    def _on_profile_button_enter(self, _event: object) -> None:
        self._show_profile_maintenance_tooltip("En maintenance")

    # Method: _on_profile_button_motion - Déplace l'infobulle pendant le survol du bouton Profil.
    def _on_profile_button_motion(self, _event: object) -> None:
        self._show_profile_maintenance_tooltip("En maintenance")

    # Method: _on_profile_button_leave - Masque l'infobulle quand le survol du bouton Profil se termine.
    def _on_profile_button_leave(self, _event: object) -> None:
        self._hide_profile_maintenance_tooltip()

    # Method: _on_file_menu_select - Affiche l'infobulle quand l'entrée Profil du menu Fichier est survolée.
    def _on_file_menu_select(self, _event: object) -> None:
        menu = self.file_menu
        target_index = self.file_menu_profile_index
        if menu is None or target_index is None:
            self._hide_profile_maintenance_tooltip()
            return
        try:
            active = menu.index("active")
        except TclError:
            self._hide_profile_maintenance_tooltip()
            return
        if active == target_index:
            self._show_profile_maintenance_tooltip("En maintenance")
            return
        self._hide_profile_maintenance_tooltip()

    # Method: _on_file_menu_unmap - Masque l'infobulle quand le menu Fichier se ferme.
    def _on_file_menu_unmap(self, _event: object) -> None:
        self._hide_profile_maintenance_tooltip()

    # Method: _on_profile_maintenance_request - Indique que l'ouverture du profil est temporairement désactivée.
    def _on_profile_maintenance_request(self) -> None:
        self.status_text.set("Profil en maintenance.")

    # Method: _is_pointer_over_profile_button - Détermine si le pointeur survole la zone du bouton Profil.
    def _is_pointer_over_profile_button(self) -> bool:
        button = self.profile_button
        if button is None or not button.winfo_exists() or not button.winfo_viewable():
            return False
        pointer_x = self.root.winfo_pointerx()
        pointer_y = self.root.winfo_pointery()
        x = button.winfo_rootx()
        y = button.winfo_rooty()
        width = button.winfo_width()
        height = button.winfo_height()
        return x <= pointer_x < (x + width) and y <= pointer_y < (y + height)

    # Method: _is_pointer_over_profile_menu_item - Détermine si le pointeur survole l'entrée Profil du menu Fichier.
    def _is_pointer_over_profile_menu_item(self) -> bool:
        menu = self.file_menu
        target_index = self.file_menu_profile_index
        if menu is None or target_index is None or not menu.winfo_exists() or not menu.winfo_ismapped():
            return False
        pointer_x = self.root.winfo_pointerx()
        pointer_y = self.root.winfo_pointery()
        menu_x = menu.winfo_rootx()
        menu_y = menu.winfo_rooty()
        menu_w = menu.winfo_width()
        menu_h = menu.winfo_height()
        if not (menu_x <= pointer_x < (menu_x + menu_w) and menu_y <= pointer_y < (menu_y + menu_h)):
            return False
        try:
            hover_index = menu.index(f"@{pointer_y - menu_y}")
        except TclError:
            return False
        return hover_index == target_index

    # Method: _on_global_pointer_motion - Affiche l'infobulle de maintenance du Profil selon la zone survolée.
    def _on_global_pointer_motion(self, _event: object) -> None:
        if self._is_pointer_over_profile_button() or self._is_pointer_over_profile_menu_item():
            self._show_profile_maintenance_tooltip("En maintenance")
            return
        self._hide_profile_maintenance_tooltip()

    # Method: _on_main_tab_button_press - Gère le clic sur un bouton de navigation principal.
    def _on_main_tab_button_press(self, tab_key: str) -> None:
        resolved_tab_key = self._resolve_main_tab_key(tab_key)
        if resolved_tab_key is None:
            return
        button = self.main_tab_buttons.get(resolved_tab_key)
        if button is not None:
            try:
                if button.instate(["disabled"]):
                    self._show_maintenance_tab_tooltip("En maintenance")
                    return
            except TclError:
                return
        self._hide_maintenance_tab_tooltip()
        self._select_main_tab(resolved_tab_key)

    # Method: _resolve_main_tab_key - Convertit une clé ou un libellé d'onglet vers la clé interne canonique.
    def _resolve_main_tab_key(self, tab_key: str) -> str | None:
        raw = self._safe_text(tab_key)
        if not raw:
            return None
        if raw in MAIN_TAB_ORDER:
            return raw
        normalized = raw.casefold()
        aliases = {
            MAIN_TAB_CURRENT: MAIN_TAB_CURRENT,
            MAIN_TAB_LABELS[MAIN_TAB_CURRENT].casefold(): MAIN_TAB_CURRENT,
            "jeu": MAIN_TAB_CURRENT,
            MAIN_TAB_GAMES: MAIN_TAB_GAMES,
            MAIN_TAB_LABELS[MAIN_TAB_GAMES].casefold(): MAIN_TAB_GAMES,
            "game": MAIN_TAB_GAMES,
            MAIN_TAB_RECENT: MAIN_TAB_RECENT,
            MAIN_TAB_LABELS[MAIN_TAB_RECENT].casefold(): MAIN_TAB_RECENT,
            "succes recents": MAIN_TAB_RECENT,
        }
        return aliases.get(normalized)

    # Method: _select_main_tab - Active l'onglet visuel demandé et met à jour le style des boutons.
    def _select_main_tab(self, tab_key: str, force: bool = False) -> None:
        resolved_tab_key = self._resolve_main_tab_key(tab_key)
        if resolved_tab_key is None:
            return
        frame = self.main_tab_frames.get(resolved_tab_key)
        if frame is None or not frame.winfo_exists():
            return
        if not force and self.main_tab_selected_key == resolved_tab_key:
            return
        frame.tkraise()
        self.main_tab_selected_key = resolved_tab_key
        for key, button in self.main_tab_buttons.items():
            if not button.winfo_exists():
                continue
            style_name = "MainTabSelected.TButton" if key == resolved_tab_key else "TButton"
            button.configure(style=style_name)

    # Method: _on_maintenance_tab_button_enter - Affiche l'infobulle au survol d'un bouton d'onglet en maintenance.
    def _on_maintenance_tab_button_enter(self, _event: object) -> None:
        self._show_maintenance_tab_tooltip("En maintenance")

    # Method: _on_maintenance_tab_button_motion - Déplace l'infobulle pendant le survol d'un bouton d'onglet en maintenance.
    def _on_maintenance_tab_button_motion(self, _event: object) -> None:
        self._show_maintenance_tab_tooltip("En maintenance")

    # Method: _on_maintenance_tab_button_leave - Masque l'infobulle de maintenance quand le pointeur quitte le bouton.
    def _on_maintenance_tab_button_leave(self, _event: object) -> None:
        self._hide_maintenance_tab_tooltip()

    # Method: _sanitize_success_points_text - Retire la partie "True ratio" des anciens textes éventuellement en cache.
    def _sanitize_success_points_text(self, points_text: str) -> str:
        text = self._safe_text(points_text)
        if not text:
            return text
        cleaned = re.sub(r"\s*\|\s*true\s*ratio\s*:\s*.*$", "", text, flags=re.IGNORECASE).strip()
        return cleaned or text

    # Method: _set_current_game_achievement_rows - Met à jour le bloc "premier succès non débloqué".
    def _set_current_game_achievement_rows(self, next_achievement: dict[str, str] | None, has_achievements: bool = True) -> None:
        if not next_achievement:
            if has_achievements:
                self.current_game_next_achievement_title.set("Tous les succès sont débloqués.")
                self.current_game_next_achievement_description.set("Aucun succès verrouillé sur ce jeu.")
            else:
                self.current_game_next_achievement_title.set("Aucun succès disponible.")
                self.current_game_next_achievement_description.set("Les informations de succès ne sont pas disponibles.")
            self.current_game_next_achievement_points.set(ACHIEVEMENT_NA_VALUE)
            self.current_game_next_achievement_unlocks.set(ACHIEVEMENT_NA_VALUE)
            self.current_game_next_achievement_feasibility.set(ACHIEVEMENT_NA_VALUE)
            return

        description = self._safe_text(next_achievement.get("description", ACHIEVEMENT_NA_VALUE))
        if description and description not in {"-", ACHIEVEMENT_NA_VALUE}:
            # Ne jamais bloquer l'UI sur un appel réseau de traduction.
            translated_description = self._translate_achievement_description_cached_only(description)
            if translated_description == description:
                self._schedule_achievement_description_translation(
                    description,
                    refresh_visible_summary=True,
                )
            description = translated_description
        points_text = self._sanitize_success_points_text(next_achievement.get("points", ACHIEVEMENT_NA_VALUE))
        self.current_game_next_achievement_title.set(next_achievement.get("title", ACHIEVEMENT_NA_VALUE))
        self.current_game_next_achievement_description.set(description or ACHIEVEMENT_NA_VALUE)
        self.current_game_next_achievement_points.set(points_text or ACHIEVEMENT_NA_VALUE)
        self.current_game_next_achievement_unlocks.set(next_achievement.get("unlocks", ACHIEVEMENT_NA_VALUE))
        self.current_game_next_achievement_feasibility.set(next_achievement.get("feasibility", ACHIEVEMENT_NA_VALUE))

    # Method: _translate_achievement_description_cached_only - Retourne uniquement la traduction déjà en cache (jamais d'appel réseau).
    def _translate_achievement_description_cached_only(self, description: str) -> str:
        text = self._safe_text(description)
        if not text or text in {"-", ACHIEVEMENT_NA_VALUE}:
            return text
        cache = getattr(self, "_achievement_translation_cache", None)
        if not isinstance(cache, dict):
            return text
        normalized = " ".join(text.split())
        cached = cache.get(normalized)
        if isinstance(cached, str) and cached.strip():
            return cached.strip()
        return text

    # Method: _normalize_achievement_description_text - Normalise une description pour les comparaisons et clés de cache.
    def _normalize_achievement_description_text(self, description: str) -> str:
        return " ".join(self._safe_text(description).split())

    # Method: _schedule_achievement_description_translation - Lance une traduction asynchrone si absente du cache.
    def _schedule_achievement_description_translation(
        self,
        description: str,
        refresh_visible_summary: bool = False,
    ) -> None:
        normalized = self._normalize_achievement_description_text(description)
        if not normalized or normalized in {"-", ACHIEVEMENT_NA_VALUE}:
            return
        cached = self._translate_achievement_description_cached_only(normalized)
        if cached and cached != normalized:
            if refresh_visible_summary:
                self._apply_current_game_description_translation_if_visible(normalized, cached)
            return

        with self._achievement_translation_lock:
            existing = self._achievement_translation_pending.get(normalized)
            if existing is not None:
                self._achievement_translation_pending[normalized] = bool(existing or refresh_visible_summary)
                return
            self._achievement_translation_pending[normalized] = bool(refresh_visible_summary)

        worker = threading.Thread(
            target=self._achievement_description_translation_worker,
            args=(normalized,),
            daemon=True,
        )
        worker.start()

    # Method: _achievement_description_translation_worker - Traduit en arrière-plan et notifie l'UI si le texte affiché doit être rafraîchi.
    def _achievement_description_translation_worker(self, source_text: str) -> None:
        translated = source_text
        try:
            translated = self._translate_achievement_description_to_french(source_text) or source_text
        except Exception:
            translated = source_text

        with self._achievement_translation_lock:
            refresh_visible_summary = bool(self._achievement_translation_pending.pop(source_text, False))

        normalized_translated = self._normalize_achievement_description_text(translated)
        if not normalized_translated or normalized_translated == source_text:
            return
        if not refresh_visible_summary:
            return
        self._queue_ui_callback(
            lambda src=source_text, dst=normalized_translated: self._apply_current_game_description_translation_if_visible(
                src,
                dst,
            )
        )

    # Method: _apply_current_game_description_translation_if_visible - Applique la traduction seulement si la même description est encore affichée.
    def _apply_current_game_description_translation_if_visible(self, source_text: str, translated_text: str) -> None:
        source = self._normalize_achievement_description_text(source_text)
        translated = self._normalize_achievement_description_text(translated_text)
        if not source or not translated or source == translated:
            return
        current = self._normalize_achievement_description_text(self.current_game_next_achievement_description.get())
        if current != source:
            return
        self.current_game_next_achievement_description.set(translated)

    # Method: _achievement_order_label_for_mode - Retourne le libellé UI correspondant au mode de tri actif.
    def _achievement_order_label_for_mode(self, mode: str) -> str:
        return ACHIEVEMENT_ORDER_LABELS.get(mode, ACHIEVEMENT_ORDER_LABELS[ACHIEVEMENT_ORDER_NORMAL])

    # Method: _achievement_row_normal_order - Retourne l'ordre "normal" d'une ligne de succès (fallback sur l'index courant).
    def _achievement_row_normal_order(self, item: dict[str, str], fallback_index: int) -> int:
        raw_order_text = self._safe_text(item.get("normal_order"))
        if not raw_order_text:
            return fallback_index
        raw_order = self._safe_int(raw_order_text)
        if raw_order < 0:
            return fallback_index
        return raw_order

    # Method: _achievement_row_difficulty_sort_values - Extrait les champs de tri de difficulté (known, score).
    def _achievement_row_difficulty_sort_values(self, item: dict[str, str]) -> tuple[int, float]:
        known_rank = 0 if self._safe_bool(item.get("difficulty_known", "0")) else 1
        raw_score = self._safe_text(item.get("difficulty_score"))
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            score = 9999.0
        if score < 9999.0:
            return known_rank, score

        # Compatibilité cache/anciens enregistrements: dérive un score depuis "next_feasibility".
        feasibility_text = self._safe_text(item.get("next_feasibility")).casefold()
        if not feasibility_text or feasibility_text == "inconnue":
            return 1, 9999.0

        pct_match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*%\s*des joueurs", feasibility_text)
        if pct_match:
            try:
                unlock_pct = float(pct_match.group(1).replace(",", "."))
                return 0, max(0.0, min(100.0, 100.0 - unlock_pct))
            except ValueError:
                pass

        ratio_match = re.search(r"trueratio\s*([0-9]+(?:[.,][0-9]+)?)", feasibility_text)
        if ratio_match:
            try:
                true_ratio = float(ratio_match.group(1).replace(",", "."))
                if true_ratio > 0:
                    return 0, min(999.0, true_ratio)
            except ValueError:
                pass

        level_scores = (
            ("très facile", 10.0),
            ("tres facile", 10.0),
            ("facile", 30.0),
            ("moyenne", 50.0),
            ("très difficile", 90.0),
            ("tres difficile", 90.0),
            ("difficile", 70.0),
        )
        for marker, fallback_score in level_scores:
            if marker in feasibility_text:
                return 0, fallback_score

        return known_rank, score

    # Method: _order_current_game_achievements - Applique le mode de tri demandé en conservant les succès non débloqués en premier.
    def _order_current_game_achievements(self, achievements: list[dict[str, str]]) -> list[dict[str, str]]:
        indexed_rows: list[tuple[int, dict[str, str]]] = []
        for fallback_index, row in enumerate(achievements):
            if not isinstance(row, dict):
                continue
            indexed_rows.append((fallback_index, dict(row)))

        if not indexed_rows:
            return []

        def normal_key(row: tuple[int, dict[str, str]]) -> tuple[int, int, str, int]:
            fallback_index, item = row
            normal_order = self._achievement_row_normal_order(item, fallback_index)
            ach_id = self._safe_int(item.get("next_id"))
            if ach_id <= 0:
                ach_id = 999_999_999
            title = self._safe_text(item.get("next_title")).casefold()
            return normal_order, ach_id, title, fallback_index

        locked_rows: list[tuple[int, dict[str, str]]] = []
        unlocked_rows: list[tuple[int, dict[str, str]]] = []
        for row in indexed_rows:
            if self._safe_bool(row[1].get("is_unlocked", "0")):
                unlocked_rows.append(row)
            else:
                locked_rows.append(row)

        mode = self.current_game_achievement_order_mode
        if mode == ACHIEVEMENT_ORDER_EASY_TO_HARD:
            def easy_key(row: tuple[int, dict[str, str]]) -> tuple[int, float, tuple[int, int, str, int]]:
                known_rank, score = self._achievement_row_difficulty_sort_values(row[1])
                return known_rank, score, normal_key(row)

            locked_rows.sort(key=easy_key)
        elif mode == ACHIEVEMENT_ORDER_HARD_TO_EASY:
            def hard_key(row: tuple[int, dict[str, str]]) -> tuple[int, float, tuple[int, int, str, int]]:
                known_rank, score = self._achievement_row_difficulty_sort_values(row[1])
                return known_rank, -score, normal_key(row)

            locked_rows.sort(key=hard_key)
        else:
            locked_rows.sort(key=normal_key)

        unlocked_rows.sort(key=normal_key)
        return [item for _, item in (locked_rows + unlocked_rows)]

    # Method: _extract_locked_achievements - Extrait les succès non débloqués pour la navigation précédente/suivante.
    def _extract_locked_achievements(self, achievements: list[dict[str, str]]) -> list[dict[str, str]]:
        locked: list[dict[str, str]] = []
        for item in achievements:
            if not isinstance(item, dict):
                continue
            if self._safe_bool(item.get("is_unlocked", "0")):
                continue
            title = self._safe_text(item.get("next_title"))
            description = self._safe_text(item.get("next_description"))
            if not title:
                tooltip = self._safe_text(item.get("tooltip"))
                if "\n" in tooltip:
                    parts = tooltip.split("\n", 1)
                    title = parts[0].strip()
                    description = parts[1].strip()
                elif tooltip:
                    title = tooltip
            description = self._translate_achievement_description_cached_only(description)
            locked.append(
                {
                    "image_key": self._safe_text(item.get("image_key")),
                    "title": title or ACHIEVEMENT_NA_VALUE,
                    "description": description or ACHIEVEMENT_NA_VALUE,
                    "points": self._safe_text(item.get("next_points")) or ACHIEVEMENT_NA_VALUE,
                    "unlocks": self._safe_text(item.get("next_unlocks")) or ACHIEVEMENT_NA_VALUE,
                    "feasibility": self._safe_text(item.get("next_feasibility")) or ACHIEVEMENT_NA_VALUE,
                }
            )
        return locked

    # Method: _refresh_achievement_navigation_buttons_state - Met à jour l'état des boutons Précédent/Suivant.
    def _refresh_achievement_navigation_buttons_state(self) -> None:
        size = len(self.current_game_locked_achievements)
        index = self.current_game_locked_achievement_index
        self.current_game_achievement_order_label.set(
            self._achievement_order_label_for_mode(self.current_game_achievement_order_mode)
        )

        previous_button = self.current_game_previous_achievement_button
        if previous_button is not None and previous_button.winfo_exists():
            if size > 1 and index > 0:
                previous_button.state(["!disabled"])
            else:
                previous_button.state(["disabled"])

        next_button = self.current_game_next_achievement_button
        if next_button is not None and next_button.winfo_exists():
            if size > 1 and index < (size - 1):
                next_button.state(["!disabled"])
            else:
                next_button.state(["disabled"])

        order_button = self.current_game_achievement_order_button
        if order_button is not None and order_button.winfo_exists():
            if self.current_game_achievement_data:
                order_button.state(["!disabled"])
            else:
                order_button.state(["disabled"])

    # Method: _set_current_game_next_badge_from_image_key - Met à jour l'image du badge affiché dans la section "Premier succès".
    def _set_current_game_next_badge_from_image_key(self, image_key: str) -> None:
        label = self.current_game_image_labels.get("next_badge")
        if label is None:
            return
        data = self.current_game_active_images.get(image_key)
        if not data:
            label.configure(image="", text="Image indisponible")
            return
        try:
            encoded = base64.b64encode(data)
            image = PhotoImage(data=encoded)
        except TclError:
            label.configure(image="", text="Format non supporté")
            return
        max_size = NEXT_ACHIEVEMENT_BADGE_MAX_SIZE
        scale = max((image.width() + max_size - 1) // max_size, (image.height() + max_size - 1) // max_size)
        if scale > 1:
            image = image.subsample(scale, scale)
        self.current_game_image_refs["next_badge"] = image
        label.configure(image=image, text="")

    # Method: _cancel_current_game_clicked_achievement_restore_job - Annule le retour automatique après aperçu temporaire.
    def _cancel_current_game_clicked_achievement_restore_job(self) -> None:
        if self.current_game_clicked_achievement_restore_job is None:
            return
        try:
            self.root.after_cancel(self.current_game_clicked_achievement_restore_job)
        except TclError:
            pass
        self.current_game_clicked_achievement_restore_job = None

    # Method: _clear_current_game_clicked_achievement_selection - Réinitialise la sélection d'un succès cliqué dans la galerie.
    def _clear_current_game_clicked_achievement_selection(self) -> None:
        self._cancel_current_game_clicked_achievement_restore_job()
        self.current_game_clicked_achievement_key = ""
        self.current_game_clicked_achievement_persistent = False

    # Method: _find_current_game_achievement_row_by_image_key - Retourne la ligne de données associée au badge cliqué.
    def _find_current_game_achievement_row_by_image_key(self, image_key: str) -> dict[str, str] | None:
        key = self._safe_text(image_key)
        if not key:
            return None
        for row in self.current_game_achievement_data:
            if not isinstance(row, dict):
                continue
            if self._safe_text(row.get("image_key")) == key:
                return row
        return None

    # Method: _apply_current_game_clicked_achievement_preview - Affiche le succès sélectionné dans le bloc principal.
    def _apply_current_game_clicked_achievement_preview(self, image_key: str) -> bool:
        row = self._find_current_game_achievement_row_by_image_key(image_key)
        if row is None:
            return False
        description = self._safe_text(row.get("next_description"))
        description = self._translate_achievement_description_cached_only(description)
        self._set_current_game_achievement_rows(
            {
                "title": self._safe_text(row.get("next_title")) or ACHIEVEMENT_NA_VALUE,
                "description": description or ACHIEVEMENT_NA_VALUE,
                "points": self._safe_text(row.get("next_points")) or ACHIEVEMENT_NA_VALUE,
                "unlocks": self._safe_text(row.get("next_unlocks")) or ACHIEVEMENT_NA_VALUE,
                "feasibility": self._safe_text(row.get("next_feasibility")) or ACHIEVEMENT_NA_VALUE,
            },
            has_achievements=True,
        )
        self._set_current_game_next_badge_from_image_key(self._safe_text(row.get("image_key")))
        self._refresh_achievement_navigation_buttons_state()
        return True

    # Method: _restore_current_game_main_achievement_after_click_preview - Restaure l'affichage standard après l'aperçu temporaire.
    def _restore_current_game_main_achievement_after_click_preview(self) -> None:
        self.current_game_clicked_achievement_restore_job = None
        if self.current_game_clicked_achievement_persistent:
            return
        self.current_game_clicked_achievement_key = ""
        if self.current_game_locked_achievements:
            self._apply_locked_achievement_index()
            return
        has_achievements = bool(self.current_game_achievement_data)
        self._set_current_game_achievement_rows(None, has_achievements=has_achievements)
        self._set_current_game_images(self.current_game_active_images)
        self._refresh_achievement_navigation_buttons_state()

    # Method: _reapply_current_game_clicked_achievement_if_active - Réapplique le succès cliqué si une sélection est active.
    def _reapply_current_game_clicked_achievement_if_active(self) -> None:
        key = self._safe_text(self.current_game_clicked_achievement_key)
        if not key:
            return
        if self._apply_current_game_clicked_achievement_preview(key):
            return
        self._clear_current_game_clicked_achievement_selection()

    # Method: _on_current_game_achievement_click - Gère la sélection temporaire/persistante d'un succès depuis la galerie.
    def _on_current_game_achievement_click(self, image_key: str) -> None:
        key = self._safe_text(image_key)
        if not key:
            return
        row = self._find_current_game_achievement_row_by_image_key(key)
        if row is None:
            return
        if self.current_game_clicked_achievement_key == key and self.current_game_clicked_achievement_persistent:
            self._apply_current_game_clicked_achievement_preview(key)
            return

        now = time.monotonic()
        second_click_same_achievement = (
            key == self.current_game_last_clicked_achievement_key
            and (now - self.current_game_last_clicked_achievement_click_monotonic) <= ACHIEVEMENT_CLICK_DOUBLE_WINDOW_SECONDS
        )
        self.current_game_last_clicked_achievement_key = key
        self.current_game_last_clicked_achievement_click_monotonic = now

        self.current_game_clicked_achievement_key = key
        self.current_game_clicked_achievement_persistent = second_click_same_achievement
        self._cancel_current_game_clicked_achievement_restore_job()
        if not self._apply_current_game_clicked_achievement_preview(key):
            self._clear_current_game_clicked_achievement_selection()
            return

        if second_click_same_achievement:
            self._debug_log(f"_on_current_game_achievement_click persistent key='{key}'")
            return

        preview_duration_ms = (
            ACHIEVEMENT_CLICK_PREVIEW_UNLOCKED_MS
            if self._safe_bool(row.get("is_unlocked", "0"))
            else ACHIEVEMENT_CLICK_PREVIEW_MS
        )
        self.current_game_clicked_achievement_restore_job = self.root.after(
            preview_duration_ms,
            self._restore_current_game_main_achievement_after_click_preview,
        )
        self._debug_log(f"_on_current_game_achievement_click preview key='{key}' duration_ms={preview_duration_ms}")

    # Method: _apply_locked_achievement_index - Applique l'achievement verrouillé sélectionné dans la section dédiée.
    def _apply_locked_achievement_index(self) -> None:
        if not self.current_game_locked_achievements:
            self._refresh_achievement_navigation_buttons_state()
            return
        size = len(self.current_game_locked_achievements)
        self.current_game_locked_achievement_index = max(0, min(size - 1, self.current_game_locked_achievement_index))
        selected = self.current_game_locked_achievements[self.current_game_locked_achievement_index]
        self._set_current_game_achievement_rows(
            {
                "title": selected.get("title", ACHIEVEMENT_NA_VALUE),
                "description": selected.get("description", ACHIEVEMENT_NA_VALUE),
                "points": selected.get("points", ACHIEVEMENT_NA_VALUE),
                "unlocks": selected.get("unlocks", ACHIEVEMENT_NA_VALUE),
                "feasibility": selected.get("feasibility", ACHIEVEMENT_NA_VALUE),
            },
            has_achievements=True,
        )
        self._set_current_game_next_badge_from_image_key(selected.get("image_key", ""))
        self._refresh_achievement_navigation_buttons_state()

    # Method: _sync_locked_achievement_navigation - Synchronise l'état du bouton avec les succès non débloqués du jeu courant.
    def _sync_locked_achievement_navigation(
        self,
        achievements: list[dict[str, str]],
        preferred_next_achievement: dict[str, str] | None = None,
    ) -> None:
        ordered_achievements = self._order_current_game_achievements(achievements)
        self.current_game_locked_achievements = self._extract_locked_achievements(ordered_achievements)
        self.current_game_locked_achievement_index = 0
        if preferred_next_achievement and self.current_game_locked_achievements:
            preferred_title = self._safe_text(preferred_next_achievement.get("title"))
            preferred_description = self._safe_text(preferred_next_achievement.get("description"))
            for index, item in enumerate(self.current_game_locked_achievements):
                if item.get("title", "") != preferred_title:
                    continue
                if preferred_description and item.get("description", "") != preferred_description:
                    continue
                self.current_game_locked_achievement_index = index
                break
        self._apply_locked_achievement_index()
        self._reapply_current_game_clicked_achievement_if_active()

    # Method: _cycle_current_game_achievement_order_mode - Fait tourner le mode d'ordre des succès et réapplique l'affichage.
    def _cycle_current_game_achievement_order_mode(self) -> None:
        try:
            current_index = ACHIEVEMENT_ORDER_CYCLE.index(self.current_game_achievement_order_mode)
        except ValueError:
            current_index = 0
        next_index = (current_index + 1) % len(ACHIEVEMENT_ORDER_CYCLE)
        self.current_game_achievement_order_mode = ACHIEVEMENT_ORDER_CYCLE[next_index]
        self.current_game_achievement_order_label.set(
            self._achievement_order_label_for_mode(self.current_game_achievement_order_mode)
        )

        if not self.current_game_achievement_data:
            self._refresh_achievement_navigation_buttons_state()
            return

        # Forcer un changement visible immédiat du succès affiché lors du clic sur "Ordre".
        self._clear_current_game_clicked_achievement_selection()

        self._set_current_game_achievement_gallery(
            [dict(item) for item in self.current_game_achievement_data],
            dict(self.current_game_active_images),
        )
        self._sync_locked_achievement_navigation(
            [dict(item) for item in self.current_game_achievement_data],
            None,
        )
        self._debug_log(
            f"_cycle_current_game_achievement_order_mode mode='{self.current_game_achievement_order_mode}'"
        )

    # Method: _show_next_locked_achievement - Affiche le succès non débloqué suivant dans la section dédiée.
    def _show_next_locked_achievement(self) -> None:
        self._clear_current_game_clicked_achievement_selection()
        size = len(self.current_game_locked_achievements)
        if size <= 1:
            self._refresh_achievement_navigation_buttons_state()
            return
        if self.current_game_locked_achievement_index >= (size - 1):
            self._refresh_achievement_navigation_buttons_state()
            return
        self.current_game_locked_achievement_index += 1
        self._apply_locked_achievement_index()

    # Method: _show_previous_locked_achievement - Affiche le succès non débloqué précédent dans la section dédiée.
    def _show_previous_locked_achievement(self) -> None:
        self._clear_current_game_clicked_achievement_selection()
        size = len(self.current_game_locked_achievements)
        if size <= 1:
            self._refresh_achievement_navigation_buttons_state()
            return
        if self.current_game_locked_achievement_index <= 0:
            self._refresh_achievement_navigation_buttons_state()
            return
        self.current_game_locked_achievement_index -= 1
        self._apply_locked_achievement_index()

    # Method: _start_missing_achievement_badges_loader - Lance un chargement différé pour remplacer les tuiles N/A.
    def _start_missing_achievement_badges_loader(self) -> None:
        if self.is_closing or self.current_game_badge_loader_in_progress:
            return

        pending: list[tuple[str, str, str]] = []
        for achievement in self.current_game_achievement_data:
            image_key = self._safe_text(achievement.get("image_key"))
            if not image_key or image_key in self.current_game_active_images:
                continue
            base_url = self._safe_text(achievement.get("badge_url"))
            locked_url = self._safe_text(achievement.get("badge_url_locked"))
            if not base_url and not locked_url:
                continue
            is_unlocked = self._safe_bool(achievement.get("is_unlocked", "0"))
            preferred_url = base_url if is_unlocked else (locked_url or base_url)
            fallback_url = base_url if preferred_url != base_url else ""
            pending.append((image_key, preferred_url, fallback_url))

        if not pending:
            return

        self.current_game_badge_loader_token += 1
        token = self.current_game_badge_loader_token
        self._debug_log(f"_start_missing_achievement_badges_loader token={token} pending={len(pending)}")
        self.current_game_badge_loader_in_progress = True
        worker = threading.Thread(
            target=self._missing_achievement_badges_worker,
            args=(token, pending),
            daemon=True,
        )
        worker.start()

    # Method: _missing_achievement_badges_worker - Télécharge les badges manquants hors thread UI.
    def _missing_achievement_badges_worker(self, token: int, pending: list[tuple[str, str, str]]) -> None:
        loaded: dict[str, bytes] = {}
        for image_key, preferred_url, fallback_url in pending:
            if self.is_closing or token != self.current_game_badge_loader_token:
                break
            raw_data = self._fetch_image_bytes(preferred_url) if preferred_url else None
            if raw_data is None and fallback_url:
                raw_data = self._fetch_image_bytes(fallback_url)
            if raw_data:
                loaded[image_key] = raw_data

        self._queue_ui_callback(
            lambda result=loaded, current_token=token: self._on_missing_achievement_badges_loaded(current_token, result)
        )

    # Method: _on_missing_achievement_badges_loaded - Applique les badges téléchargés et supprime les N/A restants quand possible.
    def _on_missing_achievement_badges_loaded(self, token: int, loaded: dict[str, bytes]) -> None:
        if token != self.current_game_badge_loader_token:
            return
        self.current_game_badge_loader_in_progress = False
        if not loaded:
            self._debug_log(f"_on_missing_achievement_badges_loaded token={token} loaded=0")
            return
        self._debug_log(f"_on_missing_achievement_badges_loaded token={token} loaded={len(loaded)}")

        self.current_game_active_images.update(loaded)
        for image_key, raw_data in loaded.items():
            label = self.current_game_achievement_tile_by_key.get(image_key)
            if label is None or not label.winfo_exists():
                continue
            try:
                encoded = base64.b64encode(raw_data)
                image = PhotoImage(data=encoded)
                scale = max((image.width() + 63) // 64, (image.height() + 63) // 64)
                if scale > 1:
                    image = image.subsample(scale, scale)
                self.current_game_achievement_refs[f"{image_key}:lazy"] = image
                label.configure(image=image, text="")
            except TclError:
                continue

        if self.current_game_locked_achievements:
            size = len(self.current_game_locked_achievements)
            self.current_game_locked_achievement_index = max(0, min(size - 1, self.current_game_locked_achievement_index))
            selected = self.current_game_locked_achievements[self.current_game_locked_achievement_index]
            self._set_current_game_next_badge_from_image_key(selected.get("image_key", ""))
        clicked_key = self._safe_text(self.current_game_clicked_achievement_key)
        if clicked_key:
            self._set_current_game_next_badge_from_image_key(clicked_key)

    # Method: _set_current_game_achievement_gallery - Alimente la galerie des succès avec images + infobulles.
    def _set_current_game_achievement_gallery(self, achievements: list[dict[str, str]], images: dict[str, bytes]) -> None:
        self.current_game_achievement_data = [dict(item) for item in achievements if isinstance(item, dict)]
        ordered_achievements = self._order_current_game_achievements(self.current_game_achievement_data)
        self._clear_current_game_achievement_gallery()
        if self.current_game_achievements_inner is None:
            return
        self.current_game_expected_achievement_tiles_count = len(ordered_achievements)
        if not ordered_achievements:
            self.current_game_locked_achievements = []
            self.current_game_locked_achievement_index = 0
            self._clear_current_game_clicked_achievement_selection()
            self._refresh_achievement_navigation_buttons_state()
            self.current_game_achievements_note.set("Aucun succès disponible pour ce jeu.")
            return

        order_label = self._achievement_order_label_for_mode(self.current_game_achievement_order_mode).replace("Ordre: ", "")
        self.current_game_achievements_note.set(
            f"{len(ordered_achievements)} succès | {order_label} (survolez une image pour voir le nom et la description)."
        )
        for index, achievement in enumerate(ordered_achievements):
            image_key = achievement.get("image_key", "")
            tooltip_text = achievement.get("tooltip", "").strip()
            label = ttk.Label(
                self.current_game_achievements_inner,
                text="N/A",
                style="CurrentGallery.TLabel",
                anchor="center",
                justify="center",
                cursor="hand2",
            )
            raw_data = images.get(image_key)
            if raw_data:
                try:
                    encoded = base64.b64encode(raw_data)
                    image = PhotoImage(data=encoded)
                    scale = max((image.width() + 63) // 64, (image.height() + 63) // 64)
                    if scale > 1:
                        image = image.subsample(scale, scale)
                    self.current_game_achievement_refs[f"{image_key}:{index}"] = image
                    label.configure(image=image, text="")
                except TclError:
                    label.configure(image="", text="N/A")

            label.bind("<Enter>", lambda _event, text=tooltip_text, idx=index: self._on_current_game_achievement_enter(text, idx))
            label.bind("<Motion>", lambda _event, text=tooltip_text, idx=index: self._on_current_game_achievement_motion(text, idx))
            label.bind("<Leave>", lambda _event: self._on_current_game_achievement_leave())
            label.bind("<Button-1>", lambda _event, key=image_key: self._on_current_game_achievement_click(key))
            self._track_rounded_image_widget(label, ACHIEVEMENT_IMAGE_CORNER_RADIUS)
            self.current_game_achievement_tiles.append(label)
            if image_key:
                self.current_game_achievement_tile_by_key[image_key] = label

        self._layout_current_game_achievement_gallery()
        if self.current_game_achievements_canvas is not None:
            self.current_game_achievements_canvas.update_idletasks()
            self.current_game_achievements_canvas.configure(scrollregion=self.current_game_achievements_canvas.bbox("all"))
            self.current_game_achievements_canvas.yview_moveto(0.0)
        self.current_game_achievement_scroll_direction = 1
        if self._should_auto_scroll_current_game_achievements():
            self._restart_current_game_achievement_auto_scroll(immediate=True)
        else:
            self._stop_current_game_achievement_auto_scroll()
        if self.current_game_achievements_inner is not None:
            self.root.after_idle(lambda: self._apply_rounded_corners_to_widget_tree(self.current_game_achievements_inner))
        self._start_missing_achievement_badges_loader()

    # Method: _fetch_image_bytes - Télécharge une image distante avec cache mémoire.
    def _fetch_image_bytes(self, url: str) -> bytes | None:
        normalized = url.strip()
        if not normalized:
            return None
        cached = self._image_bytes_cache.get(normalized)
        if cached is not None:
            return cached
        response = None
        for attempt in range(2):
            try:
                response = self._http_session.get(normalized, timeout=IMAGE_FETCH_TIMEOUT_SECONDS)
                if response.status_code == 200 and response.content:
                    break
            except requests.RequestException:
                response = None
            if attempt == 0:
                time.sleep(0.12)
        if response is None or response.status_code != 200 or not response.content:
            return None

        self._image_bytes_cache[normalized] = response.content
        return response.content

    # Method: _set_current_game_images - Met à jour la valeur ou l'état associé.
    def _set_current_game_images(self, images: dict[str, bytes]) -> None:
        self.current_game_active_images = dict(images)
        self.current_game_image_refs = {}
        for key, label in self.current_game_image_labels.items():
            data = images.get(key)
            if not data:
                label.configure(image="", text="Image indisponible")
                continue
            try:
                encoded = base64.b64encode(data)
                image = PhotoImage(data=encoded)
            except TclError:
                label.configure(image="", text="Format non supporté")
                continue
            if key == "boxart":
                max_width, max_height = (320, 180)
            elif key == "next_badge":
                max_width, max_height = (NEXT_ACHIEVEMENT_BADGE_MAX_SIZE, NEXT_ACHIEVEMENT_BADGE_MAX_SIZE)
            else:
                max_width, max_height = (108, 108)
            scale = max((image.width() + max_width - 1) // max_width, (image.height() + max_height - 1) // max_height)
            if scale > 1:
                image = image.subsample(scale, scale)
            self.current_game_image_refs[key] = image
            label.configure(image=image, text="")

    # Method: _pick_current_game - Sélectionne l'élément le plus pertinent.
    def _pick_current_game(self, dashboard: dict[str, object], prefer_last_played: bool = False) -> tuple[int, str]:
        if prefer_last_played:
            latest = dashboard.get("latest")
            if isinstance(latest, dict):
                game_id = self._safe_int(latest.get("last_played_game_id"))
                if game_id > 0:
                    title = self._safe_text(latest.get("last_played_game_title"))
                    return game_id, title

        recent = dashboard.get("recent_achievements", [])
        if isinstance(recent, list) and not prefer_last_played:
            for entry in recent:
                if not isinstance(entry, dict):
                    continue
                game_id = self._safe_int(entry.get("game_id"))
                if game_id > 0:
                    return game_id, str(entry.get("game_title", "")).strip()

        games = dashboard.get("games", [])
        if not isinstance(games, list) or not games:
            return 0, ""

        # Method: sort_key - Calcule la clé de tri locale.
        def sort_key(item: dict[str, object]) -> tuple[float, str]:
            date_text = str(item.get("most_recent_awarded_date", "")).strip()
            parsed = self._parse_sort_datetime(date_text)
            ts = parsed.timestamp() if parsed is not None else 0.0
            return ts, str(item.get("title", "")).casefold()

        best = max((item for item in games if isinstance(item, dict)), key=sort_key, default=None)
        if not isinstance(best, dict):
            return 0, ""
        return self._safe_int(best.get("game_id")), str(best.get("title", "")).strip()

    # Method: _is_recent_activity_timestamp - Indique si un horodatage est suffisamment récent pour un signal Live fiable.
    def _is_recent_activity_timestamp(self, value: object, max_age_seconds: int = LIVE_RICH_PRESENCE_MAX_AGE_SECONDS) -> bool:
        text = self._safe_text(value)
        if not text:
            return False
        parsed = self._parse_sort_datetime(text)
        if parsed is None:
            return False
        max_age = float(max_age_seconds)
        if parsed.tzinfo is not None:
            try:
                parsed = parsed.astimezone().replace(tzinfo=None)
            except ValueError:
                parsed = parsed.replace(tzinfo=None)
            age_seconds = (datetime.now() - parsed).total_seconds()
            return -90.0 <= age_seconds <= max_age

        # Certains horodatages API sont naïfs mais exprimés en UTC.
        # On accepte la valeur si elle est récente en interprétation locale OU UTC.
        for now_ref in (datetime.now(), datetime.utcnow()):
            age_seconds = (now_ref - parsed).total_seconds()
            if -90.0 <= age_seconds <= max_age:
                return True
        return False

    # Method: _is_rich_presence_game_loaded - Indique si le texte Rich Presence correspond a un jeu reellement charge.
    def _is_rich_presence_game_loaded(self, value: object) -> bool:
        text = self._safe_text(value)
        if not text:
            return False
        lowered = text.casefold()
        negative_markers = (
            "main menu",
            "menu principal",
            "no game",
            "game not loaded",
            "not loaded",
            "no content",
            "nothing loaded",
            "aucun jeu",
            "pas de jeu",
            "rom not loaded",
        )
        return not any(marker in lowered for marker in negative_markers)

    # Method: _extract_live_current_game - Extrait le jeu en cours depuis le résumé API.
    def _extract_live_current_game(
        self,
        summary: dict[str, object],
        emulator_live: bool = False,
    ) -> tuple[int, str, str, bool, str]:
        rich_presence = ""
        rich_presence_raw = ""
        for field in ("RichPresenceMsg", "RichPresence", "RichPresenceMessage"):
            value = self._safe_text(summary.get(field))
            if not value:
                continue
            rich_presence_raw = value
            if self._is_rich_presence_game_loaded(value):
                rich_presence = value
            break

        rich_presence_date = self._safe_text(
            summary.get("RichPresenceMsgDate")
            or summary.get("RichPresenceDate")
            or summary.get("RichPresenceUpdatedAt")
        )
        rich_presence_recent = bool(rich_presence) and self._is_recent_activity_timestamp(rich_presence_date)
        strict_fallback_presence_recent = bool(rich_presence) and self._is_recent_activity_timestamp(
            rich_presence_date,
            max_age_seconds=LIVE_RECENT_PLAYED_FALLBACK_MAX_AGE_SECONDS,
        )
        if rich_presence and not rich_presence_recent:
            rich_presence = ""
        rich_presence_explicit_no_game = bool(rich_presence_raw) and not bool(rich_presence)
        rich_presence_preview = " ".join(rich_presence_raw.split())
        if len(rich_presence_preview) > 110:
            rich_presence_preview = f"{rich_presence_preview[:107]}..."
        recent_activity_window_seconds = (
            LIVE_RECENT_PLAYED_MAX_AGE_SECONDS
            if rich_presence
            else LIVE_RECENT_PLAYED_FALLBACK_MAX_AGE_SECONDS
        )

        recent = summary.get("RecentlyPlayed")
        recent_activity_detected = False
        best_recent_game_id = 0
        best_recent_title = ""
        best_recent_timestamp = -1.0
        strict_best_recent_game_id = 0
        strict_best_recent_title = ""
        strict_best_recent_timestamp = -1.0
        fallback_recent_game_id = 0
        fallback_recent_title = ""
        if isinstance(recent, list):
            for item in recent:
                if not isinstance(item, dict):
                    continue
                game_id = self._safe_int(item.get("GameID") or item.get("ID"))
                title = self._extract_title_text(item.get("Title") or item.get("GameTitle") or item)
                if game_id > 0 and fallback_recent_game_id <= 0:
                    fallback_recent_game_id = game_id
                    fallback_recent_title = title

                candidate_date = (
                    self._safe_text(item.get("LastPlayed"))
                    or self._safe_text(item.get("DateModified"))
                    or self._safe_text(item.get("Date"))
                    or self._safe_text(item.get("MostRecentAwardedDate"))
                )
                parsed_candidate = self._parse_sort_datetime(candidate_date)
                candidate_timestamp = parsed_candidate.timestamp() if parsed_candidate is not None else -1.0
                is_recent_item = self._is_recent_activity_timestamp(
                    candidate_date,
                    max_age_seconds=recent_activity_window_seconds,
                )
                if is_recent_item:
                    recent_activity_detected = True
                    if game_id > 0 and candidate_timestamp > best_recent_timestamp:
                        best_recent_timestamp = candidate_timestamp
                        best_recent_game_id = game_id
                        best_recent_title = title
                is_strict_recent_item = self._is_recent_activity_timestamp(
                    candidate_date,
                    max_age_seconds=LIVE_RECENT_PLAYED_FALLBACK_MAX_AGE_SECONDS,
                )
                if is_strict_recent_item and game_id > 0 and candidate_timestamp > strict_best_recent_timestamp:
                    strict_best_recent_timestamp = candidate_timestamp
                    strict_best_recent_game_id = game_id
                    strict_best_recent_title = title

        online = self._safe_bool(summary.get("IsOnline")) or self._safe_bool(summary.get("IsOnine"))
        def emit_result(
            game_id: int,
            title: str,
            presence: str,
            is_online_value: bool,
            decision: str,
        ) -> tuple[int, str, str, bool, str]:
            self._probe(
                "extract_live_current_game",
                decision=decision,
                game_id=game_id,
                rich_presence=bool(presence),
                rich_presence_recent=rich_presence_recent,
                strict_fallback_presence_recent=strict_fallback_presence_recent,
                recent_activity=recent_activity_detected,
                best_recent_game_id=best_recent_game_id,
                strict_best_recent_game_id=strict_best_recent_game_id,
                online=is_online_value,
                rich_presence_preview=rich_presence_preview or "-",
                rich_presence_date=rich_presence_date or "-",
            )
            return game_id, title, presence, is_online_value, decision
        if rich_presence_explicit_no_game:
            return emit_result(0, "", "", online, "explicit_no_game")
        if not rich_presence and not recent_activity_detected:
            return emit_result(0, "", rich_presence, online, "no_recent_activity")

        # En mode "live", on privilégie d'abord les champs directs GameID/MostRecentGameID.
        # Cela évite de choisir un mauvais jeu via RecentlyPlayed quand le Rich Presence est valide.
        direct_pairs = (
            ("GameID", "GameTitle"),
            ("MostRecentGameID", "MostRecentGameTitle"),
        )
        if rich_presence:
            for game_id_field, title_field in direct_pairs:
                game_id = self._safe_int(summary.get(game_id_field))
                if game_id > 0:
                    return emit_result(
                        game_id,
                        self._extract_title_text(summary.get(title_field)),
                        rich_presence,
                        online,
                        f"direct_pair:{game_id_field}",
                    )
            if strict_fallback_presence_recent and strict_best_recent_game_id > 0:
                return emit_result(
                    strict_best_recent_game_id,
                    strict_best_recent_title,
                    rich_presence,
                    online,
                    "recent_fallback",
                )
            if best_recent_game_id > 0:
                return emit_result(
                    0,
                    "",
                    rich_presence,
                    online,
                    "recent_fallback_rejected_relaxed_blocked",
                )

        # Sans Rich Presence exploitable, on peut encore confirmer un jeu chargé
        # quand l'émulateur est vivant ET qu'une activité très récente est visible.
        if emulator_live and recent_activity_detected:
            for game_id_field, title_field in direct_pairs:
                game_id = self._safe_int(summary.get(game_id_field))
                if game_id > 0:
                    return emit_result(
                        game_id,
                        self._extract_title_text(summary.get(title_field)),
                        rich_presence,
                        online,
                        f"direct_pair_no_presence:{game_id_field}",
                    )
            if strict_best_recent_game_id > 0:
                return emit_result(
                    strict_best_recent_game_id,
                    strict_best_recent_title,
                    rich_presence,
                    online,
                    "recent_strict_no_presence",
                )
            if best_recent_game_id > 0:
                return emit_result(0, "", rich_presence, online, "recent_no_presence_rejected_relaxed_blocked")

        return emit_result(0, "", rich_presence, online, "no_live_game")

    # Method: _extract_last_played_game - Extrait le dernier jeu joué depuis le résumé API.
    def _extract_last_played_game(self, summary: dict[str, object]) -> tuple[int, str]:
        recent = summary.get("RecentlyPlayed")
        if isinstance(recent, list):
            best_game_id = 0
            best_title = ""
            best_timestamp = -1.0
            fallback_game_id = 0
            fallback_title = ""
            for item in recent:
                if not isinstance(item, dict):
                    continue
                game_id = self._safe_int(item.get("GameID") or item.get("ID"))
                if game_id <= 0:
                    continue
                title = self._extract_title_text(item.get("Title") or item.get("GameTitle") or item)
                if fallback_game_id <= 0:
                    fallback_game_id = game_id
                    fallback_title = title
                date_text = (
                    self._safe_text(item.get("LastPlayed"))
                    or self._safe_text(item.get("DateModified"))
                    or self._safe_text(item.get("Date"))
                    or self._safe_text(item.get("MostRecentAwardedDate"))
                )
                parsed = self._parse_sort_datetime(date_text)
                timestamp = parsed.timestamp() if parsed is not None else -1.0
                if timestamp > best_timestamp:
                    best_timestamp = timestamp
                    best_game_id = game_id
                    best_title = title
            if best_game_id > 0:
                return best_game_id, best_title
            if fallback_game_id > 0:
                return fallback_game_id, fallback_title

        direct_pairs = (
            ("MostRecentGameID", "MostRecentGameTitle"),
            ("LastGameID", "LastGame"),
            ("GameID", "GameTitle"),
        )
        for game_id_field, title_field in direct_pairs:
            game_id = self._safe_int(summary.get(game_id_field))
            if game_id > 0:
                return game_id, self._extract_title_text(summary.get(title_field))

        return 0, ""

    # Method: _find_recently_played_game_entry - Retrouve l'entree RecentlyPlayed correspondant au jeu cible.
    def _find_recently_played_game_entry(self, summary: dict[str, object], game_id: int) -> dict[str, object]:
        if game_id <= 0:
            return {}
        recent = summary.get("RecentlyPlayed")
        if not isinstance(recent, list):
            return {}
        for item in recent:
            if not isinstance(item, dict):
                continue
            item_game_id = self._safe_int(item.get("GameID") or item.get("ID"))
            if item_game_id == game_id:
                return item
        return {}

    # Method: _parse_completion_percent - Convertit une valeur de completion (texte libre) vers un pourcentage.
    def _parse_completion_percent(self, value: object) -> float | None:
        text = self._safe_text(value).replace(",", ".")
        if not text:
            return None

        if "/" in text:
            numbers = re.findall(r"\d+", text)
            if len(numbers) >= 2:
                done = self._safe_int(numbers[0])
                total = self._safe_int(numbers[1])
                if total > 0:
                    return round((done / total) * 100.0, 1)

        percent_match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", text)
        if percent_match:
            try:
                parsed = float(percent_match.group(1))
            except ValueError:
                parsed = None
            if parsed is not None:
                return max(0.0, min(100.0, parsed))

        numeric = self._safe_float(text)
        if numeric is None:
            return None
        if numeric < 0 or numeric > 100:
            return None
        return numeric

    # Method: _select_latest_date_text - Sélectionne la date la plus récente parmi une liste de valeurs candidates.
    def _select_latest_date_text(self, candidates: list[object]) -> str:
        best_raw = ""
        best_timestamp = -1.0
        fallback_raw = ""
        for candidate in candidates:
            raw = self._safe_text(candidate)
            if not raw:
                continue
            if not fallback_raw:
                fallback_raw = raw
            parsed = self._parse_sort_datetime(raw)
            if parsed is None:
                continue
            timestamp = parsed.timestamp()
            if timestamp > best_timestamp:
                best_timestamp = timestamp
                best_raw = raw
        return best_raw or fallback_raw

    # Method: _extract_latest_unlock_date_from_payload - Calcule le dernier succès débloqué depuis le détail du jeu.
    def _extract_latest_unlock_date_from_payload(self, payload: dict[str, object]) -> str:
        candidates: list[object] = [
            payload.get("MostRecentAwardedDate"),
            payload.get("LastAwardedDate"),
            payload.get("LastAchievementDate"),
            payload.get("DateModified"),
        ]
        unlocked_date_keys = (
            "DateEarnedHardcore",
            "DateEarned",
            "DateEarnedAt",
            "DateEarnedHardcoreAt",
            "DateUnlocked",
        )
        for achievement in self._extract_game_achievements(payload):
            if not self._is_achievement_unlocked(achievement):
                continue
            for key in unlocked_date_keys:
                candidates.append(achievement.get(key))
        return self._select_latest_date_text(candidates)

    # Method: _build_current_game_local_rows - Construit les lignes du résumé local du jeu courant.
    def _build_current_game_local_rows(
        self,
        game_id: int,
        title_hint: str,
        games_lookup: dict[int, dict[str, object]],
        source: str = "",
        rich_presence: str = "",
        summary_payload: dict[str, object] | None = None,
        game_payload: dict[str, object] | None = None,
    ) -> tuple[str, str, str, str, str, list[tuple[str, str]]]:
        game_row = games_lookup.get(game_id) if isinstance(games_lookup.get(game_id), dict) else {}
        summary = summary_payload if isinstance(summary_payload, dict) else {}
        payload = game_payload if isinstance(game_payload, dict) else {}
        recent_entry = self._find_recently_played_game_entry(summary, game_id)

        title = (
            title_hint
            or self._safe_text(game_row.get("title"))
            or self._extract_title_text(payload.get("GameTitle") or payload.get("Title"))
            or self._extract_title_text(recent_entry.get("Title") or recent_entry.get("GameTitle") or recent_entry)
        )

        console = ""
        for raw_console in (
            payload.get("ConsoleName"),
            payload.get("Console"),
            payload.get("SystemName"),
            recent_entry.get("ConsoleName"),
            recent_entry.get("Console"),
            game_row.get("console_name"),
        ):
            candidate = self._safe_text(raw_console)
            if candidate:
                console = candidate
                break

        local_hardcore = self._safe_int(game_row.get("num_awarded_hardcore"))
        local_softcore = self._safe_int(game_row.get("num_awarded"))
        local_max_possible = self._safe_int(game_row.get("max_possible"))
        payload_hardcore: int | None = None
        payload_softcore: int | None = None
        for key in ("NumAwardedToUserHardcore", "NumAwardedHardcore"):
            if key in payload:
                payload_hardcore = self._safe_int(payload.get(key))
                break
        for key in ("NumAwardedToUser", "NumAwarded"):
            if key in payload:
                payload_softcore = self._safe_int(payload.get(key))
                break
        recent_hardcore = self._safe_int(
            recent_entry.get("NumAwardedToUserHardcore") or recent_entry.get("AchievementsUnlockedHardcore")
        )
        recent_softcore = self._safe_int(
            recent_entry.get("NumAwardedToUser") or recent_entry.get("AchievementsUnlocked")
        )

        hardcore_value = max(
            0,
            payload_hardcore
            if payload_hardcore is not None
            else (recent_hardcore if recent_hardcore > 0 else local_hardcore),
        )
        softcore_value = max(
            0,
            payload_softcore
            if payload_softcore is not None
            else (recent_softcore if recent_softcore > 0 else local_softcore),
        )
        awarded_value = hardcore_value if hardcore_value > 0 else softcore_value

        payload_max_possible = max(
            self._safe_int(payload.get("NumAchievements")),
            self._safe_int(payload.get("MaxPossible")),
            self._safe_int(recent_entry.get("AchievementsTotal")),
        )
        max_possible_value = max(0, payload_max_possible if payload_max_possible > 0 else local_max_possible)

        completion_pct: float | None = None
        for raw_pct in (
            payload.get("UserCompletionHardcore"),
            payload.get("UserCompletion"),
            recent_entry.get("Completion"),
            recent_entry.get("CompletionPct"),
            game_row.get("completion_pct"),
        ):
            completion_pct = self._parse_completion_percent(raw_pct)
            if completion_pct is not None:
                break
        if completion_pct is None and max_possible_value > 0:
            completion_pct = round((awarded_value / max_possible_value) * 100.0, 1)

        if max_possible_value > 0:
            if completion_pct is None:
                completion_pct = round((awarded_value / max_possible_value) * 100.0, 1)
            pct_text = f"{max(0.0, min(100.0, completion_pct)):.1f}".rstrip("0").rstrip(".")
            progress_value = f"{awarded_value}/{max_possible_value} ({pct_text}%)"
        elif completion_pct is not None:
            pct_text = f"{max(0.0, min(100.0, completion_pct)):.1f}".rstrip("0").rstrip(".")
            progress_value = f"{awarded_value}/- ({pct_text}%)"
        else:
            progress_value = "-"

        raw_last_unlock = self._select_latest_date_text(
            [
                game_row.get("most_recent_awarded_date"),
                recent_entry.get("MostRecentAwardedDate"),
                recent_entry.get("LastAwardedDate"),
                recent_entry.get("DateModified"),
                payload.get("MostRecentAwardedDate"),
                payload.get("LastAwardedDate"),
                payload.get("LastAchievementDate"),
                payload.get("DateModified"),
                self._extract_latest_unlock_date_from_payload(payload) if payload else "",
            ]
        )
        last_unlock = self._format_datetime_display(raw_last_unlock)
        title_value = title or (f"Jeu #{game_id}" if game_id > 0 else "-")
        console_value = console or "-"
        last_unlock_value = last_unlock or "-"
        source_value = source.strip() or "Inconnu"

        rows = [
            ("Game ID", str(game_id) if game_id > 0 else "-"),
            ("Jeu", title_value),
            ("Console", console_value),
            ("Progression hardcore", progress_value),
            ("Dernier succès", last_unlock_value),
            ("Source", source_value),
        ]
        if rich_presence:
            rows.append(("Rich Presence", rich_presence))
        return title_value, console_value, progress_value, last_unlock_value, source_value, rows

    # Method: _rebuild_current_game_info_rows_from_ui_state - Recalcule la section infos pour éviter les reliquats entre modes.
    def _rebuild_current_game_info_rows_from_ui_state(self) -> None:
        game_id = 0
        if self._current_game_last_key is not None:
            try:
                game_id = int(self._current_game_last_key[1])
            except (TypeError, ValueError):
                game_id = 0
        rows = [
            ("Game ID", str(game_id) if game_id > 0 else "-"),
            ("Jeu", self.current_game_title.get().strip() or "-"),
            ("Console", self.current_game_console.get().strip() or "-"),
            ("Progression hardcore", self.current_game_progress.get().strip() or "-"),
            ("Dernier succès", self.current_game_last_unlock.get().strip() or "-"),
            ("Source", self.current_game_source.get().strip() or "Inconnu"),
        ]
        self._set_current_game_info_rows(rows)

    # Method: _update_current_game_tab - Réalise le traitement lié à update current game tab.
    def _update_current_game_tab(self, dashboard: dict[str, object], username: str, force_refresh: bool = False) -> None:
        self._show_current_game_loading_overlay("Chargement des infos du jeu en cours...")
        self._debug_log(
            f"_update_current_game_tab user='{username}' force_refresh={force_refresh} "
            f"emulator='{self.emulator_status_text.get().strip()}'"
        )
        if force_refresh:
            self.prefer_persisted_current_game_on_startup = False
        retained_game_id = 0
        retained_key = self._current_game_last_key
        if retained_key is not None and retained_key[0] == username and retained_key[1] > 0:
            retained_game_id = int(retained_key[1])
        games_lookup: dict[int, dict[str, object]] = {}
        games = dashboard.get("games", [])
        if isinstance(games, list):
            for item in games:
                if not isinstance(item, dict):
                    continue
                game_id = self._safe_int(item.get("game_id"))
                if game_id > 0:
                    games_lookup[game_id] = item

        emulator_live = self._is_emulator_process_live()
        inactive_mode = self.emulator_status_text.get().strip().casefold() == EMULATOR_STATUS_INACTIVE.casefold()
        prefer_last_played_mode = (not emulator_live) or inactive_mode
        if emulator_live:
            self.prefer_persisted_current_game_on_startup = False
        fallback_game_id, fallback_title = self._pick_current_game(
            dashboard,
            prefer_last_played=prefer_last_played_mode,
        )
        fallback_key = (username, fallback_game_id)
        same_fallback = (not force_refresh) and fallback_game_id > 0 and self._current_game_last_key == fallback_key
        current_key = self._current_game_last_key
        startup_cache_preferred = (
            prefer_last_played_mode
            and self.prefer_persisted_current_game_on_startup
            and retained_game_id > 0
            and not force_refresh
        )
        allow_fallback_preview = (
            prefer_last_played_mode
            or current_key is None
            or current_key[0] != username
            or current_key[1] <= 0
            or current_key == fallback_key
        )
        if startup_cache_preferred:
            allow_fallback_preview = False
        self._debug_log(
            f"_update_current_game_tab fallback_game_id={fallback_game_id} "
            f"same_fallback={same_fallback} current_key={self._current_game_last_key} "
            f"startup_cache_preferred={startup_cache_preferred}"
        )
        if fallback_game_id > 0:
            title_value, console_value, progress_value, last_unlock_value, source_value, fallback_rows = self._build_current_game_local_rows(
                fallback_game_id,
                fallback_title,
                games_lookup,
                source="Dernier jeu joué (local)" if prefer_last_played_mode else "Secours local",
            )
            if (not same_fallback) and allow_fallback_preview:
                self.current_game_title.set(title_value)
                self.current_game_console.set(console_value)
                self.current_game_progress.set(progress_value)
                self.current_game_last_unlock.set(last_unlock_value)
                self._set_current_game_source(source_value)
                self._set_current_game_info_rows(fallback_rows)
                fallback_cache_applied = self._apply_current_game_cached_details(fallback_key)
                if not fallback_cache_applied and retained_game_id <= 0:
                    self._set_current_game_achievement_rows(None, has_achievements=False)
                    self._set_current_game_achievement_gallery([], {})
                    self._set_current_game_images({})
                elif not fallback_cache_applied:
                    self._debug_log(
                        "_update_current_game_tab conserve l'affichage actuel des succès "
                        "en attendant les données du fallback."
                    )
            elif emulator_live and (not allow_fallback_preview):
                self._debug_log(
                    "_update_current_game_tab fallback local ignoré en mode Live "
                    "pour conserver l'affichage du jeu détecté."
                )
        else:
            if self._current_game_last_key is None:
                self._clear_current_game_details("Détection du jeu en cours...")

        api_key = self.api_key.get().strip()
        if not api_key:
            self._debug_log("_update_current_game_tab arrêt: clé API manquante.")
            self.current_game_fetch_in_progress = False
            self.status_text.set(self._connection_diagnostic())
            if fallback_game_id > 0:
                if emulator_live and not inactive_mode:
                    self.current_game_note.set("Jeu estimé localement (clé API manquante pour la détection en direct).")
                else:
                    self.current_game_note.set("Émulateur inactif: dernier jeu joué affiché depuis les données locales.")
            else:
                self.current_game_note.set("Clé API manquante pour détecter le jeu en direct ou le dernier jeu joué.")
            self._finalize_current_game_loading_overlay_after_gallery()
            return

        if self.current_game_fetch_in_progress:
            if force_refresh:
                self._pending_emulator_status_force_refresh = True
            self._debug_log(
                "_update_current_game_tab ignoré: récupération déjà en cours "
                f"(force_refresh={force_refresh})."
            )
            return

        if self._current_game_last_key is None or not same_fallback:
            self.current_game_note.set("Détection du jeu en cours via RetroAchievements...")
        self._current_game_fetch_token += 1
        fetch_token = self._current_game_fetch_token
        self.current_game_fetch_in_progress = True
        self._arm_current_game_loading_timeout(fetch_token)
        self._debug_log(f"_update_current_game_tab lancement worker fetch_token={fetch_token}")
        worker = threading.Thread(
            target=self._fetch_current_game_worker,
            args=(
                api_key,
                username,
                fallback_game_id,
                fallback_title,
                games_lookup,
                emulator_live,
                inactive_mode,
                fetch_token,
                force_refresh,
                retained_game_id,
            ),
            daemon=True,
        )
        worker.start()

    # Method: _fetch_current_game_worker - Réalise le traitement lié à fetch current game worker.
    def _fetch_current_game_worker(
        self,
        api_key: str,
        username: str,
        fallback_game_id: int,
        fallback_title: str,
        games_lookup: dict[int, dict[str, object]],
        emulator_live: bool,
        inactive_mode: bool,
        fetch_token: int,
        force_refresh: bool = False,
        retained_game_id: int = 0,
    ) -> None:
        prefer_last_played_mode = (not emulator_live) or inactive_mode
        self._debug_log(
            f"_fetch_current_game_worker start token={fetch_token} user='{username}' "
            f"fallback_game_id={fallback_game_id} emulator_live={emulator_live} inactive_mode={inactive_mode} "
            f"force_refresh={force_refresh} "
            f"retained_game_id={retained_game_id}"
        )
        game_id = fallback_game_id
        title_hint = fallback_title
        source_label = "Dernier jeu joué (local)" if prefer_last_played_mode else "Secours local"
        rich_presence = ""
        summary_payload: dict[str, object] = {}
        game_payload: dict[str, object] = {}
        images: dict[str, bytes] = {}
        next_achievement: dict[str, str] | None = None
        achievement_rows: list[dict[str, str]] = []
        error: str | None = None
        diagnostic_error: str | None = None
        startup_cache_preferred = False

        client = RetroAchievementsClient(api_key, timeout_seconds=8)
        try:
            summary = client.get_user_summary(username, include_recent_games=True)
            summary_payload = summary
            live_game_id, live_title, rich_presence, is_online, live_decision = self._extract_live_current_game(
                summary,
                emulator_live=emulator_live,
            )
            last_played_id, last_played_title = self._extract_last_played_game(summary)
            self._debug_log(
                f"_fetch_current_game_worker summary live_game_id={live_game_id} last_played_id={last_played_id} "
                f"live_decision={live_decision} is_online={is_online} rich_presence={'yes' if bool(rich_presence) else 'no'}"
            )

            startup_cache_preferred = (
                prefer_last_played_mode
                and self.prefer_persisted_current_game_on_startup
                and retained_game_id > 0
                and not force_refresh
            )

            if startup_cache_preferred:
                game_id = retained_game_id
                source_label = "Cache sauvegarde"
            elif inactive_mode and last_played_id > 0:
                game_id = last_played_id
                if last_played_title:
                    title_hint = last_played_title
                source_label = "Dernier jeu joué"
            elif live_game_id > 0:
                game_id = live_game_id
                if live_title:
                    title_hint = live_title
                source_label = "Direct émulateur" if emulator_live else "Direct RA"
            elif emulator_live and last_played_id > 0:
                # Fallback prudent: on affiche un jeu plausible, sans le considérer comme "jeu live confirmé".
                game_id = last_played_id
                if last_played_title:
                    title_hint = last_played_title
                source_label = "Dernier jeu joué (direct indisponible)"
            elif emulator_live:
                game_id = 0
                title_hint = ""
                rich_presence = ""
                source_label = EMULATOR_STATUS_EMULATOR_LOADED
            elif last_played_id > 0:
                game_id = last_played_id
                if last_played_title:
                    title_hint = last_played_title
                source_label = "Dernier jeu joué"
            elif fallback_game_id <= 0:
                source_label = "Inconnu"
        except (RetroAPIError, OSError, ValueError) as exc:
            diagnostic_error = self._format_diagnostic_error(exc)
            self._debug_log(f"_fetch_current_game_worker erreur summary: {diagnostic_error}")
            if fallback_game_id > 0:
                source_label = "Dernier jeu joué (local)" if prefer_last_played_mode else "Secours local"
            else:
                source_label = "Inconnu"

        detected_key = (username, game_id if game_id > 0 else 0)
        if startup_cache_preferred and retained_game_id > 0:
            self._debug_log(
                f"_fetch_current_game_worker conserve cache démarrage token={fetch_token} retained_game_id={retained_game_id}"
            )
            self._queue_ui_callback(
                lambda diag=diagnostic_error: self._on_current_game_unchanged(
                    fetch_token=fetch_token,
                    note="Données restaurées depuis la sauvegarde locale.",
                    source_value="Cache sauvegarde",
                    diagnostic_error=diag,
                )
            )
            return

        if game_id <= 0 and retained_game_id > 0 and prefer_last_played_mode:
            keep_note = "Émulateur inactif: affichage du dernier jeu joué."
            self._debug_log(
                f"_fetch_current_game_worker conservation dernier jeu affiché token={fetch_token} "
                f"retained_game_id={retained_game_id} emulator_live={emulator_live} inactive_mode={inactive_mode}"
            )
            self._queue_ui_callback(
                lambda note=keep_note, diag=diagnostic_error: self._on_current_game_unchanged(
                    fetch_token=fetch_token,
                    note=note,
                    source_value="Dernier jeu joué (local)",
                    diagnostic_error=diag,
                )
            )
            return

        self._probe(
            "current_game_worker_select",
            token=fetch_token,
            emulator_live=emulator_live,
            live_game_detected=source_label,
            selected_game_id=game_id,
            retained_game_id=retained_game_id,
            fallback_game_id=fallback_game_id,
        )

        if game_id <= 0:
            if emulator_live and not inactive_mode:
                source_label = source_label or EMULATOR_STATUS_EMULATOR_LOADED
                note_text = "Émulateur chargé: en attente d'un jeu chargé."
            else:
                note_text = "Aucun jeu en cours ou dernier jeu joué détecté."
            self._debug_log(f"_fetch_current_game_worker aucun jeu détecté token={fetch_token} source='{source_label}'")
            self._probe(
                "current_game_worker_no_game",
                token=fetch_token,
                source=source_label,
                emulator_live=emulator_live,
            )
            _, _, _, _, source_value, _ = self._build_current_game_local_rows(
                0,
                "",
                games_lookup,
                source=source_label,
                rich_presence=rich_presence,
            )
            self._queue_ui_callback(
                lambda source_value=source_value: self._on_current_game_loaded(
                    fetch_token=fetch_token,
                    key=(username, 0),
                    title_value="-",
                    console_value="-",
                    progress_value="-",
                    last_unlock_value="-",
                    source_value=source_value,
                    next_achievement=None,
                    achievement_rows=[],
                    images={},
                    error=None,
                    diagnostic_error=diagnostic_error,
                    note=note_text,
                )
            )
            return

        if (
            (not force_refresh)
            and retained_game_id > 0
            and detected_key[0] == username
            and detected_key[1] == retained_game_id
        ):
            title_value, console_value, progress_value, last_unlock_value, source_value, _ = self._build_current_game_local_rows(
                game_id,
                title_hint,
                games_lookup,
                source=source_label,
                rich_presence=rich_presence,
                summary_payload=summary_payload,
                game_payload=game_payload,
            )
            if self._is_live_source_label(source_label):
                unchanged_note = "Jeu direct confirmé (optimisation sans rechargement complet)."
            elif source_label.startswith("Dernier jeu joué"):
                unchanged_note = "Jeu inchangé (fallback conservé)."
            else:
                unchanged_note = "Jeu inchangé."
            self._debug_log(
                f"_fetch_current_game_worker optimisation jeu inchangé token={fetch_token} "
                f"game_id={game_id} source='{source_label}'"
            )
            self._queue_ui_callback(
                lambda note=unchanged_note, source_value=source_value, diag=diagnostic_error: self._on_current_game_unchanged(
                    fetch_token=fetch_token,
                    note=note,
                    source_value=source_value,
                    diagnostic_error=diag,
                )
            )
            return

        try:
            payload = client.get_game_info_and_user_progress(username, game_id)
            game_payload = payload
            total_players = self._safe_int(payload.get("NumDistinctPlayers"))
            boxart_url = self._normalize_media_url(str(payload.get("ImageBoxArt", "")))
            boxart_bytes = self._fetch_image_bytes(boxart_url)
            if boxart_bytes:
                images["boxart"] = boxart_bytes

            all_achievements = self._extract_game_achievements(payload)
            all_achievements.sort(key=lambda achievement: 1 if self._is_achievement_unlocked(achievement) else 0)
            first_locked_image_key = ""
            badge_fetch_attempts = 0
            badge_limit_logged = False
            for index, achievement in enumerate(all_achievements):
                ach_id = self._safe_int(achievement.get("ID"))
                image_key = f"achievement_{ach_id if ach_id > 0 else (index + 1)}_{index}"
                translated_achievement = dict(achievement)
                raw_description = self._safe_text(achievement.get("Description")) or "Sans description."
                translated_description = self._translate_achievement_description_cached_only(raw_description)
                translated_achievement["Description"] = translated_description or raw_description
                tooltip = self._build_achievement_tooltip(translated_achievement)
                is_unlocked = self._is_achievement_unlocked(achievement)
                badge_url = self._achievement_badge_url(achievement)
                locked_badge_url = self._locked_badge_url(badge_url) if badge_url else ""
                summary = self._build_next_achievement_summary(
                    translated_achievement,
                    total_players=total_players,
                    translate_description=False,
                )
                awarded_value = self._safe_int(achievement.get("NumAwarded"))
                true_ratio_value = self._safe_float(achievement.get("TrueRatio"))
                difficulty_known, difficulty_score = self._compute_achievement_difficulty_score(
                    awarded_value,
                    total_players,
                    true_ratio_value,
                )
                achievement_rows.append(
                    {
                        "image_key": image_key,
                        "tooltip": tooltip,
                        "is_unlocked": "1" if is_unlocked else "0",
                        "normal_order": str(index),
                        "next_id": str(ach_id),
                        "badge_url": badge_url,
                        "badge_url_locked": locked_badge_url,
                        "next_title": summary.get("title", ACHIEVEMENT_NA_VALUE),
                        "next_description": summary.get("description", ACHIEVEMENT_NA_VALUE),
                        "next_points": summary.get("points", ACHIEVEMENT_NA_VALUE),
                        "next_unlocks": summary.get("unlocks", ACHIEVEMENT_NA_VALUE),
                        "next_feasibility": summary.get("feasibility", ACHIEVEMENT_NA_VALUE),
                        "difficulty_known": "1" if difficulty_known else "0",
                        "difficulty_score": f"{difficulty_score:.6f}",
                    }
                )

                badge_bytes: bytes | None = None
                if badge_url:
                    if badge_fetch_attempts < MAX_ACHIEVEMENT_BADGE_FETCH:
                        badge_fetch_attempts += 1
                        preferred_url = badge_url if is_unlocked else self._locked_badge_url(badge_url)
                        badge_bytes = self._fetch_image_bytes(preferred_url)
                        if badge_bytes is None and preferred_url != badge_url:
                            badge_bytes = self._fetch_image_bytes(badge_url)
                    elif not badge_limit_logged:
                        badge_limit_logged = True
                        self._debug_log(
                            f"_fetch_current_game_worker limite images atteinte: {MAX_ACHIEVEMENT_BADGE_FETCH} badges max (complément en différé)"
                        )
                if badge_bytes:
                    images[image_key] = badge_bytes

                if next_achievement is None and not is_unlocked:
                    next_achievement = dict(summary)
                    first_locked_image_key = image_key

            if first_locked_image_key and first_locked_image_key in images:
                images["next_badge"] = images[first_locked_image_key]
        except (RetroAPIError, OSError, ValueError) as exc:
            error = str(exc)
            diagnostic_error = self._format_diagnostic_error(exc)
            self._debug_log(f"_fetch_current_game_worker erreur détails jeu_id={game_id}: {diagnostic_error}")

        title_value, console_value, progress_value, last_unlock_value, source_value, _ = self._build_current_game_local_rows(
            game_id,
            title_hint,
            games_lookup,
            source=source_label,
            rich_presence=rich_presence,
            summary_payload=summary_payload,
            game_payload=game_payload,
        )
        if self._is_live_source_label(source_label):
            note = "Jeu détecté en direct."
        elif source_label.startswith("Dernier jeu joué"):
            if emulator_live and not inactive_mode:
                note = "Émulateur chargé: jeu direct indisponible, affichage du dernier jeu joué."
            else:
                note = "Émulateur inactif: affichage du dernier jeu joué."
        else:
            note = "Détails chargés."
        self._debug_log(
            f"_fetch_current_game_worker fin token={fetch_token} game_id={game_id} "
            f"source='{source_label}' note='{note}' error={'yes' if bool(error) else 'no'}"
        )
        self._probe(
            "current_game_worker_end",
            token=fetch_token,
            game_id=game_id,
            source=source_label,
            note=note,
            error=bool(error),
            achievement_rows=len(achievement_rows),
        )
        self._queue_ui_callback(
            lambda: self._on_current_game_loaded(
                fetch_token=fetch_token,
                key=(username, game_id),
                title_value=title_value,
                console_value=console_value,
                progress_value=progress_value,
                last_unlock_value=last_unlock_value,
                source_value=source_value,
                next_achievement=next_achievement,
                achievement_rows=achievement_rows,
                images=images,
                error=error,
                diagnostic_error=diagnostic_error,
                note=note,
            )
        )

    # Method: _on_current_game_unchanged - Conserve l'affichage actuel quand le jeu détecté n'a pas changé.
    def _on_current_game_unchanged(
        self,
        fetch_token: int,
        note: str,
        source_value: str = "",
        diagnostic_error: str | None = None,
    ) -> None:
        if fetch_token != self._current_game_fetch_token:
            self._debug_log(
                f"_on_current_game_unchanged ignoré: token={fetch_token} attendu={self._current_game_fetch_token}"
            )
            return
        self.current_game_fetch_in_progress = False
        self._end_transition_timer()
        self._debug_log(
            f"_on_current_game_unchanged token={fetch_token} source='{source_value}' note='{note}'"
        )
        if source_value.strip():
            self._set_current_game_source(source_value)
        # Nettoie les traces visuelles d'un mode précédent (ex: Rich Presence).
        self._rebuild_current_game_info_rows_from_ui_state()
        effective_source = source_value or self.current_game_source.get()
        self._sync_emulator_status_after_current_game_update(effective_source)
        if diagnostic_error:
            self.status_text.set(diagnostic_error)
        self.current_game_note.set(note)
        self._persist_current_game_cache_after_inactive_transition_if_needed(
            source_value or self.current_game_source.get()
        )
        self._trigger_refresh_after_live_game_loaded(source_value or self.current_game_source.get())
        if self._has_missing_current_game_achievement_badges():
            self._debug_log("_on_current_game_unchanged: relance chargement badges manquants (N/A détecté).")
            self._start_missing_achievement_badges_loader()
        if self.current_game_achievement_tiles and self.current_game_achievement_scroll_job is None:
            self._restart_current_game_achievement_auto_scroll(immediate=False)
        self._finalize_current_game_loading_overlay()

    # Method: _trigger_refresh_after_live_game_loaded - Déclenche une actualisation unique après le premier chargement en mode Live.
    def _trigger_refresh_after_live_game_loaded(self, source_value: str) -> None:
        if not self.pending_refresh_after_live_game_load:
            return
        if not self._is_emulator_process_live():
            self.pending_refresh_after_live_game_load = False
            return
        self.pending_refresh_after_live_game_load = False
        self._debug_log(
            f"_trigger_refresh_after_live_game_loaded source='{source_value}'"
        )

        def do_refresh() -> None:
            if self.is_closing:
                return
            self.refresh_dashboard(
                show_errors=False,
                sync_before_refresh=False,
                force_current_game_refresh=False,
            )
        if self._has_valid_connection():
            self._request_event_sync("jeu direct chargé", delay_ms=0)

        try:
            self.root.after(0, do_refresh)
        except TclError:
            return

    # Method: _sync_emulator_status_after_current_game_update - Ajuste le statut Live selon la source réellement chargée.
    def _sync_emulator_status_after_current_game_update(self, source_value: str) -> None:
        previous_status = self.emulator_status_text.get().strip()
        emulator_process_live = self._is_emulator_process_live()
        if self._is_live_source_label(source_value) and emulator_process_live:
            self._set_emulator_status_text(EMULATOR_STATUS_GAME_LOADED)
            self._probe(
                "sync_status_after_game_update",
                source=source_value,
                previous_status=previous_status,
                next_status=EMULATOR_STATUS_GAME_LOADED,
            )
            return
        if not emulator_process_live:
            self._set_emulator_status_text(EMULATOR_STATUS_INACTIVE)
            self._probe(
                "sync_status_after_game_update",
                source=source_value,
                previous_status=previous_status,
                next_status=EMULATOR_STATUS_INACTIVE,
            )
            return
        self._set_emulator_status_text(EMULATOR_STATUS_EMULATOR_LOADED)
        self._probe(
            "sync_status_after_game_update",
            source=source_value,
            previous_status=previous_status,
            next_status=EMULATOR_STATUS_EMULATOR_LOADED,
        )

    # Method: _on_current_game_loaded - Traite l'événement correspondant.
    def _on_current_game_loaded(
        self,
        fetch_token: int,
        key: tuple[str, int],
        title_value: str,
        console_value: str,
        progress_value: str,
        last_unlock_value: str,
        source_value: str,
        next_achievement: dict[str, str] | None,
        achievement_rows: list[dict[str, str]],
        images: dict[str, bytes],
        error: str | None,
        diagnostic_error: str | None,
        note: str,
    ) -> None:
        if fetch_token != self._current_game_fetch_token:
            self._debug_log(
                f"_on_current_game_loaded ignoré: token={fetch_token} attendu={self._current_game_fetch_token}"
            )
            return
        self.current_game_fetch_in_progress = False
        self._end_transition_timer()
        self._debug_log(
            f"_on_current_game_loaded token={fetch_token} key={key} source='{source_value}' "
            f"error={'yes' if bool(error) else 'no'} diag={'yes' if bool(diagnostic_error) else 'no'}"
        )
        self._current_game_last_key = key
        self.current_game_title.set(title_value)
        self.current_game_console.set(console_value)
        self.current_game_progress.set(progress_value)
        self.current_game_last_unlock.set(last_unlock_value)
        self._set_current_game_source(source_value)
        if self._is_live_source_label(source_value):
            self.prefer_persisted_current_game_on_startup = False
        self._sync_emulator_status_after_current_game_update(source_value)
        if diagnostic_error:
            self.status_text.set(diagnostic_error)
        if error:
            self._debug_log(f"_on_current_game_loaded détails indisponibles: {error}")
            self.current_game_note.set(f"Détails indisponibles: {error}")
            self._set_current_game_achievement_rows(None, has_achievements=False)
            self._set_current_game_achievement_gallery([], {})
            self._set_current_game_images({})
            self._sync_locked_achievement_navigation([], None)
            self._persist_current_game_cache_after_inactive_transition_if_needed(
                source_value or self.current_game_source.get()
            )
            self._trigger_refresh_after_live_game_loaded(source_value or self.current_game_source.get())
            self._finalize_current_game_loading_overlay_after_gallery()
            return

        self.current_game_note.set(note)
        self._current_game_details_cache[key] = {
            "next_achievement": dict(next_achievement) if next_achievement else {},
            "achievements": [dict(item) for item in achievement_rows],
        }
        self._current_game_images_cache[key] = images
        self._set_current_game_achievement_rows(next_achievement, has_achievements=bool(achievement_rows))
        self._set_current_game_achievement_gallery(achievement_rows, images)
        self._set_current_game_images(images)
        preferred_next = (
            next_achievement if self.current_game_achievement_order_mode == ACHIEVEMENT_ORDER_NORMAL else None
        )
        self._sync_locked_achievement_navigation(achievement_rows, preferred_next)
        self._persist_current_game_cache_after_inactive_transition_if_needed(
            source_value or self.current_game_source.get()
        )
        self._trigger_refresh_after_live_game_loaded(source_value or self.current_game_source.get())
        self._finalize_current_game_loading_overlay_after_gallery()

    # Method: _stat_label - Réalise le traitement lié à stat label.
    def _stat_label(self, parent: ttk.LabelFrame, title: str, var: StringVar) -> ttk.Frame:
        cell = ttk.Frame(parent)
        ttk.Label(cell, text=title).grid(row=0, column=0, sticky=W)
        ttk.Label(cell, textvariable=var).grid(row=1, column=0, sticky=W, pady=(2, 0))
        return cell

    # Method: _on_root_configure - Traite l'événement correspondant.
    def _on_root_configure(self, event: object) -> None:
        widget = getattr(event, "widget", None)
        if widget is not self.root:
            return
        self._apply_rounded_widget_region(self.root)

        width = self.root.winfo_width()
        if width <= 0 or abs(width - self._last_layout_width) < 12:
            return
        self._last_layout_width = width
        self._apply_responsive_layout(width)

    # Method: _apply_responsive_layout - Applique les paramètres ou la transformation nécessaires.
    def _apply_responsive_layout(self, width: int) -> None:
        if (
            self.top_bar is not None
            and self.connection_button
            and self.profile_button
            and self.theme_toggle_frame
        ):
            for col in range(3):
                self.top_bar.columnconfigure(col, weight=0)
            self.top_bar.columnconfigure(2, weight=1)
            self.connection_button.grid_configure(row=0, column=0, columnspan=1, padx=(0, 8), pady=0, sticky=W)
            self.profile_button.grid_configure(row=0, column=1, columnspan=1, padx=(0, 8), pady=0, sticky=W)
            self.theme_toggle_frame.grid_configure(row=0, column=2, columnspan=1, padx=(0, 8), pady=0, sticky="e")

        if self.current_game_title_value_label is not None:
            self.current_game_title_value_label.configure(wraplength=max(240, min(920, width - 470)))
        if self.current_game_next_achievement_desc_label is not None:
            self.current_game_next_achievement_desc_label.configure(wraplength=max(220, min(820, width - 360)))
        self._layout_current_game_achievement_gallery()

        if self.status_label is not None:
            self.status_label.configure(wraplength=max(180, width - 360))
        if (
            self.status_bar is not None
            and self.version_label is not None
            and self.performance_timer_label is not None
        ):
            self.status_label.grid_configure(row=0, column=0, columnspan=1, sticky="w", padx=0, pady=0)
            self.performance_timer_label.grid_configure(row=0, column=1, sticky="e", pady=0, padx=(0, 8))
            self.version_label.grid_configure(row=0, column=2, sticky="e", pady=0, padx=0)
    # Method: _reset_event_watch_state - Réinitialise l'état local de détection des changements distants.
    def _reset_event_watch_state(self) -> None:
        self._event_watch_username = ""
        self._event_watch_game_id = 0
        self._event_watch_unlock_marker = ""
        self._event_pending_game_id = 0
        self._event_pending_unlock_marker = ""

    # Method: _extract_summary_unlock_marker - Construit une signature compacte pour détecter un nouveau succès.
    def _extract_summary_unlock_marker(self, summary: dict[str, object]) -> str:
        points = self._safe_int(summary.get("TotalPoints") or summary.get("Points"))
        softcore_points = self._safe_int(summary.get("TotalSoftcorePoints"))
        true_points = self._safe_int(summary.get("TotalTruePoints"))
        latest_award = ""
        best_timestamp = -1.0

        for key in ("LastAchievementDate", "LastAwardedDate", "LastActivity"):
            raw = self._safe_text(summary.get(key))
            if not raw:
                continue
            parsed = self._parse_sort_datetime(raw)
            timestamp = parsed.timestamp() if parsed is not None else -1.0
            if timestamp > best_timestamp:
                best_timestamp = timestamp
                latest_award = raw

        recent = summary.get("RecentlyPlayed")
        if isinstance(recent, list):
            for item in recent:
                if not isinstance(item, dict):
                    continue
                raw = (
                    self._safe_text(item.get("MostRecentAwardedDate"))
                    or self._safe_text(item.get("LastAwardedDate"))
                    or self._safe_text(item.get("DateModified"))
                )
                if not raw:
                    continue
                parsed = self._parse_sort_datetime(raw)
                timestamp = parsed.timestamp() if parsed is not None else -1.0
                if timestamp > best_timestamp:
                    best_timestamp = timestamp
                    latest_award = raw
        return f"{points}|{softcore_points}|{true_points}|{latest_award}"

    # Method: _event_sync_probe_worker - Vérifie les changements distants puis notifie l'UI.
    def _event_sync_probe_worker(self, api_key: str, username: str, emulator_live: bool, reason: str) -> None:
        detected_game_id = 0
        live_game_id = 0
        unlock_marker = ""
        diagnostic_error: str | None = None
        try:
            client = RetroAchievementsClient(api_key, timeout_seconds=8)
            summary = client.get_user_summary(username, include_recent_games=True)
            live_game_id, _, _, _, live_decision = self._extract_live_current_game(
                summary,
                emulator_live=emulator_live,
            )
            if live_game_id > 0:
                detected_game_id = live_game_id
            elif (not emulator_live) and detected_game_id <= 0:
                # En mode émulateur live, ne pas retomber sur le dernier jeu joué:
                # sinon un jeu déchargé reste vu comme "Jeu chargé".
                detected_game_id, _ = self._extract_last_played_game(summary)
            unlock_marker = self._extract_summary_unlock_marker(summary)
            self._probe(
                "event_probe_worker",
                reason=reason,
                emulator_live=emulator_live,
                live_game_id=live_game_id,
                live_decision=live_decision,
                detected_game_id=detected_game_id,
                unlock_marker=bool(unlock_marker),
            )
        except (RetroAPIError, OSError, ValueError) as exc:
            diagnostic_error = self._format_diagnostic_error(exc)
            self._probe(
                "event_probe_worker_error",
                reason=reason,
                emulator_live=emulator_live,
                live_game_id=live_game_id,
                detected_game_id=detected_game_id,
                error=diagnostic_error,
            )

        self._queue_ui_callback(
            lambda: self._on_event_sync_probe_result(
                username=username,
                detected_game_id=detected_game_id,
                unlock_marker=unlock_marker,
                diagnostic_error=diagnostic_error,
                reason=reason,
            )
        )

    # Method: _on_event_sync_probe_result - Décide de synchroniser seulement si un changement pertinent est détecté.
    def _on_event_sync_probe_result(
        self,
        username: str,
        detected_game_id: int,
        unlock_marker: str,
        diagnostic_error: str | None,
        reason: str,
    ) -> None:
        self.event_probe_in_progress = False
        if self.is_closing:
            return
        if diagnostic_error:
            self._set_status_message(diagnostic_error)
            self._debug_log(f"_on_event_sync_probe_result diagnostic={diagnostic_error}")
            self._probe(
                "event_probe_result",
                reason=reason,
                branch="diagnostic_error",
                detected_game_id=detected_game_id,
                diagnostic_error=diagnostic_error,
            )
            return

        if self._event_watch_username != username:
            self._event_watch_username = username
            self._event_watch_game_id = detected_game_id
            self._event_watch_unlock_marker = unlock_marker
            self._set_status_message("Surveillance active: en attente d'un changement.", muted=True)
            self._debug_log(
                f"_on_event_sync_probe_result baseline user='{username}' game_id={detected_game_id}"
            )
            self._probe(
                "event_probe_result",
                reason=reason,
                branch="baseline",
                detected_game_id=detected_game_id,
            )
            return

        previous_game_id = self._event_watch_game_id
        game_changed = detected_game_id != previous_game_id
        ui_status = self.emulator_status_text.get().strip().casefold()
        ui_thinks_game_loaded = ui_status == EMULATOR_STATUS_GAME_LOADED.casefold()
        forced_unload_refresh = ui_thinks_game_loaded and detected_game_id <= 0
        effective_game_changed = game_changed or forced_unload_refresh
        unlock_changed = bool(unlock_marker) and unlock_marker != self._event_watch_unlock_marker
        self._probe(
            "event_probe_result_eval",
            reason=reason,
            previous_game_id=previous_game_id,
            detected_game_id=detected_game_id,
            game_changed=game_changed,
            forced_unload_refresh=forced_unload_refresh,
            unlock_changed=unlock_changed,
            ui_status=ui_status,
        )

        if not effective_game_changed and not unlock_changed:
            self._event_watch_game_id = detected_game_id
            self._event_watch_unlock_marker = unlock_marker
            self._set_status_message(
                "Surveillance active: aucun nouveau succès ni changement de jeu.",
                muted=True,
            )
            self._probe(
                "event_probe_result",
                reason=reason,
                branch="no_change",
                detected_game_id=detected_game_id,
                unlock_changed=unlock_changed,
                forced_unload_refresh=forced_unload_refresh,
            )
            return

        if self.sync_in_progress:
            self._probe(
                "event_probe_result",
                reason=reason,
                branch="sync_in_progress_reschedule",
                detected_game_id=detected_game_id,
            )
            self._request_event_sync(reason, delay_ms=700)
            return

        if effective_game_changed and not unlock_changed:
            self._event_watch_game_id = detected_game_id
            self._event_watch_unlock_marker = unlock_marker
            if detected_game_id <= 0 and (previous_game_id > 0 or forced_unload_refresh):
                # Applique immédiatement l'état visuel attendu sans attendre le refresh complet.
                self._set_emulator_status_text(EMULATOR_STATUS_EMULATOR_LOADED)
                self._debug_log(
                    "_on_event_sync_probe_result refresh rapide: jeu déchargé (retour état émulateur)"
                )
                self._set_status_message("Jeu déchargé: rafraîchissement rapide...", muted=True)
                self._probe(
                    "event_probe_result",
                    reason=reason,
                    branch="quick_refresh_unload",
                    previous_game_id=previous_game_id,
                    detected_game_id=detected_game_id,
                    forced_unload_refresh=forced_unload_refresh,
                )
            else:
                self._debug_log(
                    f"_on_event_sync_probe_result refresh rapide: changement de jeu game_id={detected_game_id}"
                )
                self._set_status_message("Changement de jeu détecté: rafraîchissement rapide...", muted=True)
                self._probe(
                    "event_probe_result",
                    reason=reason,
                    branch="quick_refresh_game_change",
                    previous_game_id=previous_game_id,
                    detected_game_id=detected_game_id,
                    forced_unload_refresh=forced_unload_refresh,
                )
            self.refresh_dashboard(show_errors=False, sync_before_refresh=False)
            return

        if effective_game_changed and unlock_changed:
            trigger_reason = "changement de jeu et succès débloqué"
        else:
            trigger_reason = "succès débloqué"

        self._debug_log(
            f"_on_event_sync_probe_result déclenche sync: reason='{trigger_reason}' "
            f"game_id={detected_game_id}"
        )
        self._probe(
            "event_probe_result",
            reason=reason,
            branch="trigger_sync",
            trigger_reason=trigger_reason,
            detected_game_id=detected_game_id,
            unlock_changed=unlock_changed,
            game_changed=game_changed,
        )
        self._event_pending_game_id = detected_game_id
        self._event_pending_unlock_marker = unlock_marker
        self._set_status_message(f"Synchronisation déclenchée ({trigger_reason})...", muted=True)
        self.sync_now(show_errors=False)

    # Method: _restart_auto_sync - Réalise le traitement lié à restart auto sync.
    def _restart_auto_sync(self, immediate: bool = False) -> None:
        if self.auto_sync_job is not None:
            try:
                self.root.after_cancel(self.auto_sync_job)
            except TclError:
                pass
            self.auto_sync_job = None
        if self.is_closing:
            return
        delay_ms = 0 if immediate else AUTO_SYNC_INTERVAL_MS
        try:
            self.auto_sync_job = self.root.after(delay_ms, self._auto_sync_tick)
        except TclError:
            self.auto_sync_job = None

    # Method: _auto_sync_tick - Exécute un traitement automatique planifié.
    def _auto_sync_tick(self) -> None:
        self.auto_sync_job = None
        if self.is_closing:
            return

        # Watchdog: si la boucle de détection émulateur a été interrompue,
        # on la relance pour conserver une surveillance continue.
        if self.emulator_poll_job is None and not self.emulator_probe_in_progress:
            self._restart_emulator_probe(immediate=True)

        if self._has_valid_connection():
            effective_live = self._is_emulator_process_live()
            min_gap_ms = EVENT_SYNC_LIVE_MIN_GAP_MS if effective_live else EVENT_SYNC_IDLE_MIN_GAP_MS
            self._request_event_sync_throttled(
                "surveillance continue",
                delay_ms=0,
                min_gap_ms=min_gap_ms,
            )

        self._restart_auto_sync(immediate=False)

    # Method: _cancel_event_sync - Annule la synchronisation par événement en attente.
    def _cancel_event_sync(self) -> None:
        if self.event_sync_job is not None:
            try:
                self.root.after_cancel(self.event_sync_job)
            except TclError:
                pass
            self.event_sync_job = None
        self.pending_event_sync_reason = ""
    # Method: _request_event_sync - Planifie une synchronisation déclenchée par un événement.
    def _request_event_sync(self, reason: str, delay_ms: int = EVENT_SYNC_DELAY_MS) -> None:
        if self.is_closing:
            return
        if not self._has_valid_connection():
            self._set_status_message(self._connection_diagnostic())
            return
        normalized_reason = reason.strip() or "événement"
        self.pending_event_sync_reason = normalized_reason
        if self.event_sync_job is not None:
            try:
                self.root.after_cancel(self.event_sync_job)
            except TclError:
                pass
            self.event_sync_job = None
        delay = max(0, int(delay_ms))
        self.event_sync_job = self.root.after(delay, self._run_event_sync)
        self._last_event_sync_request_monotonic = time.monotonic()

    # Method: _request_event_sync_throttled - Planifie la surveillance distante avec limitation de fréquence.
    def _request_event_sync_throttled(
        self,
        reason: str,
        delay_ms: int = EVENT_SYNC_DELAY_MS,
        min_gap_ms: int = EVENT_SYNC_LIVE_MIN_GAP_MS,
    ) -> None:
        if self.is_closing or not self._has_valid_connection():
            return
        if self.event_sync_job is not None:
            if delay_ms <= 0:
                self._request_event_sync(reason, delay_ms=0)
            return
        if self.sync_in_progress or self.event_probe_in_progress:
            return
        elapsed_ms = (time.monotonic() - self._last_event_sync_request_monotonic) * 1000.0
        if self._last_event_sync_request_monotonic > 0.0 and elapsed_ms < float(min_gap_ms):
            return
        self._request_event_sync(reason, delay_ms=delay_ms)

    # Method: _run_event_sync - Exécute la synchronisation demandée par un événement.
    def _run_event_sync(self) -> None:
        self.event_sync_job = None
        reason = self.pending_event_sync_reason.strip() or "événement"
        self.pending_event_sync_reason = ""
        if self.is_closing:
            return
        if self.sync_in_progress:
            self._request_event_sync(reason, delay_ms=160)
            return
        if not self._has_valid_connection():
            self._set_status_message(self._connection_diagnostic())
            return
        if self.event_probe_in_progress:
            self._request_event_sync(reason, delay_ms=160)
            return
        api_key = self.api_key.get().strip()
        username = self._tracked_username()
        emulator_live = self._is_emulator_process_live()
        if not api_key or not username:
            self._set_status_message(self._connection_diagnostic())
            return
        self.event_probe_in_progress = True
        self._set_status_message(f"Vérification des changements ({reason})...", muted=True)
        worker = threading.Thread(
            target=self._event_sync_probe_worker,
            args=(api_key, username, emulator_live, reason),
            daemon=True,
        )
        worker.start()
    # Method: _cancel_scheduled_jobs - Annule les opérations planifiées.
    def _cancel_scheduled_jobs(self) -> None:
        for job_name in (
            "auto_sync_job",
            "event_sync_job",
            "status_muted_reset_job",
            "performance_timer_update_job",
            "modal_track_job",
            "startup_init_job",
            "startup_finish_job",
            "startup_connection_job",
            "saved_window_geometry_apply_job",
            "emulator_poll_job",
            "emulator_status_refresh_job",
            "current_game_loading_timeout_job",
            "current_game_loading_hard_timeout_job",
            "current_game_achievement_scroll_job",
            "current_game_clicked_achievement_restore_job",
        ):
            job_id = getattr(self, job_name, None)
            if job_id is None:
                continue
            try:
                self.root.after_cancel(job_id)
            except TclError:
                pass
            setattr(self, job_name, None)
        self._saved_window_geometry_pending = ""
        self._saved_window_geometry_retry_remaining = 0
        self._loading_timer_active = False
        self._transition_timer_active = False
        self._loading_timer_started_monotonic = 0.0
        self._transition_timer_started_monotonic = 0.0
        self.emulator_probe_in_progress = False
        self.event_probe_in_progress = False

    # Method: _on_app_close - Traite l'événement correspondant.
    def _on_app_close(self) -> None:
        if self.is_closing:
            return
        self.is_closing = True
        self._hide_maintenance_tab_tooltip()
        self._hide_profile_maintenance_tooltip()
        self._hide_startup_loader()
        self._hide_current_game_loading_overlay()
        self._cancel_scheduled_jobs()
        self._close_profile_window()
        self._close_connection_window()
        self._persist_current_game_cache()
        self._save_window_geometry()
        try:
            self._http_session.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except TclError:
            pass
    # Method: _open_connection_if_missing - Ouvre l'élément demandé.
    def _open_connection_if_missing(self) -> None:
        self.startup_connection_job = None
        if self.is_closing:
            return
        if self._has_saved_valid_connection():
            return
        self.status_text.set("Configurez la connexion pour démarrer.")
        self.open_connection_window()

    # Method: _show_modal_overlay - Réalise le traitement lié à show modal overlay.
    def _show_modal_overlay(self) -> None:
        if self.modal_overlay is not None and self.modal_overlay.winfo_exists():
            return

        overlay = Canvas(self.root, bg="#000000", highlightthickness=0, bd=0)
        self.modal_overlay = overlay
        overlay.place(x=0, y=0, relwidth=1, relheight=1)
        overlay.create_rectangle(0, 0, 1, 1, fill="#000000", outline="", stipple="gray25", tags="shade")
        overlay.bind("<Configure>", lambda event: overlay.coords("shade", 0, 0, event.width, event.height))
        overlay.bind("<Button-1>", lambda _event: "break")
        self._sync_modal_overlay()

    # Method: _active_modal_window - Réalise le traitement lié à active modal window.
    def _active_modal_window(self) -> Toplevel | None:
        if self.connection_window is not None and self.connection_window.winfo_exists():
            return self.connection_window
        if self.profile_window is not None and self.profile_window.winfo_exists():
            return self.profile_window
        return None

    # Method: _sync_modal_overlay - Synchronise les données concernées.
    def _sync_modal_overlay(self) -> None:
        if self.modal_overlay is None or not self.modal_overlay.winfo_exists():
            return

        self.modal_overlay.place(x=0, y=0, relwidth=1, relheight=1)
        modal = self._active_modal_window()
        if modal is not None:
            modal.lift()

    # Method: _hide_modal_overlay - Réalise le traitement lié à hide modal overlay.
    def _hide_modal_overlay(self) -> None:
        if self.modal_overlay is None:
            return
        if self.modal_overlay.winfo_exists():
            self.modal_overlay.place_forget()
            self.modal_overlay.destroy()
        self.modal_overlay = None

    # Method: _start_modal_tracking - Démarre le processus associé.
    def _start_modal_tracking(self) -> None:
        if self.is_closing:
            return
        self._stop_modal_tracking()
        self.modal_track_job = self.root.after(120, self._track_modal_position)

    # Method: _stop_modal_tracking - Arrête le processus associé.
    def _stop_modal_tracking(self) -> None:
        if self.modal_track_job is None:
            return
        try:
            self.root.after_cancel(self.modal_track_job)
        except TclError:
            pass
        self.modal_track_job = None

    # Method: _track_modal_position - Réalise le traitement lié à track modal position.
    def _track_modal_position(self) -> None:
        self.modal_track_job = None
        if self.is_closing:
            return
        modal = self._active_modal_window()
        if modal is None:
            return

        anchor = (self.root.winfo_rootx(), self.root.winfo_rooty(), self.root.winfo_width(), self.root.winfo_height())
        if anchor != self._last_modal_anchor:
            self._last_modal_anchor = anchor
            self._sync_modal_overlay()
            self._center_modal_window(modal)

        self._start_modal_tracking()

    # Method: _center_modal_window - Centre l'élément concerné dans son conteneur.
    def _center_modal_window(self, modal: Toplevel | None) -> None:
        if modal is None or not modal.winfo_exists():
            return

        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        win_w = max(modal.winfo_width(), modal.winfo_reqwidth())
        win_h = max(modal.winfo_height(), modal.winfo_reqheight())

        x = root_x + max(0, (root_w - win_w) // 2)
        y = root_y + max(0, (root_h - win_h) // 2)
        current_x = modal.winfo_rootx()
        current_y = modal.winfo_rooty()
        if abs(current_x - x) > 1 or abs(current_y - y) > 1:
            modal.geometry(f"+{x}+{y}")
    # Method: open_connection_window - Ouvre l'élément demandé.
    def open_connection_window(self) -> None:
        if self.profile_window is not None and self.profile_window.winfo_exists():
            self._close_profile_window()
        if self.connection_window is not None and self.connection_window.winfo_exists():
            self._sync_modal_overlay()
            self._center_modal_window(self.connection_window)
            self.connection_window.lift()
            self.connection_window.focus_force()
            return

        self._show_modal_overlay()
        self._start_modal_tracking()
        win = Toplevel(self.root)
        self.connection_window = win
        self.root.after_idle(lambda: self._apply_rounded_window_corners(win))
        win.title("Connexion à RetroAchievements")
        win.transient(self.root)
        win.grab_set()
        win.resizable(True, True)
        win.minsize(420, 160)
        win.configure(bg=self.theme_colors.get("root_bg", "#f3f5f8"))
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)
        win.protocol("WM_DELETE_WINDOW", self._close_connection_window)
        win.bind("<Configure>", lambda _event: self._on_modal_window_configure(self.connection_window))

        content = ttk.Frame(win, style="Modal.TFrame", padding=(12, 12, 12, 10))
        content.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        content.columnconfigure(1, weight=1)

        key_var = StringVar(value=self.api_key.get())
        api_user_var = StringVar(value=self.api_username.get())

        ttk.Label(content, text="Clé API", style="Modal.TLabel").grid(row=0, column=0, sticky=W, padx=2, pady=(2, 6))
        ttk.Entry(content, textvariable=key_var, show="*", style="Modal.TEntry").grid(
            row=0, column=1, sticky="ew", padx=2, pady=(2, 6)
        )

        ttk.Label(content, text="Nom d'utilisateur API", style="Modal.TLabel").grid(row=1, column=0, sticky=W, padx=2, pady=6)
        ttk.Entry(content, textvariable=api_user_var, style="Modal.TEntry").grid(row=1, column=1, sticky="ew", padx=2, pady=6)

        buttons = ttk.Frame(content, style="Modal.TFrame")
        buttons.grid(row=2, column=0, columnspan=2, sticky="e", padx=2, pady=(10, 2))
        ttk.Button(
            buttons,
            text="Enregistrer",
            command=lambda: self._apply_connection_from_dialog(
                key_var.get(), api_user_var.get(), self.tracked_username.get(), self.db_path.get()
            ),
            style="Modal.TButton",
        ).pack(side=LEFT, padx=(0, 8))
        ttk.Button(buttons, text="Annuler", command=self._close_connection_window, style="Modal.TButton").pack(side=LEFT)
        self.root.after_idle(lambda: self._apply_rounded_corners_to_widget_tree(win))
        self._sync_modal_overlay()
        self._center_modal_window(self.connection_window)
        self._last_modal_anchor = (self.root.winfo_rootx(), self.root.winfo_rooty(), self.root.winfo_width(), self.root.winfo_height())
        win.lift()
        win.focus_force()

    # Method: _apply_connection_from_dialog - Applique les paramètres ou la transformation nécessaires.
    def _apply_connection_from_dialog(
        self, api_key: str, api_username: str, tracked_username: str, db_path: str
    ) -> None:
        self.api_key.set(api_key.strip())
        self.api_username.set(api_username.strip())
        self.tracked_username.set(tracked_username.strip())
        self.db_path.set(db_path.strip())
        self.save_config()
        self._close_connection_window()

    # Method: _close_connection_window - Réalise le traitement lié à close connection window.
    def _close_connection_window(self) -> None:
        if self.connection_window is None:
            return
        if self.connection_window.winfo_exists():
            try:
                self.connection_window.grab_release()
            except TclError:
                pass
            self.connection_window.destroy()
        self.connection_window = None

        if self._active_modal_window() is None:
            self._stop_modal_tracking()
            self._last_modal_anchor = (0, 0, 0, 0)
            self._hide_modal_overlay()
        else:
            self._sync_modal_overlay()

    # Method: _apply_profile_layout - Applique les paramètres ou la transformation nécessaires.
    def _apply_profile_layout(self, width: int) -> None:
        if self.stats_frame is None or not self.stat_cells:
            return

        max_columns = 7
        desired = max(1, min(max_columns, width // 185))
        for col in range(max_columns):
            self.stats_frame.columnconfigure(col, weight=0)
        for col in range(desired):
            self.stats_frame.columnconfigure(col, weight=1)

        for idx, cell in enumerate(self.stat_cells):
            row = idx // desired
            col = idx % desired
            cell.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")

    # Method: _on_modal_window_configure - Traite l'événement correspondant.
    def _on_modal_window_configure(self, modal: Toplevel | None) -> None:
        self._sync_modal_overlay()
        self._center_modal_window(modal)

    # Method: _on_profile_window_configure - Traite l'événement correspondant.
    def _on_profile_window_configure(self, _event: object) -> None:
        self._on_modal_window_configure(self.profile_window)
        if self.profile_window is None or not self.profile_window.winfo_exists():
            return
        width = self.profile_window.winfo_width()
        if width <= 0 or abs(width - self._last_profile_layout_width) < 12:
            return
        self._last_profile_layout_width = width
        self._apply_profile_layout(width)

    # Method: open_profile_window - [Dormant] Ouvre la fenêtre profil complète.
    # Conservé volontairement pour réactivation ultérieure; l'UI actuelle reste en maintenance.
    def open_profile_window(self) -> None:
        if not self._has_saved_valid_connection():
            self.status_text.set("Aucune connexion valide. Configurez la connexion.")
            self.open_connection_window()
            return

        if self.connection_window is not None and self.connection_window.winfo_exists():
            self._close_connection_window()

        if self.profile_window is not None and self.profile_window.winfo_exists():
            self._sync_modal_overlay()
            self._center_modal_window(self.profile_window)
            self.profile_window.lift()
            self.profile_window.focus_force()
            return

        self._show_modal_overlay()
        self._start_modal_tracking()
        win = Toplevel(self.root)
        self.profile_window = win
        self.root.after_idle(lambda: self._apply_rounded_window_corners(win))
        win.title("Profil RetroAchievements")
        win.transient(self.root)
        win.grab_set()
        win.resizable(True, True)
        win.minsize(520, 340)
        win.configure(bg=self.theme_colors.get("root_bg", "#f3f5f8"))
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)
        win.protocol("WM_DELETE_WINDOW", self._close_profile_window)
        win.bind("<Configure>", self._on_profile_window_configure)

        content = ttk.Frame(win, style="Modal.TFrame", padding=(10, 10, 10, 10))
        content.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)

        self.stats_frame = ttk.LabelFrame(content, text="Statistiques", style="TLabelframe")
        self.stats_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.stat_cells = [
            self._stat_label(self.stats_frame, "Points", self.stat_points),
            self._stat_label(self.stats_frame, "Points softcore", self.stat_softcore),
            self._stat_label(self.stats_frame, "Points true", self.stat_true),
            self._stat_label(self.stats_frame, "Jeux maîtrisés", self.stat_mastered),
            self._stat_label(self.stats_frame, "Jeux terminés", self.stat_beaten),
            self._stat_label(self.stats_frame, "Jeux", self.stat_games),
            self._stat_label(self.stats_frame, "Dernière synchro", self.stat_snapshot),
        ]

        buttons = ttk.Frame(content, style="Modal.TFrame")
        buttons.grid(row=2, column=0, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Fermer", command=self._close_profile_window, style="Modal.TButton").pack(side=LEFT)

        self._apply_profile_layout(win.winfo_width())
        self.root.after_idle(lambda: self._apply_rounded_corners_to_widget_tree(win))
        self._sync_modal_overlay()
        self._center_modal_window(self.profile_window)
        self._last_modal_anchor = (self.root.winfo_rootx(), self.root.winfo_rooty(), self.root.winfo_width(), self.root.winfo_height())
        win.lift()
        win.focus_force()
        self.refresh_dashboard(show_errors=False)

    # Method: _close_profile_window - Réalise le traitement lié à close profile window.
    def _close_profile_window(self) -> None:
        if self.profile_window is None:
            return
        if self.profile_window.winfo_exists():
            try:
                self.profile_window.grab_release()
            except TclError:
                pass
            self.profile_window.destroy()
        self.profile_window = None
        self.stats_frame = None
        self.stat_cells = []
        self._last_profile_layout_width = 0

        if self._active_modal_window() is None:
            self._stop_modal_tracking()
            self._last_modal_anchor = (0, 0, 0, 0)
            self._hide_modal_overlay()
        else:
            self._sync_modal_overlay()

    # Method: _ensure_db_ready - Vérifie les préconditions avant l'exécution.
    def _ensure_db_ready(self, show_errors: bool = True) -> bool:
        db = self.db_path.get().strip()
        self._debug_log(f"_ensure_db_ready start show_errors={show_errors} db_path='{db}'")
        if not db:
            self.status_text.set("Diagnostic SQLite: chemin de base de données vide.")
            if show_errors:
                messagebox.showerror("Erreur", "Le chemin de la base est obligatoire.")
            self._debug_log("_ensure_db_ready échec: db_path vide")
            return False
        try:
            Path(db).parent.mkdir(parents=True, exist_ok=True)
            init_db(db)
        except (OSError, sqlite3.Error) as exc:
            self.status_text.set(self._format_diagnostic_error(exc))
            if show_errors:
                messagebox.showerror("Erreur", f"Impossible d'initialiser la base de données: {exc}")
            self._debug_log(f"_ensure_db_ready échec: {exc}")
            return False
        self._debug_log("_ensure_db_ready succès")
        return True

    # Method: sync_now - Synchronise les données concernées.
    def sync_now(self, show_errors: bool = True) -> None:
        if self.sync_in_progress:
            self._debug_log(f"sync_now ignoré: sync déjà en cours show_errors={show_errors}")
            return
        api_key = self.api_key.get().strip()
        username = self._tracked_username()
        self._debug_log(
            f"sync_now start show_errors={show_errors} user='{username}' "
            f"api_key_present={'yes' if bool(api_key) else 'no'}"
        )
        if not api_key or not username:
            diagnostic = self._connection_diagnostic()
            self._set_status_message(diagnostic)
            if show_errors:
                messagebox.showerror("Erreur", diagnostic)
            self._debug_log(f"sync_now échec precheck: {diagnostic}")
            return
        if not self._ensure_db_ready(show_errors=show_errors):
            self._debug_log("sync_now abort: _ensure_db_ready=False")
            return

        self.sync_in_progress = True
        if self.sync_button is not None:
            self.sync_button.state(["disabled"])
        self._set_status_message(
            "Synchronisation en cours..." if show_errors else "Synchronisation auto en cours...",
            muted=not show_errors,
        )

        worker = threading.Thread(target=self._sync_worker, args=(show_errors,), daemon=True)
        worker.start()

    # Method: _sync_worker - Synchronise les données concernées.
    def _sync_worker(self, show_errors: bool) -> None:
        config = self._config_values()
        username = self._tracked_username()
        self._debug_log(
            f"_sync_worker start show_errors={show_errors} user='{username}' db_path='{config['db_path']}'"
        )
        try:
            client = RetroAchievementsClient(config["api_key"])
            snapshot = client.fetch_snapshot(username)
            save_snapshot(config["db_path"], snapshot)
        except (RetroAPIError, OSError, sqlite3.Error, ValueError) as exc:
            error_message = str(exc)
            diagnostic_message = self._format_diagnostic_error(exc)
            self._queue_ui_callback(
                lambda msg=error_message, diag=diagnostic_message: self._on_sync_error(msg, show_errors, diag)
            )
            return
        self._debug_log("_sync_worker succès")
        self._queue_ui_callback(lambda: self._on_sync_success(show_errors))

    # Method: _queue_ui_callback - Planifie l'action sur le thread d'interface.
    def _queue_ui_callback(self, callback) -> None:
        if self.is_closing:
            return

        def safe_callback() -> None:
            try:
                callback()
            except Exception as exc:
                logger = self.debug_logger or get_debug_logger()
                try:
                    logger.exception("Erreur callback UI queue: %s", exc)
                except Exception:
                    pass

        try:
            self.root.after(0, safe_callback)
        except TclError:
            return

    # Method: _on_sync_error - Traite l'événement correspondant.
    def _on_sync_error(self, message: str, show_errors: bool, diagnostic_message: str = "") -> None:
        self.sync_in_progress = False
        if self.sync_button is not None:
            self.sync_button.state(["!disabled"])
        self._debug_log(
            f"_on_sync_error show_errors={show_errors} diagnostic='{diagnostic_message}' message='{message}'"
        )
        self._event_pending_game_id = 0
        self._event_pending_unlock_marker = ""
        if diagnostic_message.strip():
            self._set_status_message(diagnostic_message)
        else:
            self._set_status_message(
                "Synchronisation échouée." if show_errors else "Synchronisation auto échouée.",
                muted=not show_errors,
            )
        if show_errors:
            messagebox.showerror("Erreur de synchronisation", message)

    # Method: _on_sync_success - Traite l'événement correspondant.
    def _on_sync_success(self, show_errors: bool) -> None:
        self.sync_in_progress = False
        if self.sync_button is not None:
            self.sync_button.state(["!disabled"])
        if self._event_watch_username == self._tracked_username():
            if self._event_pending_game_id > 0:
                self._event_watch_game_id = self._event_pending_game_id
            if self._event_pending_unlock_marker:
                self._event_watch_unlock_marker = self._event_pending_unlock_marker
        self._event_pending_game_id = 0
        self._event_pending_unlock_marker = ""
        self._debug_log(f"_on_sync_success show_errors={show_errors}")
        self._set_status_message(
            "Synchronisation terminée." if show_errors else "Synchronisation auto terminée.",
            muted=not show_errors,
        )
        self.refresh_dashboard(
            show_errors=show_errors,
            sync_before_refresh=False,
            force_current_game_refresh=True,
        )

    # Method: refresh_dashboard - Réalise le traitement lié à refresh dashboard.
    def refresh_dashboard(
        self,
        show_errors: bool = True,
        sync_before_refresh: bool = True,
        force_current_game_refresh: bool = False,
    ) -> None:
        self._debug_log(
            f"refresh_dashboard show_errors={show_errors} sync_before_refresh={sync_before_refresh} "
            f"force_current_game_refresh={force_current_game_refresh} "
            f"user='{self._tracked_username()}' emulator='{self.emulator_status_text.get().strip()}'"
        )
        if not self._ensure_db_ready(show_errors=show_errors):
            self._debug_log("refresh_dashboard arrêt: base non prête.")
            return

        username = self._tracked_username()
        if not username:
            self._debug_log("refresh_dashboard arrêt: utilisateur vide.")
            self.status_text.set(self._connection_diagnostic())
            self._clear_dashboard("Aucun utilisateur configuré.")
            self._open_connection_if_missing()
            return

        if sync_before_refresh and self._has_valid_connection():
            if self.sync_in_progress:
                self._debug_log("refresh_dashboard: sync déjà en cours (sync_before_refresh).")
                self.status_text.set("Synchronisation en cours...")
                return
            self._debug_log("refresh_dashboard déclenche sync_now avant affichage.")
            self.sync_now(show_errors=show_errors)
            return

        try:
            dashboard = get_dashboard_data(self.db_path.get().strip(), username)
        except (sqlite3.Error, OSError, ValueError) as exc:
            diagnostic = self._format_diagnostic_error(exc)
            self._debug_log(f"refresh_dashboard erreur DB: {diagnostic}")
            self.status_text.set(diagnostic)
            self._clear_dashboard("Erreur locale: impossible de lire les données.")
            if show_errors:
                messagebox.showerror("Erreur de base locale", diagnostic)
            return
        latest = dashboard.get("latest")
        delta = dashboard.get("delta")

        if not latest:
            self._debug_log("refresh_dashboard: aucun snapshot local.")
            if self._has_valid_connection():
                emulator_live = self._is_emulator_process_live()
                if emulator_live and not show_errors:
                    self._debug_log("refresh_dashboard fallback API jeu en cours (émulateur déjà chargé).")
                    self.status_text.set("Aucune donnée locale. Détection du jeu en cours via l'API...")
                    self._update_current_game_tab(
                        {"games": [], "recent_achievements": []},
                        username,
                        force_refresh=(show_errors or force_current_game_refresh),
                    )
                    return
                if not emulator_live:
                    self._debug_log("refresh_dashboard fallback API dernier jeu (émulateur inactif).")
                    self.status_text.set("Aucune donnée locale. Récupération du dernier jeu via l'API...")
                    self._update_current_game_tab(
                        {"games": [], "recent_achievements": []},
                        username,
                        force_refresh=(show_errors or force_current_game_refresh),
                    )
                    return
            if show_errors:
                if self.sync_in_progress:
                    self._debug_log("refresh_dashboard: sync déjà en cours.")
                    self.status_text.set("Synchronisation en cours...")
                    return
                if self._has_valid_connection():
                    emulator_live = self._is_emulator_process_live()
                    if not emulator_live:
                        self._debug_log("refresh_dashboard fallback API dernier jeu (show_errors=True).")
                        self.status_text.set("Aucune donnée locale. Récupération du dernier jeu via l'API...")
                        self._update_current_game_tab(
                            {"games": [], "recent_achievements": []},
                            username,
                            force_refresh=(show_errors or force_current_game_refresh),
                        )
                        return
                    self.status_text.set("Aucune donnée locale. Synchronisation en cours...")
                    self._debug_log("refresh_dashboard déclenche sync_now.")
                    self.sync_now(show_errors=True)
                    return
                self.status_text.set(f"{self._connection_diagnostic()} Aucune donnée locale.")
                self._debug_log("refresh_dashboard: connexion API invalide, ouverture fenêtre connexion.")
                self.open_connection_window()
                return
            self._clear_dashboard("Aucune donnée locale. Lancez une synchronisation manuelle.")
            self._debug_log("refresh_dashboard: aucune donnée et mode silencieux.")
            return

        self.stat_points.set(self._with_delta(latest["total_points"], delta, "points"))
        self.stat_softcore.set(self._with_delta(latest["softcore_points"], delta, "softcore_points"))
        self.stat_true.set(self._with_delta(latest["true_points"], delta, "true_points"))
        self.stat_mastered.set(self._with_delta(latest["mastered_games"], delta, "mastered_games"))
        self.stat_beaten.set(self._with_delta(latest["beaten_games"], delta, "beaten_games"))
        self.stat_games.set(str(latest["total_games"]))
        self.stat_snapshot.set(str(latest["captured_at"]))

        self._clear_progress_recent_cache()
        self._update_current_game_tab(
            dashboard,
            username,
            force_refresh=(show_errors or force_current_game_refresh),
        )
        self._debug_log("refresh_dashboard: mise à jour jeu en cours lancée.")
        self.status_text.set(f"Données chargées pour {username}")

    # Method: _with_delta - Construit une valeur enrichie pour l'affichage.
    def _with_delta(self, current: int, delta: dict[str, int] | None, key: str) -> str:
        if not delta:
            return str(current)
        amount = int(delta.get(key, 0))
        sign = "+" if amount >= 0 else ""
        return f"{current} ({sign}{amount})"

    # Method: _fill_games_table - Alimente l'interface avec les données disponibles.
    def _fill_games_table(self, games: list[dict[str, object]]) -> None:
        if self.game_tree is None:
            return
        self.game_tree.delete(*self.game_tree.get_children())
        rows: list[tuple[object, ...]] = []
        for game in games:
            values = (
                game.get("title", ""),
                game.get("console_name", ""),
                f"{game.get('num_awarded_hardcore', 0)}/{game.get('max_possible', 0)}",
                game.get("completion_pct", 0),
                game.get("highest_award_kind", ""),
                self._format_datetime_display(game.get("most_recent_awarded_date", "")),
            )
            rows.append(values)
            self.game_tree.insert(
                "",
                END,
                values=values,
            )
        self._auto_fit_tree_columns(self.game_tree, rows)
        self._reapply_tree_sort(self.game_tree)

    # Method: _fill_recent_table - Alimente l'interface avec les données disponibles.
    def _fill_recent_table(self, items: list[dict[str, object]]) -> None:
        if self.recent_tree is None:
            return
        self.recent_tree.delete(*self.recent_tree.get_children())
        rows: list[tuple[object, ...]] = []
        for achievement in items:
            mode = "Hardcore" if int(achievement.get("unlocked_hardcore", 0)) else "Softcore"
            values = (
                achievement.get("game_title", ""),
                achievement.get("title", ""),
                achievement.get("points", 0),
                mode,
                self._format_datetime_display(achievement.get("unlocked_at", "")),
            )
            rows.append(values)
            self.recent_tree.insert(
                "",
                END,
                values=values,
            )
        self._auto_fit_tree_columns(self.recent_tree, rows)
        self._reapply_tree_sort(self.recent_tree)

    # Method: _clear_progress_recent_cache - Vide le cache visuel des onglets en maintenance.
    def _clear_progress_recent_cache(self) -> None:
        self._fill_games_table([])
        self._fill_recent_table([])
        if self.game_tree is not None:
            self._tree_sort_state.pop(str(self.game_tree), None)
            self._refresh_tree_headings(self.game_tree)
        if self.recent_tree is not None:
            self._tree_sort_state.pop(str(self.recent_tree), None)
            self._refresh_tree_headings(self.recent_tree)

    # Method: _clear_dashboard - Réinitialise les données ciblées.
    def _clear_dashboard(self, status: str) -> None:
        self.stat_points.set("-")
        self.stat_softcore.set("-")
        self.stat_true.set("-")
        self.stat_mastered.set("-")
        self.stat_beaten.set("-")
        self.stat_games.set("-")
        self.stat_snapshot.set("-")
        self._clear_progress_recent_cache()
        self._clear_current_game_details(status)
        self.status_text.set(status)

    # Method: show_about - Réalise le traitement lié à show about.
    def show_about(self) -> None:
        messagebox.showinfo(
            "À propos",
            "PyRA\nSuivi RetroAchievements\nApplication de bureau de suivi RetroAchievements.",
        )

    # Method: open_data_folder - Ouvre l'élément demandé.
    def open_data_folder(self) -> None:
        self._open_path(data_dir())

    # Method: open_db_folder - Ouvre l'élément demandé.
    def open_db_folder(self) -> None:
        raw_db = self.db_path.get().strip()
        if not raw_db:
            messagebox.showerror("Erreur", "Le chemin de la base est vide.")
            return
        db_parent = Path(raw_db).expanduser().resolve().parent
        db_parent.mkdir(parents=True, exist_ok=True)
        self._open_path(db_parent)

    # Method: _open_path - Ouvre l'élément demandé.
    def _open_path(self, path: Path) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror("Erreur", f"Impossible d'ouvrir {path}: {exc}")

    # Method: _on_save_shortcut - Traite l'événement correspondant.
    def _on_save_shortcut(self, _event: object) -> str:
        self.save_config()
        return "break"

    # Method: _on_connection_shortcut - Traite l'événement correspondant.
    def _on_connection_shortcut(self, _event: object) -> str:
        self.open_connection_window()
        return "break"

    # Method: _on_profile_shortcut - Traite l'événement correspondant.
    def _on_profile_shortcut(self, _event: object) -> str:
        self._on_profile_maintenance_request()
        return "break"

    # Method: _on_sync_shortcut - Traite l'événement correspondant.
    def _on_sync_shortcut(self, _event: object) -> str:
        self.sync_now()
        return "break"

    # Method: _on_refresh_shortcut - Traite l'événement correspondant.
    def _on_refresh_shortcut(self, _event: object) -> str:
        self.refresh_dashboard()
        return "break"

    # Method: _on_quit_shortcut - Traite l'événement correspondant.
    def _on_quit_shortcut(self, _event: object) -> str:
        self._on_app_close()
        return "break"





