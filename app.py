from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import base64
from pathlib import Path
from tkinter import END, HORIZONTAL, LEFT, VERTICAL, W, BooleanVar, Canvas, Menu, PhotoImage, StringVar, TclError, Tk, Toplevel, messagebox
from tkinter import font as tkfont
from tkinter import ttk

import requests

from retro_tracker.debug_logger import get_debug_logger
from retro_tracker.db import get_dashboard_data, init_db, save_snapshot
from retro_tracker.mixins import AchievementMixin, ParsingMixin
from retro_tracker.ra_api import RetroAPIError, RetroAchievementsClient


APP_NAME = "PyRA - RetroAchievements Tracker"
APP_VERSION = "0.9.0-beta.1"
THEME_MODES = {"light", "dark"}
AUTO_SYNC_INTERVAL_MS = 60_000
EVENT_SYNC_DELAY_MS = 550
EMULATOR_POLL_INTERVAL_MS = 2_500
EMULATOR_STATE_CONFIRMATION_COUNT = 2
EVENT_SYNC_LIVE_MIN_GAP_MS = 9_000
EVENT_SYNC_IDLE_MIN_GAP_MS = 25_000
CURRENT_GAME_LOADING_OVERLAY_MAX_MS = 25_000
IMAGE_FETCH_TIMEOUT_SECONDS = 6
MAX_ACHIEVEMENT_BADGE_FETCH = 28
ACHIEVEMENT_NA_VALUE = "N/A"
EMULATOR_STATUS_INACTIVE = "Inactif"
EMULATOR_STATUS_EMULATOR_LOADED = "Émulateur chargé"
EMULATOR_STATUS_GAME_LOADED = "Jeu chargé"
ACHIEVEMENT_SCROLL_INTERVAL_MS = 75
WINDOW_GEOMETRY_RE = re.compile(r"^\d+x\d+[+-]\d+[+-]\d+$")
EMULATOR_PROCESS_HINTS = (
    "retroarch",
    "pcsx2",
    "duckstation",
    "ppsspp",
    "dolphin",
    "flycast",
    "bizhawk",
    "emuhawk",
    "ralibretro",
    "rasnes9x",
    "ravba",
    "rap64",
    "ranes",
    "skyemu",
    "project64",
    "firelight",
)


# Function: data_dir - Retourne le dossier de données de l'application et le crée si nécessaire.
def data_dir() -> Path:
    base = Path(os.getenv("APPDATA", Path.home()))
    directory = base / "PyRA"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


# Function: config_path - Retourne le chemin du fichier de configuration.
def config_path() -> Path:
    return data_dir() / "config.json"


# Function: current_game_cache_path - Retourne le chemin du cache persistant du jeu en cours.
def current_game_cache_path() -> Path:
    return data_dir() / "current_game_cache.json"


# Function: debug_log_path_candidates - Retourne les emplacements possibles pour le fichier de journal de débogage.
def debug_log_path_candidates() -> list[Path]:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "debug.log")
    else:
        candidates.append(Path(__file__).resolve().parent / "debug.log")
    candidates.append(data_dir() / "debug.log")
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


# Class: TrackerApp - Orchestre l'interface, la synchronisation et les interactions utilisateur.
class TrackerApp(ParsingMixin, AchievementMixin):
    # Method: __init__ - Initialise l'objet et prépare son état interne.
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self._apply_window_icon()
        self.root.minsize(640, 420)

        self.api_key = StringVar()
        self.api_username = StringVar()
        self.tracked_username = StringVar()
        self.db_path = StringVar()
        self.status_text = StringVar(value="Prêt")
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

        self.sync_button: ttk.Button | None = None
        self.refresh_button: ttk.Button | None = None
        self.connection_button: ttk.Button | None = None
        self.profile_button: ttk.Button | None = None
        self.file_menu: Menu | None = None
        self.file_menu_profile_index: int | None = None
        self.summary_label: ttk.Label | None = None
        self.status_label: ttk.Label | None = None
        self.version_label: ttk.Label | None = None
        self.status_bar: ttk.Frame | None = None
        self.status_muted_reset_job: str | None = None
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
        self.current_game_info_tree: ttk.Treeview | None = None
        self.current_game_title_value_label: ttk.Label | None = None
        self.current_game_next_achievement_desc_label: ttk.Label | None = None
        self.current_game_next_achievement_button: ttk.Button | None = None
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
        self.current_game_achievement_tile_by_key: dict[str, ttk.Label] = {}
        self.current_game_achievement_data: list[dict[str, str]] = []
        self.current_game_locked_achievements: list[dict[str, str]] = []
        self.current_game_locked_achievement_index = 0
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
        self._tree_column_types: dict[str, dict[str, str]] = {}
        self._tree_headings: dict[str, dict[str, str]] = {}
        self._tree_sort_state: dict[str, tuple[str, bool]] = {}
        self._current_game_fetch_token = 0
        self.current_game_fetch_in_progress = False
        self.current_game_loading_timeout_job: str | None = None
        self.current_game_loading_hard_timeout_job: str | None = None
        self.persist_current_game_cache_on_inactive_transition = False
        self.pending_refresh_after_live_game_load = False
        self.prefer_persisted_current_game_on_startup = False
        self._current_game_last_key: tuple[str, int] | None = None
        self._current_game_details_cache: dict[tuple[str, int], dict[str, object]] = {}
        self._current_game_images_cache: dict[tuple[str, int], dict[str, bytes]] = {}
        self._image_bytes_cache: dict[str, bytes] = {}
        self._http_session = requests.Session()
        self.sync_in_progress = False
        self.auto_sync_job: str | None = None
        self.event_sync_job: str | None = None
        self._last_event_sync_request_monotonic = 0.0
        self.pending_event_sync_reason = ""
        self.startup_init_job: str | None = None
        self.startup_finish_job: str | None = None
        self.startup_connection_job: str | None = None
        self.emulator_poll_job: str | None = None
        self.emulator_probe_in_progress = False
        self._emulator_probe_candidate_live: bool | None = None
        self._emulator_probe_candidate_count = 0
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

        self._setup_debug_logger()

        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")
        self._apply_theme("light")

        self._build_menu()
        self._build_ui()
        self._load_config()
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.bind_all("<Motion>", self._on_global_pointer_motion, add="+")
        self.root.report_callback_exception = self._on_tk_callback_exception
        self.root.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self._prime_emulator_status_on_startup()
        self.refresh_dashboard(show_errors=False, sync_before_refresh=False)
        self._request_event_sync("démarrage", delay_ms=1_500)
        self._restart_emulator_probe(immediate=True)
        self.startup_connection_job = self.root.after(150, self._open_connection_if_missing)

    # Method: _show_startup_loader - Affiche une barre de chargement au démarrage de l'application.
    def _show_startup_loader(self) -> None:
        if self.startup_loader_frame is not None and self.startup_loader_frame.winfo_exists():
            return
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

    # Method: _run_startup_sequence - Exécute les étapes d'initialisation visibles par la barre de progression.
    def _run_startup_sequence(self) -> None:
        self.startup_init_job = None
        if self.is_closing:
            return
        self._set_startup_loader_progress(20, "Chargement des donnees locales...")
        self.refresh_dashboard(show_errors=False, sync_before_refresh=False)
        if self.is_closing:
            return
        self._set_startup_loader_progress(55, "Activation de la synchronisation par événement...")
        self._request_event_sync("démarrage", delay_ms=450)
        if self.is_closing:
            return
        self._set_startup_loader_progress(80, "Verification de l'emulateur...")
        self._prime_emulator_status_on_startup()
        self._restart_emulator_probe(immediate=True)
        if self.is_closing:
            return
        self._set_startup_loader_progress(100, "Initialisation terminee.")
        self.startup_finish_job = self.root.after(180, self._finish_startup_sequence)

    # Method: _finish_startup_sequence - Termine l'initialisation et ouvre la connexion si necessaire.
    def _finish_startup_sequence(self) -> None:
        self.startup_finish_job = None
        if self.is_closing:
            return
        self._hide_startup_loader()
        self.status_text.set("Pret")
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
            for icon_name in ("icon.ico", "app.ico", "PyRA.ico", "PyRA.generated.ico"):
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

    # Method: _process_matches_ra_emulator - Vérifie si le nom de processus correspond à un émulateur compatible RA.
    def _process_matches_ra_emulator(self, process_name: str) -> bool:
        normalized = process_name.strip().casefold()
        if not normalized:
            return False
        if normalized.endswith(".exe"):
            normalized = normalized[:-4]
        return any(hint in normalized for hint in EMULATOR_PROCESS_HINTS)

    # Method: _list_running_process_names - Récupère la liste des processus actifs via tasklist.
    def _list_running_process_names(self) -> list[str]:
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=3,
                check=False,
                creationflags=create_no_window,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0 or not result.stdout:
            return []

        names: list[str] = []
        reader = csv.reader(io.StringIO(result.stdout))
        for row in reader:
            if not row:
                continue
            name = row[0].strip()
            if name:
                names.append(name)
        return names

    # Method: _detect_ra_emulator_live - Détecte si un émulateur compatible RetroAchievements est en cours d'exécution.
    def _detect_ra_emulator_live(self) -> bool:
        process_names = self._list_running_process_names()
        for name in process_names:
            if self._process_matches_ra_emulator(name):
                return True
        return False

    # Method: _is_emulator_live_status_text - Indique si un libellé de statut correspond à un état Live.
    def _is_emulator_live_status_text(self, status_text: str) -> bool:
        normalized = status_text.strip().casefold()
        return normalized in {
            "live",  # Compatibilité ancienne valeur.
            EMULATOR_STATUS_EMULATOR_LOADED.casefold(),
            EMULATOR_STATUS_GAME_LOADED.casefold(),
        }

    # Method: _is_emulator_live - Détermine si l'état courant est Live à partir du libellé affiché.
    def _is_emulator_live(self) -> bool:
        return self._is_emulator_live_status_text(self.emulator_status_text.get())

    # Method: _set_emulator_status_text - Met à jour le libellé du statut sans déclencher de logique métier.
    def _set_emulator_status_text(self, status_text: str) -> None:
        self.emulator_status_text.set(status_text)
        self._refresh_emulator_status_tab()

    # Method: _prime_emulator_status_on_startup - Initialise l'état Live/Inactif dès l'ouverture de l'application.
    def _prime_emulator_status_on_startup(self) -> None:
        is_live = False
        try:
            is_live = self._detect_ra_emulator_live()
        except Exception:
            is_live = False
        next_status = EMULATOR_STATUS_EMULATOR_LOADED if is_live else EMULATOR_STATUS_INACTIVE
        self._set_emulator_status_text(next_status)
        self._emulator_probe_candidate_live = None
        self._emulator_probe_candidate_count = 0
        self._debug_log(f"_prime_emulator_status_on_startup status='{next_status}'")

    # Method: _set_emulator_status - Met à jour le statut Live/Inactif affiché près du sélecteur de thème.
    def _set_emulator_status(self, is_live: bool) -> None:
        previous = self.emulator_status_text.get().strip()
        next_status = EMULATOR_STATUS_EMULATOR_LOADED if is_live else EMULATOR_STATUS_INACTIVE
        self._set_emulator_status_text(next_status)
        if previous != next_status and not self.is_closing:
            self._debug_log(f"_set_emulator_status transition '{previous}' -> '{next_status}'")
            live_transition = (not self._is_emulator_live_status_text(previous)) and self._is_emulator_live_status_text(next_status)
            if live_transition:
                self.pending_refresh_after_live_game_load = True
            elif not self._is_emulator_live_status_text(next_status):
                self.pending_refresh_after_live_game_load = False
            if self._is_emulator_live_status_text(previous) and not self._is_emulator_live_status_text(next_status):
                self.persist_current_game_cache_on_inactive_transition = True
                self._debug_log("_set_emulator_status demande de persistance cache (Live -> Inactif).")
            self._set_status_message(f"État émulateur: {next_status}", muted=True)
            self.refresh_dashboard(
                show_errors=False,
                sync_before_refresh=False,
                force_current_game_refresh=live_transition,
            )

    # Method: _refresh_emulator_status_tab - Met à jour le pseudo-onglet de statut émulateur à droite.
    def _refresh_emulator_status_tab(self) -> None:
        if self.emulator_status_tab is None or self.emulator_status_label is None:
            return
        if not self.emulator_status_tab.winfo_exists() or not self.emulator_status_label.winfo_exists():
            return
        status = self.emulator_status_text.get().strip().casefold()
        if status == EMULATOR_STATUS_GAME_LOADED.casefold():
            style_name = "StatusTabGameLoaded.TLabel"
        elif status == EMULATOR_STATUS_EMULATOR_LOADED.casefold():
            style_name = "StatusTabEmulatorLoaded.TLabel"
        else:
            style_name = "StatusTabInactive.TLabel"
        try:
            self.emulator_status_label.configure(style=style_name)
        except TclError:
            return

    # Method: _restart_emulator_probe - Planifie la prochaine détection de l'émulateur.
    def _restart_emulator_probe(self, immediate: bool = False) -> None:
        if self.is_closing:
            return
        if self.emulator_poll_job is not None:
            try:
                self.root.after_cancel(self.emulator_poll_job)
            except TclError:
                pass
            self.emulator_poll_job = None
        delay = 600 if immediate else EMULATOR_POLL_INTERVAL_MS
        self.emulator_poll_job = self.root.after(delay, self._emulator_probe_tick)

    # Method: _emulator_probe_tick - Exécute un cycle de détection des émulateurs en arrière-plan.
    def _emulator_probe_tick(self) -> None:
        self.emulator_poll_job = None
        if self.is_closing:
            return
        if self.emulator_probe_in_progress:
            self._restart_emulator_probe(immediate=False)
            return
        self.emulator_probe_in_progress = True
        worker = threading.Thread(target=self._emulator_probe_worker, daemon=True)
        worker.start()

    # Method: _emulator_probe_worker - Lit les processus actifs et prépare le résultat Live/Inactif.
    def _emulator_probe_worker(self) -> None:
        is_live = False
        try:
            is_live = self._detect_ra_emulator_live()
        except Exception:
            is_live = False
        self._queue_ui_callback(lambda live=is_live: self._on_emulator_probe_result(live))

    # Method: _on_emulator_probe_result - Applique le résultat de détection et relance le polling.
    def _on_emulator_probe_result(self, is_live: bool) -> None:
        self.emulator_probe_in_progress = False
        current_live = self._is_emulator_live()
        if is_live == current_live:
            self._emulator_probe_candidate_live = None
            self._emulator_probe_candidate_count = 0
        else:
            if self._emulator_probe_candidate_live != is_live:
                self._emulator_probe_candidate_live = is_live
                self._emulator_probe_candidate_count = 1
            else:
                self._emulator_probe_candidate_count += 1
            if self._emulator_probe_candidate_count >= EMULATOR_STATE_CONFIRMATION_COUNT:
                self._emulator_probe_candidate_live = None
                self._emulator_probe_candidate_count = 0
                self._set_emulator_status(is_live)

        effective_live = self._is_emulator_live()
        if self._has_valid_connection():
            min_gap_ms = EVENT_SYNC_LIVE_MIN_GAP_MS if effective_live else EVENT_SYNC_IDLE_MIN_GAP_MS
            self._request_event_sync_throttled(
                "surveillance changements",
                delay_ms=120,
                min_gap_ms=min_gap_ms,
            )
        self._restart_emulator_probe(immediate=False)

    # Method: _build_menu - Construit les composants d'interface concernés.
    def _build_menu(self) -> None:
        menubar = Menu(self.root)

        file_menu = Menu(menubar, tearoff=0)
        self.file_menu = file_menu
        file_menu.add_command(
            label="Ouvrir la fenêtre de connexion",
            command=self.open_connection_window,
            accelerator="Ctrl+L",
        )
        file_menu.add_command(
            label="Ouvrir le profil",
            command=self._on_profile_maintenance_request,
            accelerator="Ctrl+P",
        )
        end_index = file_menu.index("end")
        self.file_menu_profile_index = int(end_index) if end_index is not None else None
        if self.file_menu_profile_index is not None:
            file_menu.entryconfigure(self.file_menu_profile_index, state="disabled")
        file_menu.bind("<<MenuSelect>>", self._on_file_menu_select)
        file_menu.bind("<Unmap>", self._on_file_menu_unmap)
        file_menu.add_command(
            label="Sauvegarder la configuration",
            command=self.save_config,
            accelerator="Ctrl+S",
        )
        file_menu.add_command(
            label="Effacer la connexion enregistrée",
            command=self.clear_saved_connection,
        )
        file_menu.add_separator()
        file_menu.add_command(label="Ouvrir le dossier des données", command=self.open_data_folder)
        file_menu.add_command(label="Ouvrir dossier de la base", command=self.open_db_folder)
        file_menu.add_separator()
        file_menu.add_command(
            label="Quitter",
            command=self._on_app_close,
            accelerator="Ctrl+Q",
        )
        menubar.add_cascade(label="Fichier", menu=file_menu)

        actions_menu = Menu(menubar, tearoff=0)
        actions_menu.add_command(
            label="Rafraîchir les données",
            command=self.refresh_dashboard,
            accelerator="F5",
        )
        menubar.add_cascade(label="Actions", menu=actions_menu)

        display_menu = Menu(menubar, tearoff=0)
        display_menu.add_checkbutton(label="Mode sombre", variable=self.dark_mode_enabled, command=self._on_theme_toggle)
        display_menu.add_command(label="Mode clair", command=lambda: self._set_theme("light"))
        menubar.add_cascade(label="Affichage", menu=display_menu)

        help_menu = Menu(menubar, tearoff=0)
        help_menu.add_command(label="À propos", command=self.show_about)
        menubar.add_cascade(label="Aide", menu=help_menu)

        self.root.config(menu=menubar)
        self.root.bind_all("<Control-s>", self._on_save_shortcut)
        self.root.bind_all("<Control-l>", self._on_connection_shortcut)
        self.root.bind_all("<Control-p>", self._on_profile_shortcut)
        self.root.bind_all("<Control-r>", self._on_sync_shortcut)
        self.root.bind_all("<F5>", self._on_refresh_shortcut)
        self.root.bind_all("<Control-q>", self._on_quit_shortcut)

    # Method: _build_ui - Construit les composants d'interface concernés.
    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self.top_bar = ttk.Frame(self.root)
        self.top_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=10)

        self.connection_button = ttk.Button(self.top_bar, text="Connexion", command=self.open_connection_window)
        self.connection_button.grid(row=0, column=0, padx=(0, 8), sticky=W)
        self.profile_button = ttk.Button(self.top_bar, text="Profil", command=self._on_profile_maintenance_request)
        self.profile_button.grid(row=0, column=1, padx=(0, 8), sticky=W)
        self.profile_button.state(["disabled"])
        self.profile_button.bind("<Enter>", self._on_profile_button_enter)
        self.profile_button.bind("<Motion>", self._on_profile_button_motion)
        self.profile_button.bind("<Leave>", self._on_profile_button_leave)
        self.summary_label = ttk.Label(self.top_bar, textvariable=self.connection_summary)
        self.summary_label.grid(row=0, column=2, padx=(6, 0), sticky=W)
        self.theme_toggle_frame = ttk.Frame(self.top_bar)
        self.theme_toggle_frame.grid(row=0, column=3, padx=(0, 0), sticky="e")
        self.theme_light_label = ttk.Label(self.theme_toggle_frame, text="Light", style="ThemeToggle.TLabel", cursor="hand2")
        self.theme_light_label.grid(row=0, column=0, sticky=W)
        self.theme_light_label.bind("<Button-1>", lambda _event: self._set_theme("light"))
        self.theme_separator_label = ttk.Label(self.theme_toggle_frame, text=" | ", style="ThemeToggleSep.TLabel")
        self.theme_separator_label.grid(row=0, column=1, sticky=W)
        self.theme_dark_label = ttk.Label(self.theme_toggle_frame, text="Dark", style="ThemeToggle.TLabel", cursor="hand2")
        self.theme_dark_label.grid(row=0, column=2, sticky=W)
        self.theme_dark_label.bind("<Button-1>", lambda _event: self._set_theme("dark"))

        content = ttk.Frame(self.root)
        content.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        tabs_row = ttk.Frame(content)
        tabs_row.grid(row=0, column=0, sticky="nsew")
        tabs_row.columnconfigure(0, weight=1)
        tabs_row.rowconfigure(0, weight=1)

        tabs = ttk.Notebook(tabs_row)
        tabs.grid(row=0, column=0, sticky="nsew")
        self.main_tabs = tabs
        tabs.bind("<Motion>", self._on_main_tabs_motion)
        tabs.bind("<Leave>", self._on_main_tabs_leave)
        games_tab = ttk.Frame(tabs)
        recent_tab = ttk.Frame(tabs)
        current_tab = ttk.Frame(tabs)
        games_tab.rowconfigure(0, weight=1)
        games_tab.columnconfigure(0, weight=1)
        recent_tab.rowconfigure(0, weight=1)
        recent_tab.columnconfigure(0, weight=1)
        current_tab.rowconfigure(0, weight=1)
        current_tab.columnconfigure(0, weight=1)
        tabs.add(current_tab, text="Jeu en cours")
        tabs.add(games_tab, text="Progression par jeu", state="disabled")
        tabs.add(recent_tab, text="Succès récents", state="disabled")
        tabs.select(current_tab)
        status_tab = ttk.Frame(tabs_row, style="StatusTab.TFrame", padding=(12, 6, 12, 5))
        status_tab.place(in_=tabs_row, relx=1.0, x=0, y=0, anchor="ne")
        status_tab.lift()
        self.emulator_status_tab = status_tab
        self.emulator_status_label = ttk.Label(
            status_tab,
            textvariable=self.emulator_status_text,
            style="StatusTabInactive.TLabel",
            anchor="center",
            justify="center",
        )
        self.emulator_status_label.grid(row=0, column=0, sticky="nsew")
        status_tab.bind("<Button-1>", lambda _event: "break")
        self.emulator_status_label.bind("<Button-1>", lambda _event: "break")
        self.game_tree = self._build_games_table(games_tab)
        self.recent_tree = self._build_recent_table(recent_tab)
        self._build_current_game_tab(current_tab)
        self._refresh_emulator_status_tab()

        self.status_bar = ttk.Frame(self.root)
        self.status_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.status_bar.columnconfigure(0, weight=1)
        self.status_bar.columnconfigure(1, weight=0)
        self.status_label = ttk.Label(self.status_bar, textvariable=self.status_text, style="StatusDefault.TLabel")
        self.status_label.grid(row=0, column=0, sticky="w")
        self.version_label = ttk.Label(self.status_bar, text=f"v{APP_VERSION}")
        self.version_label.grid(row=0, column=1, sticky="e")
        self._apply_responsive_layout(self.root.winfo_width())

    # Method: _build_games_table - Construit les composants d'interface concernés.
    def _build_games_table(self, parent: ttk.Frame) -> ttk.Treeview:
        container = ttk.Frame(parent)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        columns = ("title", "console", "hardcore", "pct", "status", "updated")
        table = ttk.Treeview(container, columns=columns, show="headings")
        headings = {
            "title": "Jeu",
            "console": "Console",
            "hardcore": "Hardcore",
            "pct": "%",
            "status": "Statut",
            "updated": "Dernier succès",
        }
        widths = {"title": 360, "console": 140, "hardcore": 100, "pct": 60, "status": 140, "updated": 170}
        for col in columns:
            table.heading(
                col,
                text=headings[col],
                command=lambda c=col, t=table: self._on_tree_heading_click(t, c),
            )
            table.column(col, width=widths[col], anchor=W, stretch=False)

        y_scroll = ttk.Scrollbar(container, orient=VERTICAL, command=table.yview)
        x_scroll = ttk.Scrollbar(container, orient=HORIZONTAL, command=table.xview)
        table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        table.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self._register_sortable_tree(
            table,
            headings=headings,
            column_types={
                "title": "text",
                "console": "text",
                "hardcore": "fraction",
                "pct": "float",
                "status": "text",
                "updated": "date",
            },
        )
        self._auto_fit_tree_columns(table, [])
        return table

    # Method: _build_recent_table - Construit les composants d'interface concernés.
    def _build_recent_table(self, parent: ttk.Frame) -> ttk.Treeview:
        container = ttk.Frame(parent)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        columns = ("game", "title", "points", "mode", "date")
        table = ttk.Treeview(container, columns=columns, show="headings")
        headings = {
            "game": "Jeu",
            "title": "Succès",
            "points": "Points",
            "mode": "Mode",
            "date": "Date",
        }
        widths = {"game": 300, "title": 280, "points": 70, "mode": 90, "date": 170}
        for col in columns:
            table.heading(
                col,
                text=headings[col],
                command=lambda c=col, t=table: self._on_tree_heading_click(t, c),
            )
            table.column(col, width=widths[col], anchor=W, stretch=False)

        y_scroll = ttk.Scrollbar(container, orient=VERTICAL, command=table.yview)
        x_scroll = ttk.Scrollbar(container, orient=HORIZONTAL, command=table.xview)
        table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        table.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self._register_sortable_tree(
            table,
            headings=headings,
            column_types={
                "game": "text",
                "title": "text",
                "points": "int",
                "mode": "text",
                "date": "date",
            },
        )
        self._auto_fit_tree_columns(table, [])
        return table

    # Method: _build_current_game_tab - Construit les composants d'interface concernés.
    def _build_current_game_tab(self, parent: ttk.Frame) -> None:
        container = ttk.Frame(parent)
        self.current_game_tab_container = container
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(2, weight=1)

        summary = ttk.LabelFrame(container, text="  Résumé du jeu en cours")
        summary.grid(row=0, column=0, sticky="ew", pady=(8, 8))
        summary.columnconfigure(0, weight=0)
        summary.columnconfigure(1, weight=0)
        summary.columnconfigure(2, weight=1)
        summary_fields = [
            ("Jeu", self.current_game_title),
            ("Console", self.current_game_console),
            ("Progression", self.current_game_progress),
            ("Dernier succès", self.current_game_last_unlock),
        ]
        cover = ttk.Frame(summary, padding=(8, 0, 10, 0))
        cover.grid(row=0, column=0, rowspan=len(summary_fields), sticky="nw")
        cover.columnconfigure(0, weight=1)
        cover_label = ttk.Label(cover, text="Image indisponible", anchor="center", justify="center")
        cover_label.grid(row=0, column=0, sticky="nsew")
        self.current_game_image_labels = {"boxart": cover_label}

        for row, (label, var) in enumerate(summary_fields):
            ttk.Label(summary, text=f"{label} :").grid(row=row, column=1, sticky=W, padx=(8, 6), pady=2)
            if label == "Jeu":
                title_label = ttk.Label(
                    summary,
                    textvariable=var,
                    style="CurrentGameTitle.TLabel",
                    justify="left",
                    anchor="w",
                    wraplength=520,
                )
                title_label.grid(row=row, column=2, sticky="ew", padx=(0, 8), pady=2)
                self.current_game_title_value_label = title_label
            else:
                ttk.Label(summary, textvariable=var).grid(row=row, column=2, sticky=W, padx=(0, 8), pady=2)

        next_achievement = ttk.LabelFrame(container, text="  Premier succès non débloqué")
        next_achievement.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        next_achievement.columnconfigure(1, weight=1)
        next_achievement.rowconfigure(0, weight=1)

        next_badge_frame = ttk.Frame(next_achievement, padding=(8, 8, 8, 8))
        next_badge_frame.grid(row=0, column=0, sticky="nw")
        next_badge_label = ttk.Label(next_badge_frame, text="Image indisponible", anchor="center", justify="center")
        next_badge_label.grid(row=0, column=0, sticky="nsew")
        self.current_game_image_labels["next_badge"] = next_badge_label

        next_info = ttk.Frame(next_achievement, padding=(0, 8, 8, 8))
        next_info.grid(row=0, column=1, sticky="nsew")
        next_info.columnconfigure(1, weight=1)
        ttk.Label(next_info, text="Nom :").grid(row=0, column=0, sticky=W, padx=(4, 6), pady=2)
        ttk.Label(next_info, textvariable=self.current_game_next_achievement_title).grid(
            row=0, column=1, sticky=W, padx=(0, 4), pady=2
        )
        ttk.Label(next_info, text="Description :").grid(row=1, column=0, sticky=W, padx=(4, 6), pady=2)
        desc_label = ttk.Label(
            next_info,
            textvariable=self.current_game_next_achievement_description,
            justify="left",
            anchor="w",
            wraplength=560,
        )
        desc_label.grid(row=1, column=1, sticky="ew", padx=(0, 4), pady=2)
        self.current_game_next_achievement_desc_label = desc_label
        ttk.Label(next_info, text="Points / ratio :").grid(row=2, column=0, sticky=W, padx=(4, 6), pady=2)
        ttk.Label(next_info, textvariable=self.current_game_next_achievement_points).grid(
            row=2, column=1, sticky=W, padx=(0, 4), pady=2
        )
        ttk.Label(next_info, text="Déblocages :").grid(row=3, column=0, sticky=W, padx=(4, 6), pady=2)
        ttk.Label(next_info, textvariable=self.current_game_next_achievement_unlocks).grid(
            row=3, column=1, sticky=W, padx=(0, 4), pady=2
        )
        ttk.Label(next_info, text="Faisabilité :").grid(row=4, column=0, sticky=W, padx=(4, 6), pady=2)
        ttk.Label(next_info, textvariable=self.current_game_next_achievement_feasibility).grid(
            row=4, column=1, sticky=W, padx=(0, 4), pady=2
        )
        next_button = ttk.Button(
            next_achievement,
            text="Passer au suivant",
            command=self._show_next_locked_achievement,
            state="disabled",
        )
        next_button.grid(row=1, column=1, sticky="e", padx=(0, 10), pady=(0, 8))
        self.current_game_next_achievement_button = next_button

        all_achievements = ttk.LabelFrame(container, text="  Tous les succès du jeu en cours")
        all_achievements.grid(row=2, column=0, sticky="nsew")
        all_achievements.columnconfigure(0, weight=1)
        all_achievements.rowconfigure(1, weight=1)
        ttk.Label(all_achievements, textvariable=self.current_game_achievements_note).grid(
            row=0, column=0, sticky="w", padx=8, pady=(6, 2)
        )

        gallery_host = ttk.Frame(all_achievements)
        gallery_host.grid(row=1, column=0, sticky="nsew", padx=8, pady=(2, 8))
        gallery_host.columnconfigure(0, weight=1)
        gallery_host.rowconfigure(0, weight=1)

        canvas = Canvas(
            gallery_host,
            highlightthickness=0,
            bd=0,
            yscrollincrement=1,
            bg=self.theme_colors.get("root_bg", "#f3f5f8"),
        )
        canvas.grid(row=0, column=0, sticky="nsew")

        inner = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", self._on_current_game_gallery_canvas_configure)

        self.current_game_achievements_canvas = canvas
        self.current_game_achievements_inner = inner
        self.current_game_achievements_window_id = window_id
        self._clear_current_game_details("Aucun jeu en cours détecté.")

    # Method: _font_from_style - Réalise le traitement lié à font from style.
    def _font_from_style(self, style_name: str, option: str, fallback: str) -> tkfont.Font:
        font_value = self.style.lookup(style_name, option)
        if font_value:
            try:
                return tkfont.nametofont(font_value)
            except TclError:
                try:
                    return tkfont.Font(font=font_value)
                except TclError:
                    pass
        return tkfont.nametofont(fallback)

    # Method: _auto_fit_tree_columns - Exécute un traitement automatique planifié.
    def _auto_fit_tree_columns(self, tree: ttk.Treeview, rows: list[tuple[object, ...]]) -> None:
        columns = list(tree["columns"])
        if not columns:
            return

        body_font = self._font_from_style("Treeview", "font", "TkDefaultFont")
        heading_font = self._font_from_style("Treeview.Heading", "font", "TkHeadingFont")
        sample = rows[:300]
        min_width = 64
        max_width = 900
        padding = 28

        for idx, column in enumerate(columns):
            heading = str(tree.heading(column, "text") or "")
            best = heading_font.measure(heading) + padding
            for row in sample:
                if idx >= len(row):
                    continue
                value = "" if row[idx] is None else str(row[idx])
                best = max(best, body_font.measure(value) + padding)
            width = max(min_width, min(max_width, best))
            tree.column(column, width=width, stretch=False)

    # Method: _register_sortable_tree - Réalise le traitement lié à register sortable tree.
    def _register_sortable_tree(
        self,
        tree: ttk.Treeview,
        headings: dict[str, str],
        column_types: dict[str, str],
    ) -> None:
        key = str(tree)
        self._tree_headings[key] = dict(headings)
        self._tree_column_types[key] = dict(column_types)
        self._tree_sort_state.pop(key, None)
        self._refresh_tree_headings(tree)

    # Method: _refresh_tree_headings - Met à jour l'affichage ou l'état courant.
    def _refresh_tree_headings(self, tree: ttk.Treeview) -> None:
        key = str(tree)
        headings = self._tree_headings.get(key, {})
        active = self._tree_sort_state.get(key)
        for column, base_text in headings.items():
            suffix = ""
            if active is not None and active[0] == column:
                suffix = " ↑" if active[1] else " ↓"
            tree.heading(
                column,
                text=f"{base_text}{suffix}",
                command=lambda c=column, t=tree: self._on_tree_heading_click(t, c),
            )

    # Method: _on_tree_heading_click - Traite l'événement correspondant.
    def _on_tree_heading_click(self, tree: ttk.Treeview, column: str) -> None:
        key = str(tree)
        current = self._tree_sort_state.get(key)
        ascending = True
        if current is not None and current[0] == column:
            ascending = not current[1]
        self._tree_sort_state[key] = (column, ascending)
        self._sort_treeview(tree, column, ascending)
        self._refresh_tree_headings(tree)

    # Method: _sort_treeview - Réalise le traitement lié à sort treeview.
    def _sort_treeview(self, tree: ttk.Treeview, column: str, ascending: bool) -> None:
        items = list(tree.get_children(""))
        if not items:
            return

        present: list[tuple[object, str]] = []
        missing: list[str] = []
        for item_id in items:
            is_missing, sort_value = self._coerce_sort_value(tree, column, tree.set(item_id, column))
            if is_missing:
                missing.append(item_id)
            else:
                present.append((sort_value, item_id))

        present.sort(key=lambda row: row[0], reverse=not ascending)
        ordered_items = [item_id for _, item_id in present] + missing
        for index, item_id in enumerate(ordered_items):
            tree.move(item_id, "", index)

    # Method: _coerce_sort_value - Convertit la valeur vers le type attendu.
    def _coerce_sort_value(self, tree: ttk.Treeview, column: str, value: object) -> tuple[bool, object]:
        raw = "" if value is None else str(value).strip()
        if not raw:
            return True, ""

        key = str(tree)
        column_type = self._tree_column_types.get(key, {}).get(column, "text")

        if column_type == "int":
            cleaned = re.sub(r"[^0-9\-]", "", raw)
            if cleaned not in {"", "-"}:
                return False, int(cleaned)
            return False, raw.casefold()

        if column_type == "float":
            cleaned = re.sub(r"[^0-9,\.\-]", "", raw).replace(",", ".")
            if cleaned not in {"", "-", ".", "-."}:
                return False, float(cleaned)
            return False, raw.casefold()

        if column_type == "fraction":
            match = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", raw)
            if match is not None:
                numerator = int(match.group(1))
                denominator = int(match.group(2))
                ratio = (numerator / denominator) if denominator > 0 else 0.0
                return False, (ratio, numerator, denominator)
            return False, raw.casefold()

        if column_type == "date":
            parsed = self._parse_sort_datetime(raw)
            if parsed is not None:
                return False, parsed.timestamp()
            return False, raw.casefold()

        return False, raw.casefold()

    # Method: _reapply_tree_sort - Réalise le traitement lié à reapply tree sort.
    def _reapply_tree_sort(self, tree: ttk.Treeview | None) -> None:
        if tree is None:
            return
        state = self._tree_sort_state.get(str(tree))
        if state is None:
            self._refresh_tree_headings(tree)
            return
        self._sort_treeview(tree, state[0], state[1])
        self._refresh_tree_headings(tree)

    # Method: _clear_current_game_details - Réinitialise les données ciblées.
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
        self._refresh_next_achievement_button_state()
        self._current_game_last_key = None
        self.prefer_persisted_current_game_on_startup = False
        self._current_game_fetch_token += 1
        self.current_game_fetch_in_progress = False
        if self.current_game_info_tree is not None:
            self.current_game_info_tree.delete(*self.current_game_info_tree.get_children())
        self.current_game_achievement_data = []
        self.current_game_active_images = {}
        self._clear_current_game_achievement_gallery()
        self._hide_current_game_achievement_tooltip()
        self.current_game_image_refs = {}
        for label in self.current_game_image_labels.values():
            label.configure(image="", text="Image indisponible")
        self._hide_current_game_loading_overlay()

    # Method: _show_current_game_loading_overlay - Affiche une barre de chargement avant les informations du jeu en cours.
    def _show_current_game_loading_overlay(self, message: str = "Chargement des infos du jeu en cours...") -> None:
        host = self.current_game_tab_container
        if host is None or not host.winfo_exists():
            self._debug_log("_show_current_game_loading_overlay ignoré: conteneur indisponible.")
            return
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
        self.current_game_note.set("Chargement trop long: affichage des données disponibles.")
        self._hide_current_game_loading_overlay()

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
        note = self.current_game_note.get().strip()
        if not note or note == "-" or "Détection du jeu en cours" in note:
            self.current_game_note.set("Chargement interrompu: affichage partiel (timeout sécurité).")
        self._hide_current_game_loading_overlay()

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
        self._cancel_current_game_loading_timeout()
        self._cancel_current_game_loading_hard_timeout()
        if self.current_game_loading_progress is not None and self.current_game_loading_progress.winfo_exists():
            self.current_game_loading_progress.stop()
        overlay = self.current_game_loading_overlay
        if overlay is None or not overlay.winfo_exists():
            return
        overlay.place_forget()

    # Method: _finalize_current_game_loading_overlay - Termine le cycle de chargement après le rendu complet des widgets.
    def _finalize_current_game_loading_overlay(self) -> None:
        try:
            self.root.update_idletasks()
        except TclError:
            self._hide_current_game_loading_overlay()
            return
        self.root.after_idle(self._hide_current_game_loading_overlay)

    # Method: _are_current_game_achievement_tiles_rendered - Vérifie que chaque tuile de succès est effectivement rendue.
    def _are_current_game_achievement_tiles_rendered(self) -> bool:
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

    # Method: _finalize_current_game_loading_overlay_after_gallery - Attend le rendu des images de succès avant de masquer l'overlay.
    def _finalize_current_game_loading_overlay_after_gallery(self, remaining_checks: int = 36) -> None:
        try:
            self.root.update_idletasks()
        except TclError:
            self._hide_current_game_loading_overlay()
            return
        if self._are_current_game_achievement_tiles_rendered():
            self._hide_current_game_loading_overlay()
            return
        if remaining_checks <= 0:
            self._hide_current_game_loading_overlay()
            return
        self.root.after(
            35,
            lambda: self._finalize_current_game_loading_overlay_after_gallery(remaining_checks - 1),
        )

    # Method: _source_label_style - Détermine le style à appliquer selon la source détectée.
    def _source_label_style(self, source_value: str) -> str:
        lowered = source_value.strip().lower()
        if lowered.startswith("live"):
            return "CurrentSourceLive.TLabel"
        if lowered.startswith("fallback"):
            return "CurrentSourceFallback.TLabel"
        return "CurrentSourceUnknown.TLabel"

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
        self._sync_locked_achievement_navigation(achievements, next_achievement)
        return True

    # Method: _clear_current_game_achievement_gallery - Réinitialise la galerie de succès du jeu courant.
    def _clear_current_game_achievement_gallery(self) -> None:
        self._stop_current_game_achievement_auto_scroll()
        self.current_game_achievement_refs = {}
        self.current_game_achievement_tiles = []
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
        try:
            translated = self._translate_achievement_description_to_french(description)
            wrapped = self._format_tooltip_description_three_lines(translated)
            result = f"{title_line.strip()}\n{wrapped}"
        except Exception:
            result = normalized
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
        self._show_profile_maintenance_tooltip("en maintenance")

    # Method: _on_profile_button_motion - Déplace l'infobulle pendant le survol du bouton Profil.
    def _on_profile_button_motion(self, _event: object) -> None:
        self._show_profile_maintenance_tooltip("en maintenance")

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
            self._show_profile_maintenance_tooltip("en maintenance")
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
            self._show_profile_maintenance_tooltip("en maintenance")
            return
        self._hide_profile_maintenance_tooltip()

    # Method: _on_main_tabs_motion - Affiche "En maintenance" au survol des onglets désactivés.
    def _on_main_tabs_motion(self, event: object) -> None:
        tabs = self.main_tabs
        if tabs is None or not tabs.winfo_exists():
            return
        x = int(getattr(event, "x", -1))
        y = int(getattr(event, "y", -1))
        if x < 0 or y < 0:
            self._hide_maintenance_tab_tooltip()
            return
        try:
            tab_index = tabs.index(f"@{x},{y}")
            tab_state = str(tabs.tab(tab_index, "state"))
            tab_text = str(tabs.tab(tab_index, "text"))
        except TclError:
            self._hide_maintenance_tab_tooltip()
            return
        if tab_state == "disabled" and tab_text in {"Progression par jeu", "Succès récents"}:
            self._show_maintenance_tab_tooltip("En maintenance")
            return
        self._hide_maintenance_tab_tooltip()

    # Method: _on_main_tabs_leave - Masque l'infobulle quand le pointeur quitte la zone des onglets.
    def _on_main_tabs_leave(self, _event: object) -> None:
        self._hide_maintenance_tab_tooltip()

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
        description = self._translate_achievement_description_cached_only(description)
        self.current_game_next_achievement_title.set(next_achievement.get("title", ACHIEVEMENT_NA_VALUE))
        self.current_game_next_achievement_description.set(description or ACHIEVEMENT_NA_VALUE)
        self.current_game_next_achievement_points.set(next_achievement.get("points", ACHIEVEMENT_NA_VALUE))
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

    # Method: _extract_locked_achievements - Extrait les succès non débloqués pour la navigation "Passer au suivant".
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

    # Method: _refresh_next_achievement_button_state - Active/désactive le bouton selon le nombre de succès verrouillés.
    def _refresh_next_achievement_button_state(self) -> None:
        button = self.current_game_next_achievement_button
        if button is None or not button.winfo_exists():
            return
        if len(self.current_game_locked_achievements) > 1:
            button.state(["!disabled"])
        else:
            button.state(["disabled"])

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
        scale = max((image.width() + 107) // 108, (image.height() + 107) // 108)
        if scale > 1:
            image = image.subsample(scale, scale)
        self.current_game_image_refs["next_badge"] = image
        label.configure(image=image, text="")

    # Method: _apply_locked_achievement_index - Applique l'achievement verrouillé sélectionné dans la section dédiée.
    def _apply_locked_achievement_index(self) -> None:
        if not self.current_game_locked_achievements:
            self._refresh_next_achievement_button_state()
            return
        size = len(self.current_game_locked_achievements)
        self.current_game_locked_achievement_index %= size
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
        self._refresh_next_achievement_button_state()

    # Method: _sync_locked_achievement_navigation - Synchronise l'état du bouton avec les succès non débloqués du jeu courant.
    def _sync_locked_achievement_navigation(
        self,
        achievements: list[dict[str, str]],
        preferred_next_achievement: dict[str, str] | None = None,
    ) -> None:
        self.current_game_locked_achievements = self._extract_locked_achievements(achievements)
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

    # Method: _show_next_locked_achievement - Affiche le succès non débloqué suivant dans la section dédiée.
    def _show_next_locked_achievement(self) -> None:
        if len(self.current_game_locked_achievements) <= 1:
            self._refresh_next_achievement_button_state()
            return
        self.current_game_locked_achievement_index = (
            self.current_game_locked_achievement_index + 1
        ) % len(self.current_game_locked_achievements)
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
            self.current_game_locked_achievement_index %= size
            selected = self.current_game_locked_achievements[self.current_game_locked_achievement_index]
            self._set_current_game_next_badge_from_image_key(selected.get("image_key", ""))

    # Method: _set_current_game_achievement_gallery - Alimente la galerie des succès avec images + infobulles.
    def _set_current_game_achievement_gallery(self, achievements: list[dict[str, str]], images: dict[str, bytes]) -> None:
        self.current_game_achievement_data = achievements
        self._clear_current_game_achievement_gallery()
        if self.current_game_achievements_inner is None:
            return
        if not achievements:
            self.current_game_locked_achievements = []
            self.current_game_locked_achievement_index = 0
            self._refresh_next_achievement_button_state()
            self.current_game_achievements_note.set("Aucun succès disponible pour ce jeu.")
            return

        self.current_game_achievements_note.set(
            f"{len(achievements)} succès (survolez une image pour voir le nom et la description)."
        )
        for index, achievement in enumerate(achievements):
            image_key = achievement.get("image_key", "")
            tooltip_text = achievement.get("tooltip", "").strip()
            label = ttk.Label(self.current_game_achievements_inner, text="N/A", anchor="center", justify="center")
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
        self._start_missing_achievement_badges_loader()

    # Method: _fetch_image_bytes - Télécharge une image distante avec cache mémoire.
    def _fetch_image_bytes(self, url: str) -> bytes | None:
        normalized = url.strip()
        if not normalized:
            return None
        cached = self._image_bytes_cache.get(normalized)
        if cached is not None:
            return cached
        try:
            response = self._http_session.get(normalized, timeout=IMAGE_FETCH_TIMEOUT_SECONDS)
            if response.status_code != 200 or not response.content:
                return None
        except requests.RequestException:
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
            max_width, max_height = (320, 180) if key == "boxart" else (108, 108)
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

    # Method: _extract_live_current_game - Extrait le jeu en cours depuis le résumé API.
    def _extract_live_current_game(self, summary: dict[str, object]) -> tuple[int, str, str, bool]:
        rich_presence = ""
        for field in ("RichPresenceMsg", "RichPresence", "RichPresenceMessage"):
            value = self._safe_text(summary.get(field))
            if value:
                rich_presence = value
                break

        online = self._safe_bool(summary.get("IsOnline")) or self._safe_bool(summary.get("IsOnine"))
        if not online and not rich_presence:
            return 0, "", rich_presence, online

        recent = summary.get("RecentlyPlayed")
        if isinstance(recent, list):
            for item in recent:
                if not isinstance(item, dict):
                    continue
                game_id = self._safe_int(item.get("GameID") or item.get("ID"))
                if game_id <= 0:
                    continue
                title = self._extract_title_text(item.get("Title") or item.get("GameTitle") or item)
                return game_id, title, rich_presence, online

        direct_pairs = (
            ("GameID", "GameTitle"),
            ("MostRecentGameID", "MostRecentGameTitle"),
            ("LastGameID", "LastGame"),
        )
        for game_id_field, title_field in direct_pairs:
            game_id = self._safe_int(summary.get(game_id_field))
            if game_id > 0:
                return game_id, self._extract_title_text(summary.get(title_field)), rich_presence, online

        return 0, "", rich_presence, online

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

        emulator_live = self._is_emulator_live()
        if emulator_live:
            self.prefer_persisted_current_game_on_startup = False
        fallback_game_id, fallback_title = self._pick_current_game(dashboard, prefer_last_played=not emulator_live)
        fallback_key = (username, fallback_game_id)
        same_fallback = (not force_refresh) and fallback_game_id > 0 and self._current_game_last_key == fallback_key
        current_key = self._current_game_last_key
        startup_cache_preferred = (
            (not emulator_live)
            and self.prefer_persisted_current_game_on_startup
            and retained_game_id > 0
            and not force_refresh
        )
        allow_fallback_preview = (
            (not emulator_live)
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
                source="Dernier jeu joué (local)" if not emulator_live else "Fallback local",
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
                if emulator_live:
                    self.current_game_note.set("Jeu estimé localement (clé API manquante pour la détection en direct).")
                else:
                    self.current_game_note.set("Émulateur inactif: dernier jeu joué affiché depuis les données locales.")
            else:
                self.current_game_note.set("Clé API manquante pour détecter le jeu en direct ou le dernier jeu joué.")
            self._hide_current_game_loading_overlay()
            return

        if self.current_game_fetch_in_progress and not force_refresh:
            self._debug_log("_update_current_game_tab ignoré: récupération déjà en cours.")
            self._hide_current_game_loading_overlay()
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
        fetch_token: int,
        force_refresh: bool = False,
        retained_game_id: int = 0,
    ) -> None:
        self._debug_log(
            f"_fetch_current_game_worker start token={fetch_token} user='{username}' "
            f"fallback_game_id={fallback_game_id} emulator_live={emulator_live} force_refresh={force_refresh} "
            f"retained_game_id={retained_game_id}"
        )
        game_id = fallback_game_id
        title_hint = fallback_title
        source_label = "Dernier jeu joué (local)" if not emulator_live else "Fallback local"
        rich_presence = ""
        summary_payload: dict[str, object] = {}
        game_payload: dict[str, object] = {}
        images: dict[str, bytes] = {}
        next_achievement: dict[str, str] | None = None
        achievement_rows: list[dict[str, str]] = []
        error: str | None = None
        diagnostic_error: str | None = None
        startup_cache_preferred = False

        client = RetroAchievementsClient(api_key)
        try:
            summary = client.get_user_summary(username, include_recent_games=True)
            summary_payload = summary
            live_game_id, live_title, rich_presence, is_online = self._extract_live_current_game(summary)
            last_played_id, last_played_title = self._extract_last_played_game(summary)
            self._debug_log(
                f"_fetch_current_game_worker summary live_game_id={live_game_id} last_played_id={last_played_id} "
                f"is_online={is_online} rich_presence={'yes' if bool(rich_presence) else 'no'}"
            )

            startup_cache_preferred = (
                (not emulator_live)
                and self.prefer_persisted_current_game_on_startup
                and retained_game_id > 0
                and not force_refresh
            )

            if startup_cache_preferred:
                game_id = retained_game_id
                source_label = "Cache sauvegarde"
            elif emulator_live and live_game_id > 0:
                game_id = live_game_id
                if live_title:
                    title_hint = live_title
                source_label = "Live émulateur" if (is_online or rich_presence) else "Live API"
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
                source_label = "Dernier jeu joué (local)" if not emulator_live else "Fallback local"
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

        if game_id <= 0 and retained_game_id > 0:
            if emulator_live:
                keep_note = "Détection Live indisponible: dernier jeu conservé."
            else:
                keep_note = "Émulateur inactif: dernier jeu conservé."
            self._debug_log(
                f"_fetch_current_game_worker conservation dernier jeu affiché token={fetch_token} "
                f"retained_game_id={retained_game_id} emulator_live={emulator_live}"
            )
            self._queue_ui_callback(
                lambda note=keep_note, diag=diagnostic_error: self._on_current_game_unchanged(
                    fetch_token=fetch_token,
                    note=note,
                    source_value="",
                    diagnostic_error=diag,
                )
            )
            return

        if game_id <= 0:
            self._debug_log(f"_fetch_current_game_worker aucun jeu détecté token={fetch_token} source='{source_label}'")
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
                    note="Aucun jeu en cours ou dernier jeu joué détecté.",
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
                tooltip = self._build_achievement_tooltip(achievement)
                is_unlocked = self._is_achievement_unlocked(achievement)
                badge_url = self._achievement_badge_url(achievement)
                locked_badge_url = self._locked_badge_url(badge_url) if badge_url else ""
                summary = self._build_next_achievement_summary(
                    achievement,
                    total_players=total_players,
                    translate_description=False,
                )
                achievement_rows.append(
                    {
                        "image_key": image_key,
                        "tooltip": tooltip,
                        "is_unlocked": "1" if is_unlocked else "0",
                        "badge_url": badge_url,
                        "badge_url_locked": locked_badge_url,
                        "next_title": summary.get("title", ACHIEVEMENT_NA_VALUE),
                        "next_description": summary.get("description", ACHIEVEMENT_NA_VALUE),
                        "next_points": summary.get("points", ACHIEVEMENT_NA_VALUE),
                        "next_unlocks": summary.get("unlocks", ACHIEVEMENT_NA_VALUE),
                        "next_feasibility": summary.get("feasibility", ACHIEVEMENT_NA_VALUE),
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
        if source_label.startswith("Live"):
            note = "Jeu détecté en direct."
        elif source_label.startswith("Dernier jeu joué"):
            note = "Émulateur inactif: affichage du dernier jeu joué."
        else:
            note = "Détails chargés."
        self._debug_log(
            f"_fetch_current_game_worker fin token={fetch_token} game_id={game_id} "
            f"source='{source_label}' note='{note}' error={'yes' if bool(error) else 'no'}"
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
        self._debug_log(
            f"_on_current_game_unchanged token={fetch_token} source='{source_value}' note='{note}'"
        )
        if source_value.strip():
            self._set_current_game_source(source_value)
        effective_source = source_value or self.current_game_source.get()
        self._sync_emulator_status_after_current_game_update(effective_source)
        if diagnostic_error:
            self.status_text.set(diagnostic_error)
        self.current_game_note.set(note)
        self._persist_current_game_cache_after_inactive_transition_if_needed(
            source_value or self.current_game_source.get()
        )
        self._trigger_refresh_after_live_game_loaded(source_value or self.current_game_source.get())
        if self.current_game_achievement_tiles and self.current_game_achievement_scroll_job is None:
            self._restart_current_game_achievement_auto_scroll(immediate=False)
        self._finalize_current_game_loading_overlay()

    # Method: _trigger_refresh_after_live_game_loaded - Déclenche une actualisation unique après le premier chargement en mode Live.
    def _trigger_refresh_after_live_game_loaded(self, source_value: str) -> None:
        if not self.pending_refresh_after_live_game_load:
            return
        if not self._is_emulator_live():
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
            self._request_event_sync("jeu Live chargé", delay_ms=0)

        try:
            self.root.after(0, do_refresh)
        except TclError:
            return

    # Method: _sync_emulator_status_after_current_game_update - Ajuste le statut Live selon la source réellement chargée.
    def _sync_emulator_status_after_current_game_update(self, source_value: str) -> None:
        lowered_source = source_value.strip().lower()
        if lowered_source.startswith("live"):
            self._set_emulator_status_text(EMULATOR_STATUS_GAME_LOADED)
            return
        if not self._is_emulator_live():
            self._set_emulator_status_text(EMULATOR_STATUS_INACTIVE)
            return
        self._set_emulator_status_text(EMULATOR_STATUS_EMULATOR_LOADED)

    # Method: _extract_game_detail_rows - Réalise le traitement lié à extract game detail rows.
    def _extract_game_detail_rows(self, payload: dict[str, object]) -> list[tuple[str, str]]:
        preferred = [
            ("GameTitle", "Titre"),
            ("ConsoleName", "Console"),
            ("ConsoleID", "ID console"),
            ("GameID", "ID jeu"),
            ("NumAchievements", "Nombre de succès"),
            ("NumAwardedToUser", "Succès débloqués (softcore)"),
            ("NumAwardedToUserHardcore", "Succès débloqués (hardcore)"),
            ("UserCompletion", "Complétion utilisateur"),
            ("UserCompletionHardcore", "Complétion utilisateur hardcore"),
            ("NumDistinctPlayers", "Nombre de joueurs"),
            ("Released", "Date de sortie"),
            ("Genre", "Genre"),
            ("Developer", "Développeur"),
            ("Publisher", "Éditeur"),
            ("ForumTopicID", "ID forum"),
        ]
        rows: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_key, label in preferred:
            value = payload.get(raw_key)
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                continue
            text = str(value).strip()
            if not text:
                continue
            rows.append((label, text))
            seen.add(raw_key)

        excluded = {
            "Achievements",
            "ImageIcon",
            "ImageTitle",
            "ImageIngame",
            "ImageBoxArt",
            "NumAwardedToUser",
            "NumAwardedToUserHardcore",
        }
        for raw_key in sorted(payload.keys()):
            if raw_key in seen or raw_key in excluded:
                continue
            value = payload.get(raw_key)
            if value is None or isinstance(value, (dict, list)):
                continue
            text = str(value).strip()
            if not text:
                continue
            rows.append((raw_key, text))
        return rows

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
        lowered_source = source_value.strip().lower()
        if lowered_source.startswith("live"):
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
        self._sync_locked_achievement_navigation(achievement_rows, next_achievement)
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
            and self.summary_label
            and self.theme_toggle_frame
        ):
            for col in range(4):
                self.top_bar.columnconfigure(col, weight=0)
            self.top_bar.columnconfigure(2, weight=1)
            self.summary_label.configure(wraplength=max(200, width - 430))
            self.connection_button.grid_configure(row=0, column=0, columnspan=1, padx=(0, 8), pady=0, sticky=W)
            self.profile_button.grid_configure(row=0, column=1, columnspan=1, padx=(0, 8), pady=0, sticky=W)
            self.summary_label.grid_configure(row=0, column=2, columnspan=1, padx=(6, 0), pady=0, sticky=W)
            self.theme_toggle_frame.grid_configure(row=0, column=3, columnspan=1, padx=(0, 0), pady=0, sticky="e")

        if self.current_game_title_value_label is not None:
            self.current_game_title_value_label.configure(wraplength=max(240, min(920, width - 470)))
        if self.current_game_next_achievement_desc_label is not None:
            self.current_game_next_achievement_desc_label.configure(wraplength=max(220, min(820, width - 360)))
        self._layout_current_game_achievement_gallery()

        if self.status_label is not None:
            self.status_label.configure(wraplength=max(180, width - 170))
        if self.status_bar is not None and self.version_label is not None:
            if width < 520:
                self.status_label.grid_configure(row=0, column=0, sticky="w")
                self.version_label.grid_configure(row=1, column=0, sticky="e", pady=(2, 0))
            else:
                self.status_label.grid_configure(row=0, column=0, sticky="w")
                self.version_label.grid_configure(row=0, column=1, sticky="e", pady=0)

    # Method: _load_config - Charge les données nécessaires.
    def _load_config(self) -> None:
        defaults = {
            "api_key": os.getenv("RA_API_KEY", ""),
            "api_username": os.getenv("RA_API_USERNAME", ""),
            "tracked_username": os.getenv("TRACKED_USERNAME", ""),
            "db_path": os.getenv("TRACKER_DB_PATH", str(data_dir() / "tracker.db")),
            "theme_mode": os.getenv("PYRA_THEME_MODE", "light"),
            "window_geometry": "",
        }
        self.has_saved_connection_record = False

        file_path = config_path()
        if file_path.exists():
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    defaults.update({k: str(v) for k, v in data.items() if k in defaults})
                    self.has_saved_connection_record = self._has_connection_data(defaults)
            except (OSError, ValueError):
                self.status_text.set("Impossible de lire config.json, valeurs par défaut utilisées.")

        self.api_key.set(defaults["api_key"])
        self.api_username.set(defaults["api_username"])
        self.tracked_username.set(defaults["tracked_username"])
        self.db_path.set(defaults["db_path"])
        self._set_theme(defaults["theme_mode"], persist=False)
        self._refresh_connection_summary()
        self._load_persisted_current_game_cache()
        self.root.after_idle(lambda: self._apply_saved_window_geometry(defaults.get("window_geometry", "")))

    # Method: _encode_current_game_images_for_cache - Convertit les images (bytes) en texte base64 pour le fichier JSON.
    def _encode_current_game_images_for_cache(self, images: dict[str, bytes]) -> dict[str, str]:
        encoded: dict[str, str] = {}
        for key, value in images.items():
            if not isinstance(key, str) or not isinstance(value, (bytes, bytearray)):
                continue
            encoded[key] = base64.b64encode(bytes(value)).decode("ascii")
        return encoded

    # Method: _decode_current_game_images_from_cache - Reconstruit les images (bytes) depuis un mapping base64.
    def _decode_current_game_images_from_cache(self, payload: object) -> dict[str, bytes]:
        if not isinstance(payload, dict):
            return {}
        decoded: dict[str, bytes] = {}
        for key, raw_value in payload.items():
            if not isinstance(key, str) or not isinstance(raw_value, str):
                continue
            try:
                decoded[key] = base64.b64decode(raw_value.encode("ascii"))
            except (ValueError, UnicodeError):
                continue
        return decoded

    # Method: _active_current_game_cache_key - Retourne la clé du jeu à persister, en priorité le jeu actuellement affiché.
    def _active_current_game_cache_key(self) -> tuple[str, int] | None:
        tracked = self._tracked_username().strip()
        current_key = self._current_game_last_key
        if current_key is not None:
            username, game_id = current_key
            if game_id > 0 and (not tracked or username == tracked):
                return username, game_id
        if not tracked:
            return None
        for username, game_id in reversed(list(self._current_game_details_cache.keys())):
            if username == tracked and game_id > 0:
                return username, game_id
        return None

    # Method: _persist_current_game_cache - Sauvegarde le cache du jeu en cours sur disque avant fermeture.
    def _persist_current_game_cache(self) -> None:
        key = self._active_current_game_cache_key()
        if key is None:
            return
        details_raw = self._current_game_details_cache.get(key, {})
        images_raw = self._current_game_images_cache.get(key, {})
        next_achievement = details_raw.get("next_achievement", {}) if isinstance(details_raw, dict) else {}
        achievements = details_raw.get("achievements", []) if isinstance(details_raw, dict) else []
        if not isinstance(next_achievement, dict):
            next_achievement = {}
        if not isinstance(achievements, list):
            achievements = []

        cache_payload = {
            "version": 1,
            "username": key[0],
            "game_id": int(key[1]),
            "display": {
                "title": self.current_game_title.get().strip(),
                "console": self.current_game_console.get().strip(),
                "progress": self.current_game_progress.get().strip(),
                "last_unlock": self.current_game_last_unlock.get().strip(),
                "source": self.current_game_source.get().strip(),
                "note": self.current_game_note.get().strip(),
            },
            "details": {
                "next_achievement": dict(next_achievement),
                "achievements": [dict(item) for item in achievements if isinstance(item, dict)],
            },
            "images": self._encode_current_game_images_for_cache(
                images_raw if isinstance(images_raw, dict) else {}
            ),
        }
        path = current_game_cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self._debug_log(
                f"_persist_current_game_cache saved game_id={key[1]} username='{key[0]}' path='{path}'"
            )
        except OSError as exc:
            self._debug_log(f"_persist_current_game_cache error: {exc}")

    # Method: _persist_current_game_cache_after_inactive_transition_if_needed - Sauvegarde le cache après transition Live -> Inactif.
    def _persist_current_game_cache_after_inactive_transition_if_needed(self, source_value: str = "") -> None:
        if not self.persist_current_game_cache_on_inactive_transition:
            return
        source = source_value.strip().lower()
        if source.startswith("live"):
            return
        self.persist_current_game_cache_on_inactive_transition = False
        self._persist_current_game_cache()
        self._debug_log(
            "_persist_current_game_cache_after_inactive_transition_if_needed: cache enregistré."
        )

    # Method: _load_persisted_current_game_cache - Recharge le dernier jeu en cours depuis le cache disque au démarrage.
    def _load_persisted_current_game_cache(self) -> None:
        path = current_game_cache_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._debug_log(f"_load_persisted_current_game_cache read error: {exc}")
            return
        if not isinstance(raw, dict):
            return

        cached_username = self._safe_text(raw.get("username"))
        tracked = self._tracked_username().strip()
        if tracked and cached_username and cached_username != tracked:
            self._debug_log(
                f"_load_persisted_current_game_cache ignored user_mismatch cached='{cached_username}' tracked='{tracked}'"
            )
            return

        game_id = self._safe_int(raw.get("game_id"))
        if game_id <= 0:
            return

        key_username = cached_username or tracked
        if not key_username:
            return
        key = (key_username, game_id)

        display = raw.get("display")
        if isinstance(display, dict):
            title_value = self._safe_text(display.get("title")) or (f"Jeu #{game_id}")
            console_value = self._safe_text(display.get("console")) or "-"
            progress_value = self._safe_text(display.get("progress")) or "-"
            last_unlock_value = self._safe_text(display.get("last_unlock")) or "-"
            source_value = self._safe_text(display.get("source")) or "Dernier jeu joué (cache)"
            note_value = self._safe_text(display.get("note")) or "Dernier jeu restauré depuis le cache local."
            self.current_game_title.set(title_value)
            self.current_game_console.set(console_value)
            self.current_game_progress.set(progress_value)
            self.current_game_last_unlock.set(last_unlock_value)
            self._set_current_game_source(source_value)
            self.current_game_note.set(note_value)

        details_raw = raw.get("details")
        next_achievement: dict[str, str] | None = None
        achievements: list[dict[str, str]] = []
        if isinstance(details_raw, dict):
            maybe_next = details_raw.get("next_achievement")
            maybe_achievements = details_raw.get("achievements")
            if isinstance(maybe_next, dict):
                next_achievement = {str(k): str(v) for k, v in maybe_next.items()}
            if isinstance(maybe_achievements, list):
                achievements = [dict(item) for item in maybe_achievements if isinstance(item, dict)]

        images = self._decode_current_game_images_from_cache(raw.get("images"))
        self._current_game_last_key = key
        self._current_game_details_cache[key] = {
            "next_achievement": dict(next_achievement) if next_achievement else {},
            "achievements": [dict(item) for item in achievements],
        }
        self._current_game_images_cache[key] = dict(images)
        self._set_current_game_achievement_rows(next_achievement, has_achievements=bool(achievements))
        self._set_current_game_achievement_gallery(achievements, images)
        self._set_current_game_images(images)
        self._sync_locked_achievement_navigation(achievements, next_achievement)
        self.prefer_persisted_current_game_on_startup = True
        self._debug_log(
            f"_load_persisted_current_game_cache restored game_id={game_id} username='{key_username}'"
        )

    # Method: _apply_saved_window_geometry - Applique les paramètres ou la transformation nécessaires.
    def _apply_saved_window_geometry(self, geometry_value: str) -> None:
        geometry = str(geometry_value).strip()
        if not geometry or not WINDOW_GEOMETRY_RE.fullmatch(geometry):
            return
        try:
            self.root.geometry(geometry)
        except TclError:
            return

    # Method: _has_connection_data - Vérifie si la condition attendue est satisfaite.
    def _has_connection_data(self, values: dict[str, str] | None = None) -> bool:
        source = values if values is not None else self._config_values()
        api_key = source.get("api_key", "").strip()
        api_user = source.get("api_username", "").strip()
        tracked = source.get("tracked_username", "").strip()
        return bool(api_key and (tracked or api_user))

    # Method: save_config - Enregistre les données concernées.
    def save_config(self) -> None:
        values = self._config_values()
        if not values["db_path"]:
            messagebox.showerror("Erreur", "Le chemin de la base est obligatoire.")
            return

        Path(values["db_path"]).parent.mkdir(parents=True, exist_ok=True)
        try:
            config_path().write_text(json.dumps(values, indent=2), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Erreur", f"Impossible de sauvegarder la configuration: {exc}")
            return

        self.status_text.set(f"Configuration sauvegardée dans {config_path()}")
        self.has_saved_connection_record = self._has_connection_data(values)
        self._reset_event_watch_state()
        self._refresh_connection_summary()
        if not self._ensure_db_ready(show_errors=True):
            return
        self.refresh_dashboard(show_errors=False)
        self._request_event_sync("connexion enregistrée", delay_ms=300)

    # Method: clear_saved_connection - Réinitialise les données ciblées.
    def clear_saved_connection(self) -> None:
        self._clear_saved_connection(confirm=False)

    # Method: _clear_saved_connection - Réinitialise les données ciblées.
    def _clear_saved_connection(self, confirm: bool) -> None:
        if confirm and not messagebox.askyesno(
            "Connexion",
            "Effacer la connexion enregistrée ?",
        ):
            return

        self.api_key.set("")
        self.api_username.set("")
        self.tracked_username.set("")
        self.has_saved_connection_record = False
        self._reset_event_watch_state()
        self._cancel_event_sync()

        values = self._config_values()
        values["api_key"] = ""
        values["api_username"] = ""
        values["tracked_username"] = ""
        raw_db = values.get("db_path", "").strip()
        if raw_db:
            Path(raw_db).parent.mkdir(parents=True, exist_ok=True)
        try:
            config_path().write_text(json.dumps(values, indent=2), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Erreur", f"Impossible d'effacer la connexion enregistrée: {exc}")
            return

        self._refresh_connection_summary()
        self._clear_dashboard("Connexion effacée. Configurez la connexion pour démarrer.")
        self.status_text.set("Connexion enregistrée effacée.")
        self.open_connection_window()

    # Method: _has_valid_connection - Vérifie si la condition attendue est satisfaite.
    def _has_valid_connection(self) -> bool:
        return bool(self.api_key.get().strip() and self._tracked_username())

    # Method: _connection_diagnostic - Retourne un diagnostic lisible sur l'état de la connexion.
    def _connection_diagnostic(self) -> str:
        api_key = self.api_key.get().strip()
        tracked_username = self._tracked_username().strip()
        if not api_key and not tracked_username:
            return "Diagnostic connexion: clé API manquante et utilisateur vide."
        if not api_key:
            return "Diagnostic connexion: clé API manquante."
        if not tracked_username:
            return "Diagnostic connexion: utilisateur vide."
        return "Diagnostic connexion: paramètres valides."

    # Method: _format_diagnostic_error - Transforme une exception en message de diagnostic explicite.
    def _format_diagnostic_error(self, error: Exception) -> str:
        detail = str(error).strip() or error.__class__.__name__
        if isinstance(error, sqlite3.Error):
            return f"Diagnostic SQLite: {detail}"
        if isinstance(error, RetroAPIError):
            return f"Diagnostic API: {detail}"
        if isinstance(error, OSError):
            return f"Diagnostic système: {detail}"
        if isinstance(error, ValueError):
            return f"Diagnostic données: {detail}"
        return f"Diagnostic erreur: {error.__class__.__name__}: {detail}"

    # Method: _has_saved_valid_connection - Vérifie si la condition attendue est satisfaite.
    def _has_saved_valid_connection(self) -> bool:
        return self.has_saved_connection_record and self._has_valid_connection()

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
        unlock_marker = ""
        diagnostic_error: str | None = None
        try:
            client = RetroAchievementsClient(api_key)
            summary = client.get_user_summary(username, include_recent_games=True)
            if emulator_live:
                live_game_id, _, _, _ = self._extract_live_current_game(summary)
                detected_game_id = live_game_id
            if detected_game_id <= 0:
                detected_game_id, _ = self._extract_last_played_game(summary)
            unlock_marker = self._extract_summary_unlock_marker(summary)
        except (RetroAPIError, OSError, ValueError) as exc:
            diagnostic_error = self._format_diagnostic_error(exc)

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
            return

        if self._event_watch_username != username:
            self._event_watch_username = username
            self._event_watch_game_id = detected_game_id
            self._event_watch_unlock_marker = unlock_marker
            self._set_status_message("Surveillance active: en attente d'un changement.", muted=True)
            self._debug_log(
                f"_on_event_sync_probe_result baseline user='{username}' game_id={detected_game_id}"
            )
            return

        game_changed = detected_game_id > 0 and detected_game_id != self._event_watch_game_id
        unlock_changed = bool(unlock_marker) and unlock_marker != self._event_watch_unlock_marker

        if not game_changed and not unlock_changed:
            self._event_watch_game_id = detected_game_id
            self._event_watch_unlock_marker = unlock_marker
            self._set_status_message(
                "Surveillance active: aucun nouveau succès ni changement de jeu.",
                muted=True,
            )
            return

        if self.sync_in_progress:
            self._request_event_sync(reason, delay_ms=700)
            return

        if game_changed and not unlock_changed:
            self._event_watch_game_id = detected_game_id
            self._event_watch_unlock_marker = unlock_marker
            self._debug_log(
                f"_on_event_sync_probe_result refresh rapide: changement de jeu game_id={detected_game_id}"
            )
            self._set_status_message("Changement de jeu détecté: rafraîchissement rapide...", muted=True)
            self.refresh_dashboard(show_errors=False, sync_before_refresh=False)
            return

        if game_changed and unlock_changed:
            trigger_reason = "changement de jeu et succès débloqué"
        else:
            trigger_reason = "succès débloqué"

        self._debug_log(
            f"_on_event_sync_probe_result déclenche sync: reason='{trigger_reason}' "
            f"game_id={detected_game_id}"
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
        # La synchronisation automatique est désactivée: aucune planification.
        return

    # Method: _auto_sync_tick - Exécute un traitement automatique planifié.
    def _auto_sync_tick(self) -> None:
        self.auto_sync_job = None
        # La synchronisation automatique est désactivée: aucun traitement périodique.
        return

    # Method: _cancel_event_sync - Annule la synchronisation par événement en attente.
    def _cancel_event_sync(self) -> None:
        if self.event_sync_job is not None:
            try:
                self.root.after_cancel(self.event_sync_job)
            except TclError:
                pass
            self.event_sync_job = None
        self.pending_event_sync_reason = ""

    # Method: _apply_status_label_style - Applique le style visuel normal/sourdine sur la barre d'état.
    def _apply_status_label_style(self, muted: bool) -> None:
        if self.status_label is None or not self.status_label.winfo_exists():
            return
        style_name = "StatusMuted.TLabel" if muted else "StatusDefault.TLabel"
        try:
            self.status_label.configure(style=style_name)
        except TclError:
            return

    # Method: _set_status_message - Met à jour la barre d'état avec option de style discret pour les changements d'état.
    def _set_status_message(self, message: str, muted: bool = False, muted_reset_ms: int = 2200) -> None:
        self.status_text.set(message)
        if self.status_muted_reset_job is not None:
            try:
                self.root.after_cancel(self.status_muted_reset_job)
            except TclError:
                pass
            self.status_muted_reset_job = None
        if not muted:
            self._apply_status_label_style(False)
            return
        self._apply_status_label_style(True)
        self.status_muted_reset_job = self.root.after(muted_reset_ms, lambda: self._apply_status_label_style(False))

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
        if self.sync_in_progress or self.event_probe_in_progress or self.event_sync_job is not None:
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
            self._request_event_sync(reason, delay_ms=700)
            return
        if not self._has_valid_connection():
            self._set_status_message(self._connection_diagnostic())
            return
        if self.event_probe_in_progress:
            self._request_event_sync(reason, delay_ms=700)
            return
        api_key = self.api_key.get().strip()
        username = self._tracked_username()
        emulator_live = self._is_emulator_live()
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

    # Method: _config_values - Réalise le traitement lié à config values.
    def _config_values(self) -> dict[str, str]:
        return {
            "api_key": self.api_key.get().strip(),
            "api_username": self.api_username.get().strip(),
            "tracked_username": self.tracked_username.get().strip(),
            "db_path": self.db_path.get().strip(),
            "theme_mode": "dark" if self.dark_mode_enabled.get() else "light",
            "window_geometry": self._current_window_geometry(),
        }

    # Method: _current_window_geometry - Réalise le traitement lié à current window geometry.
    def _current_window_geometry(self) -> str:
        try:
            if self.root.state() == "iconic":
                return ""
            geometry = self.root.winfo_geometry().strip()
        except TclError:
            return ""
        if not WINDOW_GEOMETRY_RE.fullmatch(geometry):
            return ""
        return geometry

    # Method: _cancel_scheduled_jobs - Annule les opérations planifiées.
    def _cancel_scheduled_jobs(self) -> None:
        for job_name in (
            "auto_sync_job",
            "event_sync_job",
            "status_muted_reset_job",
            "modal_track_job",
            "startup_init_job",
            "startup_finish_job",
            "startup_connection_job",
            "emulator_poll_job",
            "current_game_loading_timeout_job",
            "current_game_loading_hard_timeout_job",
            "current_game_achievement_scroll_job",
        ):
            job_id = getattr(self, job_name, None)
            if job_id is None:
                continue
            try:
                self.root.after_cancel(job_id)
            except TclError:
                pass
            setattr(self, job_name, None)
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

    # Method: _save_window_geometry - Enregistre les données concernées.
    def _save_window_geometry(self) -> None:
        values = self._config_values()
        try:
            config_path().write_text(json.dumps(values, indent=2), encoding="utf-8")
        except OSError:
            return

    # Method: _tracked_username - Détermine la valeur effectivement suivie.
    def _tracked_username(self) -> str:
        tracked = self.tracked_username.get().strip()
        if tracked:
            return tracked
        return self.api_username.get().strip()

    # Method: _refresh_connection_summary - Met à jour l'affichage ou l'état courant.
    def _refresh_connection_summary(self) -> None:
        username = self._tracked_username() or "(non configuré)"
        self.connection_summary.set(f"Compte: {username}")
        if self.connection_button is not None:
            label = "Connecté" if self._has_saved_valid_connection() else "Connexion"
            self.connection_button.configure(text=label)

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

    # Method: _on_theme_toggle - Traite l'événement correspondant.
    def _on_theme_toggle(self) -> None:
        mode = "dark" if self.dark_mode_enabled.get() else "light"
        self._set_theme(mode)

    # Method: _set_theme - Met à jour la valeur ou l'état associé.
    def _set_theme(self, mode: str, persist: bool = True) -> None:
        normalized = mode.lower().strip()
        if normalized not in THEME_MODES:
            normalized = "light"

        self.dark_mode_enabled.set(normalized == "dark")
        self._apply_theme(normalized)
        self._refresh_theme_toggle_buttons()
        if persist:
            self._save_theme_preference()

    # Method: _refresh_theme_toggle_buttons - Met à jour l'affichage ou l'état courant.
    def _refresh_theme_toggle_buttons(self) -> None:
        if self.theme_light_label is None or self.theme_dark_label is None:
            return

        dark_mode = self.dark_mode_enabled.get()
        self.theme_light_label.configure(style=("ThemeToggleActive.TLabel" if not dark_mode else "ThemeToggle.TLabel"))
        self.theme_dark_label.configure(style=("ThemeToggleActive.TLabel" if dark_mode else "ThemeToggle.TLabel"))

    # Method: _save_theme_preference - Enregistre les données concernées.
    def _save_theme_preference(self) -> None:
        try:
            values = self._config_values()
            raw_db_path = values.get("db_path", "").strip()
            if raw_db_path:
                Path(raw_db_path).parent.mkdir(parents=True, exist_ok=True)
            config_path().write_text(json.dumps(values, indent=2), encoding="utf-8")
        except OSError:
            self.status_text.set("Thème appliqué, sauvegarde de la préférence impossible.")

    # Method: _safe_style_configure - Exécute l'opération avec gestion d'erreur renforcée.
    def _safe_style_configure(self, style_name: str, **kwargs: object) -> None:
        if not kwargs:
            return
        try:
            self.style.configure(style_name, **kwargs)
            return
        except TclError:
            pass

        for key, value in kwargs.items():
            try:
                self.style.configure(style_name, **{key: value})
            except TclError:
                continue

    # Method: _safe_style_map - Exécute l'opération avec gestion d'erreur renforcée.
    def _safe_style_map(self, style_name: str, **kwargs: object) -> None:
        if not kwargs:
            return
        try:
            self.style.map(style_name, **kwargs)
            return
        except TclError:
            pass

        for key, value in kwargs.items():
            try:
                self.style.map(style_name, **{key: value})
            except TclError:
                continue

    # Method: _apply_theme - Applique les paramètres ou la transformation nécessaires.
    def _apply_theme(self, mode: str) -> None:
        if mode == "dark":
            colors = {
                "root_bg": "#1f2329",
                "panel_bg": "#2b313a",
                "text": "#e8ebef",
                "field_bg": "#262c34",
                "field_fg": "#e8ebef",
                "accent": "#3b82f6",
                "accent_hover": "#60a5fa",
                "selected_bg": "#2f5f9b",
                "selected_fg": "#ffffff",
                "border": "#3a414c",
            }
            title_color = "#93c5fd"
            source_live_color = "#4ade80"
            source_fallback_color = "#fbbf24"
            status_muted_color = "#9ca3af"
            status_inactive_color = "#4b5563"
        else:
            colors = {
                "root_bg": "#f3f5f8",
                "panel_bg": "#ffffff",
                "text": "#1f2937",
                "field_bg": "#ffffff",
                "field_fg": "#1f2937",
                "accent": "#2563eb",
                "accent_hover": "#3b82f6",
                "selected_bg": "#dbeafe",
                "selected_fg": "#111827",
                "border": "#d1d5db",
            }
            title_color = "#1d4ed8"
            source_live_color = "#15803d"
            source_fallback_color = "#b45309"
            status_muted_color = "#6b7280"
            status_inactive_color = "#4b5563"

        self.theme_colors = dict(colors)
        self.root.configure(bg=colors["root_bg"])

        self._safe_style_configure(".", background=colors["root_bg"], foreground=colors["text"])
        self._safe_style_configure("TFrame", background=colors["root_bg"])
        self._safe_style_configure("TLabel", background=colors["root_bg"], foreground=colors["text"])
        self._safe_style_configure("StatusDefault.TLabel", background=colors["root_bg"], foreground=colors["text"])
        self._safe_style_configure("StatusMuted.TLabel", background=colors["root_bg"], foreground=status_muted_color)
        self._safe_style_configure(
            "EmulatorStatusUnknown.TLabel",
            background=colors["root_bg"],
            foreground=colors["text"],
            font=("Segoe UI", 9, "bold"),
        )
        self._safe_style_configure(
            "EmulatorStatusLive.TLabel",
            background=colors["root_bg"],
            foreground=source_live_color,
            font=("Segoe UI", 9, "bold"),
        )
        self._safe_style_configure(
            "StatusTab.TFrame",
            background=colors["panel_bg"],
            bordercolor=colors["border"],
            borderwidth=1,
            relief="solid",
        )
        self._safe_style_configure(
            "StatusTabInactive.TLabel",
            background=colors["panel_bg"],
            foreground=status_inactive_color,
            font=("Segoe UI", 9, "bold"),
        )
        self._safe_style_configure(
            "StatusTabEmulatorLoaded.TLabel",
            background=colors["panel_bg"],
            foreground=source_live_color,
            font=("Segoe UI", 9, "bold"),
        )
        self._safe_style_configure(
            "StatusTabGameLoaded.TLabel",
            background=colors["panel_bg"],
            foreground=source_live_color,
            font=("Segoe UI", 9, "bold"),
        )
        self._safe_style_configure(
            "CurrentGameTitle.TLabel",
            background=colors["root_bg"],
            foreground=title_color,
            font=("Segoe UI", 11, "bold"),
        )
        self._safe_style_configure(
            "CurrentSourceUnknown.TLabel",
            background=colors["root_bg"],
            foreground=colors["text"],
            font=("Segoe UI", 9, "bold"),
        )
        self._safe_style_configure(
            "CurrentSourceLive.TLabel",
            background=colors["root_bg"],
            foreground=source_live_color,
            font=("Segoe UI", 9, "bold"),
        )
        self._safe_style_configure(
            "CurrentSourceFallback.TLabel",
            background=colors["root_bg"],
            foreground=source_fallback_color,
            font=("Segoe UI", 9, "bold"),
        )
        self._safe_style_configure("ThemeToggle.TLabel", background=colors["root_bg"], foreground=colors["text"])
        self._safe_style_configure(
            "ThemeToggleActive.TLabel",
            background=colors["root_bg"],
            foreground=colors["accent"],
            font=("Segoe UI", 9, "bold"),
        )
        self._safe_style_configure("ThemeToggleSep.TLabel", background=colors["root_bg"], foreground=colors["text"])
        self._safe_style_configure(
            "TLabelframe",
            background=colors["root_bg"],
            foreground=colors["text"],
            bordercolor=colors["border"],
        )
        self._safe_style_configure("TLabelframe.Label", background=colors["root_bg"], foreground=colors["text"])
        self._safe_style_configure("TButton", background=colors["panel_bg"], foreground=colors["text"], bordercolor=colors["border"])
        self._safe_style_map("TButton", background=[("active", colors["accent_hover"]), ("pressed", colors["accent"])])
        self._safe_style_configure("Modal.TFrame", background=colors["panel_bg"])
        self._safe_style_configure("Modal.TLabel", background=colors["panel_bg"], foreground=colors["text"])
        self._safe_style_configure(
            "Modal.TEntry",
            fieldbackground=colors["field_bg"],
            background=colors["field_bg"],
            foreground=colors["field_fg"],
            bordercolor=colors["border"],
            insertcolor=colors["field_fg"],
        )
        self._safe_style_configure(
            "Modal.TButton",
            background=colors["panel_bg"],
            foreground=colors["text"],
            bordercolor=colors["border"],
        )
        self._safe_style_map("Modal.TButton", background=[("active", colors["accent_hover"]), ("pressed", colors["accent"])])
        self._safe_style_configure(
            "Tooltip.TLabel",
            background=colors["panel_bg"],
            foreground=colors["text"],
            bordercolor=colors["border"],
            relief="solid",
            padding=(8, 6),
        )
        self._safe_style_configure(
            "TEntry",
            fieldbackground=colors["field_bg"],
            background=colors["field_bg"],
            foreground=colors["field_fg"],
            bordercolor=colors["border"],
            insertcolor=colors["field_fg"],
        )

        self._safe_style_configure("TNotebook", background=colors["root_bg"], borderwidth=0)
        self._safe_style_configure("TNotebook.Tab", background=colors["panel_bg"], foreground=colors["text"], padding=(10, 5))
        self._safe_style_map(
            "TNotebook.Tab",
            background=[("selected", colors["accent"]), ("active", colors["accent_hover"])],
            foreground=[("selected", "#ffffff"), ("active", "#ffffff")],
        )

        self._safe_style_configure(
            "Treeview",
            background=colors["field_bg"],
            fieldbackground=colors["field_bg"],
            foreground=colors["field_fg"],
            bordercolor=colors["border"],
            rowheight=24,
        )
        self._safe_style_configure(
            "Treeview.Heading",
            background=colors["panel_bg"],
            foreground=colors["text"],
            bordercolor=colors["border"],
        )
        self._safe_style_map(
            "Treeview.Heading",
            background=[("active", colors["panel_bg"]), ("pressed", colors["accent"])],
            foreground=[("active", colors["text"]), ("pressed", "#ffffff")],
        )
        self._safe_style_map(
            "Treeview",
            background=[("selected", colors["selected_bg"])],
            foreground=[("selected", colors["selected_fg"])],
        )

        if self.connection_window is not None and self.connection_window.winfo_exists():
            self.connection_window.configure(bg=colors["root_bg"])
        if self.profile_window is not None and self.profile_window.winfo_exists():
            self.profile_window.configure(bg=colors["root_bg"])
        if self.current_game_achievements_canvas is not None:
            try:
                self.current_game_achievements_canvas.configure(bg=colors["root_bg"])
            except TclError:
                pass
        if self.current_game_loading_overlay is not None and self.current_game_loading_overlay.winfo_exists():
            try:
                self.current_game_loading_overlay.configure(bg=colors["root_bg"])
                if self.current_game_loading_shade_id is not None:
                    self.current_game_loading_overlay.itemconfigure(
                        self.current_game_loading_shade_id,
                        fill="#000000",
                        stipple="gray25",
                    )
            except TclError:
                pass
        if self.current_game_achievement_tooltip_label is not None:
            self.current_game_achievement_tooltip_label.configure(style="Tooltip.TLabel")
        if self.maintenance_tab_tooltip_label is not None:
            self.maintenance_tab_tooltip_label.configure(style="Tooltip.TLabel")
        if self.profile_maintenance_tooltip_label is not None:
            self.profile_maintenance_tooltip_label.configure(style="Tooltip.TLabel")
        self._set_current_game_source(self.current_game_source.get())
        self._refresh_emulator_status_tab()
        modal = self._active_modal_window()
        if modal is not None:
            self._sync_modal_overlay()
            self._center_modal_window(modal)

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

    # Method: open_profile_window - Ouvre l'élément demandé.
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
        self.refresh_dashboard(show_errors=show_errors, sync_before_refresh=False)

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
                emulator_live = self._is_emulator_live()
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
                    emulator_live = self._is_emulator_live()
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
            "PyRA\nRetroachievement Tracker\nApplication desktop de suivi RetroAchievements.",
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
