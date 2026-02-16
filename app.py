from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import base64
from datetime import datetime
from pathlib import Path
from tkinter import END, HORIZONTAL, LEFT, VERTICAL, W, BooleanVar, Canvas, Menu, PhotoImage, StringVar, TclError, Tk, Toplevel, messagebox
from tkinter import font as tkfont
from tkinter import ttk

import requests

from retro_tracker.db import get_dashboard_data, init_db, save_snapshot
from retro_tracker.ra_api import RetroAPIError, RetroAchievementsClient


APP_NAME = "PyRA - RetroAchievements Tracker"
APP_VERSION = "1.0.0"
THEME_MODES = {"light", "dark"}
AUTO_SYNC_INTERVAL_MS = 60_000
EMULATOR_POLL_INTERVAL_MS = 8_000
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


# Class: TrackerApp - Orchestre l'interface, la synchronisation et les interactions utilisateur.
class TrackerApp:
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
        self.emulator_status_text = StringVar(value="Inactif ou inconnu")

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
        self.current_game_next_achievement_title = StringVar(value="-")
        self.current_game_next_achievement_description = StringVar(value="-")
        self.current_game_next_achievement_points = StringVar(value="-")
        self.current_game_next_achievement_unlocks = StringVar(value="-")
        self.current_game_next_achievement_feasibility = StringVar(value="-")
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
        self.theme_toggle_frame: ttk.Frame | None = None
        self.theme_light_label: ttk.Label | None = None
        self.theme_separator_label: ttk.Label | None = None
        self.theme_dark_label: ttk.Label | None = None
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
        self.current_game_source_value_label: ttk.Label | None = None
        self.current_game_image_labels: dict[str, ttk.Label] = {}
        self.current_game_image_refs: dict[str, PhotoImage] = {}
        self.current_game_achievements_canvas: Canvas | None = None
        self.current_game_achievements_inner: ttk.Frame | None = None
        self.current_game_achievements_window_id: int | None = None
        self.current_game_achievement_tiles: list[ttk.Label] = []
        self.current_game_achievement_data: list[dict[str, str]] = []
        self.current_game_achievement_refs: dict[str, PhotoImage] = {}
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
        self._last_layout_width = 0
        self._last_profile_layout_width = 0
        self._last_modal_anchor: tuple[int, int, int, int] = (0, 0, 0, 0)
        self.style = ttk.Style(self.root)
        self.theme_colors: dict[str, str] = {}
        self._tree_column_types: dict[str, dict[str, str]] = {}
        self._tree_headings: dict[str, dict[str, str]] = {}
        self._tree_sort_state: dict[str, tuple[str, bool]] = {}
        self._current_game_fetch_token = 0
        self._current_game_last_key: tuple[str, int] | None = None
        self._current_game_details_cache: dict[tuple[str, int], dict[str, object]] = {}
        self._current_game_images_cache: dict[tuple[str, int], dict[str, bytes]] = {}
        self._image_bytes_cache: dict[str, bytes] = {}
        self.sync_in_progress = False
        self.auto_sync_job: str | None = None
        self.startup_connection_job: str | None = None
        self.emulator_poll_job: str | None = None
        self.emulator_probe_in_progress = False
        self.has_saved_connection_record = False
        self.is_closing = False

        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")
        self._apply_theme("light")

        self._build_menu()
        self._build_ui()
        self._load_config()
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.bind_all("<Motion>", self._on_global_pointer_motion, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self.refresh_dashboard(show_errors=False)
        self._restart_auto_sync(immediate=True)
        self._restart_emulator_probe(immediate=True)
        self.startup_connection_job = self.root.after(150, self._open_connection_if_missing)

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
                timeout=8,
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

    # Method: _set_emulator_status - Met à jour le statut Live/Inactif affiché près du sélecteur de thème.
    def _set_emulator_status(self, is_live: bool) -> None:
        self.emulator_status_text.set("Live" if is_live else "Inactif ou inconnu")
        if self.emulator_status_label is not None:
            style_name = "EmulatorStatusLive.TLabel" if is_live else "EmulatorStatusUnknown.TLabel"
            self.emulator_status_label.configure(style=style_name)

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
        self._set_emulator_status(is_live)
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
            label="Synchroniser maintenant",
            command=self.sync_now,
            accelerator="Ctrl+R",
        )
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
        self.summary_label.grid(row=0, column=2, sticky=W)
        self.emulator_status_label = ttk.Label(
            self.top_bar,
            textvariable=self.emulator_status_text,
            style="EmulatorStatusUnknown.TLabel",
        )
        self.emulator_status_label.grid(row=0, column=3, padx=(8, 6), sticky="e")
        self.theme_toggle_frame = ttk.Frame(self.top_bar)
        self.theme_toggle_frame.grid(row=0, column=4, padx=(0, 0), sticky="e")
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
        content.rowconfigure(1, weight=1)
        ttk.Label(
            content,
            text="Cliquez sur Profil pour afficher les statistiques utilisateur.",
            anchor="center",
            justify="center",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        tabs = ttk.Notebook(content)
        tabs.grid(row=1, column=0, sticky="nsew")
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
        self.game_tree = self._build_games_table(games_tab)
        self.recent_tree = self._build_recent_table(recent_tab)
        self._build_current_game_tab(current_tab)

        self.status_bar = ttk.Frame(self.root)
        self.status_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.status_bar.columnconfigure(0, weight=1)
        self.status_bar.columnconfigure(1, weight=0)
        self.status_label = ttk.Label(self.status_bar, textvariable=self.status_text)
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

    # Method: _parse_sort_datetime - Analyse et convertit la valeur reçue.
    def _parse_sort_datetime(self, raw: str) -> datetime | None:
        text = raw.strip()
        if not text:
            return None

        candidates = [
            text,
            text.replace("Z", "+00:00"),
            text.replace(" UTC", "+00:00"),
            text.replace("T", " "),
        ]
        for candidate in candidates:
            try:
                return datetime.fromisoformat(candidate)
            except ValueError:
                continue

        formats = (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y",
        )
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

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
        self.current_game_title.set("-")
        self.current_game_console.set("-")
        self.current_game_progress.set("-")
        self.current_game_last_unlock.set("-")
        self._set_current_game_source("Inconnu")
        self.current_game_note.set(note)
        self.current_game_next_achievement_title.set("-")
        self.current_game_next_achievement_description.set("-")
        self.current_game_next_achievement_points.set("-")
        self.current_game_next_achievement_unlocks.set("-")
        self.current_game_next_achievement_feasibility.set("-")
        self.current_game_achievements_note.set("Aucun succès à afficher.")
        self._current_game_last_key = None
        self._current_game_fetch_token += 1
        if self.current_game_info_tree is not None:
            self.current_game_info_tree.delete(*self.current_game_info_tree.get_children())
        self.current_game_achievement_data = []
        self._clear_current_game_achievement_gallery()
        self._hide_current_game_achievement_tooltip()
        self.current_game_image_refs = {}
        for label in self.current_game_image_labels.values():
            label.configure(image="", text="Image indisponible")

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

    # Method: _clear_current_game_achievement_gallery - Réinitialise la galerie de succès du jeu courant.
    def _clear_current_game_achievement_gallery(self) -> None:
        self._stop_current_game_achievement_auto_scroll()
        self.current_game_achievement_refs = {}
        self.current_game_achievement_tiles = []
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

    # Method: _restart_current_game_achievement_auto_scroll - Planifie le prochain déplacement automatique.
    def _restart_current_game_achievement_auto_scroll(self, immediate: bool = False) -> None:
        if self.is_closing:
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
            self._show_current_game_achievement_tooltip(tooltip_text, prefer_left=prefer_left)

    # Method: _on_current_game_achievement_motion - Met à jour l'infobulle pendant le survol avec placement adapté.
    def _on_current_game_achievement_motion(self, tooltip_text: str, tile_index: int) -> None:
        if not self.current_game_achievement_hovered:
            return
        if tooltip_text.strip():
            prefer_left = self._should_show_achievement_tooltip_left(tile_index)
            self._show_current_game_achievement_tooltip(tooltip_text, prefer_left=prefer_left)

    # Method: _on_current_game_achievement_leave - Relance le défilement après le survol.
    def _on_current_game_achievement_leave(self) -> None:
        self.current_game_achievement_hovered = False
        self.current_game_achievement_tooltip_left_side = False
        self._hide_current_game_achievement_tooltip()
        if self.current_game_achievement_tiles and self.current_game_achievement_scroll_job is None:
            self._restart_current_game_achievement_auto_scroll(immediate=False)

    # Method: _move_current_game_achievement_tooltip - Repositionne l'infobulle près du pointeur, à droite ou à gauche.
    def _move_current_game_achievement_tooltip(self, prefer_left: bool | None = None) -> None:
        tooltip = self.current_game_achievement_tooltip
        if tooltip is None or not tooltip.winfo_exists():
            return
        tooltip.update_idletasks()
        use_left = self.current_game_achievement_tooltip_left_side if prefer_left is None else prefer_left
        tooltip_width = max(1, tooltip.winfo_reqwidth())
        tooltip_height = max(1, tooltip.winfo_reqheight())
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
            self.current_game_next_achievement_points.set("-")
            self.current_game_next_achievement_unlocks.set("-")
            self.current_game_next_achievement_feasibility.set("-")
            return

        self.current_game_next_achievement_title.set(next_achievement.get("title", "-"))
        self.current_game_next_achievement_description.set(next_achievement.get("description", "-"))
        self.current_game_next_achievement_points.set(next_achievement.get("points", "-"))
        self.current_game_next_achievement_unlocks.set(next_achievement.get("unlocks", "-"))
        self.current_game_next_achievement_feasibility.set(next_achievement.get("feasibility", "-"))

    # Method: _set_current_game_achievement_gallery - Alimente la galerie des succès avec images + infobulles.
    def _set_current_game_achievement_gallery(self, achievements: list[dict[str, str]], images: dict[str, bytes]) -> None:
        self.current_game_achievement_data = achievements
        self._clear_current_game_achievement_gallery()
        if self.current_game_achievements_inner is None:
            return
        if not achievements:
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

        self._layout_current_game_achievement_gallery()
        if self.current_game_achievements_canvas is not None:
            self.current_game_achievements_canvas.update_idletasks()
            self.current_game_achievements_canvas.configure(scrollregion=self.current_game_achievements_canvas.bbox("all"))
            self.current_game_achievements_canvas.yview_moveto(0.0)
        self.current_game_achievement_scroll_direction = 1
        self._restart_current_game_achievement_auto_scroll(immediate=True)

    # Method: _fetch_image_bytes - Télécharge une image distante avec cache mémoire.
    def _fetch_image_bytes(self, url: str) -> bytes | None:
        normalized = url.strip()
        if not normalized:
            return None
        cached = self._image_bytes_cache.get(normalized)
        if cached is not None:
            return cached
        try:
            response = requests.get(normalized, timeout=15)
            if response.status_code != 200 or not response.content:
                return None
        except requests.RequestException:
            return None

        self._image_bytes_cache[normalized] = response.content
        if len(self._image_bytes_cache) > 1200:
            oldest = next(iter(self._image_bytes_cache))
            self._image_bytes_cache.pop(oldest, None)
        return response.content

    # Method: _set_current_game_images - Met à jour la valeur ou l'état associé.
    def _set_current_game_images(self, images: dict[str, bytes]) -> None:
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

    # Method: _normalize_media_url - Normalise la valeur dans un format exploitable.
    def _normalize_media_url(self, path: str) -> str:
        raw = path.strip()
        if not raw:
            return ""
        if raw.lower().startswith("http://") or raw.lower().startswith("https://"):
            return raw
        if not raw.startswith("/"):
            raw = f"/{raw}"
        return f"https://media.retroachievements.org{raw}"

    # Method: _extract_game_achievements - Extrait la liste des succès depuis le payload détaillé du jeu.
    def _extract_game_achievements(self, payload: dict[str, object]) -> list[dict[str, object]]:
        raw = payload.get("Achievements")
        achievements: list[dict[str, object]] = []
        if isinstance(raw, dict):
            for key, value in raw.items():
                if not isinstance(value, dict):
                    continue
                item = dict(value)
                if "ID" not in item:
                    item["ID"] = key
                achievements.append(item)
        elif isinstance(raw, list):
            for value in raw:
                if isinstance(value, dict):
                    achievements.append(dict(value))

        # Method: sort_key - Détermine l'ordre d'affichage le plus lisible.
        def sort_key(item: dict[str, object]) -> tuple[int, int, str]:
            display = self._safe_int(item.get("DisplayOrder"))
            if display <= 0:
                display = 999_999
            ach_id = self._safe_int(item.get("ID"))
            if ach_id <= 0:
                ach_id = 999_999
            title = self._safe_text(item.get("Title")).casefold()
            return display, ach_id, title

        achievements.sort(key=sort_key)
        return achievements

    # Method: _is_achievement_unlocked - Vérifie si le succès est déjà débloqué par l'utilisateur.
    def _is_achievement_unlocked(self, achievement: dict[str, object]) -> bool:
        if self._safe_bool(achievement.get("IsUnlocked")) or self._safe_bool(achievement.get("Unlocked")):
            return True

        for key in ("DateEarnedHardcore", "DateEarned", "DateEarnedAt", "DateEarnedHardcoreAt", "DateUnlocked"):
            if self._safe_text(achievement.get(key)):
                return True

        locked_text = self._safe_text(achievement.get("Locked")).lower()
        if locked_text in {"0", "false", "no"}:
            return True
        if locked_text in {"1", "true", "yes"}:
            return False
        return False

    # Method: _achievement_badge_url - Construit l'URL d'image d'un succès.
    def _achievement_badge_url(self, achievement: dict[str, object]) -> str:
        direct = (
            "BadgeURL",
            "BadgeUri",
            "BadgeImageUrl",
            "Badge",
            "BadgeName",
        )
        for key in direct:
            raw = self._safe_text(achievement.get(key))
            if not raw:
                continue
            lowered = raw.lower()
            if lowered.startswith("http://") or lowered.startswith("https://"):
                return raw
            if raw.startswith("/"):
                return self._normalize_media_url(raw)
            if "badge/" in lowered:
                return self._normalize_media_url(raw)
            if raw.endswith(".png") or raw.endswith(".jpg") or raw.endswith(".jpeg"):
                return self._normalize_media_url(raw)
            return f"https://media.retroachievements.org/Badge/{raw}.png"
        return ""

    # Method: _locked_badge_url - Convertit l'URL du badge vers sa variante verrouillée (_lock).
    def _locked_badge_url(self, badge_url: str) -> str:
        raw = badge_url.strip()
        if not raw:
            return ""
        base = raw
        suffix = ""
        for sep in ("?", "#"):
            idx = base.find(sep)
            if idx != -1:
                suffix = base[idx:]
                base = base[:idx]
                break
        if "_lock." in base.lower():
            return raw
        dot_idx = base.rfind(".")
        if dot_idx <= 0:
            return f"{base}_lock{suffix}"
        return f"{base[:dot_idx]}_lock{base[dot_idx:]}{suffix}"

    # Method: _format_tooltip_description_three_lines - Formate une description sur 1 à 3 lignes lisibles selon sa longueur.
    def _format_tooltip_description_three_lines(self, description: str, line_max: int = 62) -> str:
        normalized = " ".join(description.split())
        if len(normalized) <= line_max:
            return normalized

        words = normalized.split(" ")
        lines: list[str] = []
        current_parts: list[str] = []
        for word in words:
            if not current_parts:
                current_parts = [word]
                continue
            candidate = " ".join(current_parts + [word])
            if len(candidate) <= line_max or len(lines) >= 2:
                current_parts.append(word)
            else:
                lines.append(" ".join(current_parts).strip())
                current_parts = [word]
        if current_parts:
            lines.append(" ".join(current_parts).strip())

        if len(lines) <= 3:
            return "\n".join(lines)

        trimmed = lines[:2]
        remaining = " ".join(lines[2:]).strip()
        trimmed.append(remaining)
        return "\n".join(trimmed)

    # Method: _build_achievement_tooltip - Formate le texte à afficher au survol d'un badge.
    def _build_achievement_tooltip(self, achievement: dict[str, object]) -> str:
        title = self._safe_text(achievement.get("Title")) or f"Succès #{self._safe_int(achievement.get('ID'))}"
        description = self._safe_text(achievement.get("Description")) or "Sans description."
        formatted_description = self._format_tooltip_description_three_lines(description)
        return f"{title}\n{formatted_description}"

    # Method: _safe_float - Convertit une valeur numérique vers float de manière tolérante.
    def _safe_float(self, value: object) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        text = self._safe_text(value).replace(",", ".")
        if not text:
            return None
        text = re.sub(r"[^0-9\.\-]", "", text)
        if text in {"", "-", ".", "-."}:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    # Method: _build_achievement_feasibility - Évalue la difficulté estimée d'un succès à partir des statistiques publiques.
    def _build_achievement_feasibility(self, awarded: int, total_players: int, true_ratio_value: float | None) -> str:
        if total_players > 0 and awarded >= 0:
            unlock_pct = (awarded * 100.0) / max(1, total_players)
            if unlock_pct >= 50.0:
                level = "Très facile"
            elif unlock_pct >= 25.0:
                level = "Facile"
            elif unlock_pct >= 10.0:
                level = "Moyenne"
            elif unlock_pct >= 3.0:
                level = "Difficile"
            else:
                level = "Très difficile"
            return f"{level} ({unlock_pct:.1f}% des joueurs)"

        if true_ratio_value is not None:
            if true_ratio_value <= 1.5:
                level = "Très facile"
            elif true_ratio_value <= 2.5:
                level = "Facile"
            elif true_ratio_value <= 4.0:
                level = "Moyenne"
            elif true_ratio_value <= 8.0:
                level = "Difficile"
            else:
                level = "Très difficile"
            return f"{level} (TrueRatio {true_ratio_value:.2f})"

        return "Inconnue"

    # Method: _build_next_achievement_summary - Prépare les champs de la section du premier succès non débloqué.
    def _build_next_achievement_summary(self, achievement: dict[str, object], total_players: int = 0) -> dict[str, str]:
        title = self._safe_text(achievement.get("Title")) or f"Succès #{self._safe_int(achievement.get('ID'))}"
        description = self._safe_text(achievement.get("Description")) or "Sans description."
        points = self._safe_int(achievement.get("Points"))
        true_ratio = self._safe_text(achievement.get("TrueRatio")) or "-"
        true_ratio_value = self._safe_float(achievement.get("TrueRatio"))
        awarded = self._safe_int(achievement.get("NumAwarded"))
        awarded_hardcore = self._safe_int(achievement.get("NumAwardedHardcore"))
        feasibility = self._build_achievement_feasibility(awarded, total_players, true_ratio_value)
        return {
            "title": title,
            "description": description,
            "points": f"{points} points | True ratio: {true_ratio}",
            "unlocks": f"Global: {awarded} | Hardcore: {awarded_hardcore}",
            "feasibility": feasibility,
        }

    # Method: _pick_current_game - Sélectionne l'élément le plus pertinent.
    def _pick_current_game(self, dashboard: dict[str, object]) -> tuple[int, str]:
        recent = dashboard.get("recent_achievements", [])
        if isinstance(recent, list):
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

    # Method: _safe_int - Exécute l'opération avec gestion d'erreur renforcée.
    def _safe_int(self, value: object) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    # Method: _safe_bool - Convertit une valeur en booléen de manière tolérante.
    def _safe_bool(self, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        text = str(value).strip().lower()
        return text in {"1", "true", "yes", "on", "online"}

    # Method: _safe_text - Convertit une valeur simple en texte sans exposer une structure brute.
    def _safe_text(self, value: object) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value).strip()
        return ""

    # Method: _extract_title_text - Extrait un titre de jeu lisible depuis une valeur API potentiellement imbriquée.
    def _extract_title_text(self, value: object) -> str:
        plain = self._safe_text(value)
        if plain:
            return plain
        if isinstance(value, dict):
            preferred = (
                "Title",
                "GameTitle",
                "Name",
                "GameName",
                "MostRecentGameTitle",
                "LastGame",
            )
            for key in preferred:
                text = self._safe_text(value.get(key))
                if text:
                    return text
            for key, item in value.items():
                if "title" in key.lower():
                    text = self._safe_text(item)
                    if text:
                        return text
            return ""
        if isinstance(value, list):
            for item in value:
                text = self._extract_title_text(item)
                if text:
                    return text
        return ""

    # Method: _format_datetime_display - Formate une date brute en date+heure lisibles.
    def _format_datetime_display(self, raw: object) -> str:
        text = self._safe_text(raw)
        if not text:
            return ""
        parsed = self._parse_sort_datetime(text)
        if parsed is None:
            return text
        if parsed.tzinfo is not None:
            try:
                parsed = parsed.astimezone().replace(tzinfo=None)
            except ValueError:
                pass
        return parsed.strftime("%Y-%m-%d %H:%M")

    # Method: _extract_live_current_game - Extrait le jeu en cours depuis le résumé API.
    def _extract_live_current_game(self, summary: dict[str, object]) -> tuple[int, str, str, bool]:
        rich_presence = ""
        for field in ("RichPresenceMsg", "RichPresence", "RichPresenceMessage"):
            value = self._safe_text(summary.get(field))
            if value:
                rich_presence = value
                break

        online = self._safe_bool(summary.get("IsOnline")) or self._safe_bool(summary.get("IsOnine"))

        direct_pairs = (
            ("MostRecentGameID", "MostRecentGameTitle"),
            ("LastGameID", "LastGame"),
            ("GameID", "GameTitle"),
        )
        for game_id_field, title_field in direct_pairs:
            game_id = self._safe_int(summary.get(game_id_field))
            if game_id > 0:
                return game_id, self._extract_title_text(summary.get(title_field)), rich_presence, online

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

        return 0, "", rich_presence, online

    # Method: _build_current_game_local_rows - Construit les lignes du résumé local du jeu courant.
    def _build_current_game_local_rows(
        self,
        game_id: int,
        title_hint: str,
        games_lookup: dict[int, dict[str, object]],
        source: str = "",
        rich_presence: str = "",
    ) -> tuple[str, str, str, str, str, list[tuple[str, str]]]:
        game_row = games_lookup.get(game_id)
        title = title_hint or (str(game_row.get("title", "")).strip() if isinstance(game_row, dict) else "")
        console = str(game_row.get("console_name", "")).strip() if isinstance(game_row, dict) else ""
        hardcore = str(game_row.get("num_awarded_hardcore", 0)) if isinstance(game_row, dict) else "0"
        max_possible = str(game_row.get("max_possible", 0)) if isinstance(game_row, dict) else "0"
        pct = str(game_row.get("completion_pct", 0)) if isinstance(game_row, dict) else "0"
        raw_last_unlock = game_row.get("most_recent_awarded_date", "") if isinstance(game_row, dict) else ""
        last_unlock = self._format_datetime_display(raw_last_unlock)
        title_value = title or (f"Jeu #{game_id}" if game_id > 0 else "-")
        console_value = console or "-"
        progress_value = f"{hardcore}/{max_possible} ({pct}%)"
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
    def _update_current_game_tab(self, dashboard: dict[str, object], username: str) -> None:
        games_lookup: dict[int, dict[str, object]] = {}
        games = dashboard.get("games", [])
        if isinstance(games, list):
            for item in games:
                if not isinstance(item, dict):
                    continue
                game_id = self._safe_int(item.get("game_id"))
                if game_id > 0:
                    games_lookup[game_id] = item

        fallback_game_id, fallback_title = self._pick_current_game(dashboard)
        fallback_key = (username, fallback_game_id)
        same_fallback = fallback_game_id > 0 and self._current_game_last_key == fallback_key
        if fallback_game_id > 0:
            title_value, console_value, progress_value, last_unlock_value, source_value, fallback_rows = self._build_current_game_local_rows(
                fallback_game_id,
                fallback_title,
                games_lookup,
                source="Fallback local",
            )
            if not same_fallback:
                self.current_game_title.set(title_value)
                self.current_game_console.set(console_value)
                self.current_game_progress.set(progress_value)
                self.current_game_last_unlock.set(last_unlock_value)
                self._set_current_game_source(source_value)
                self._set_current_game_info_rows(fallback_rows)
                self._set_current_game_achievement_rows(None, has_achievements=False)
                self._set_current_game_achievement_gallery([], {})
                self._set_current_game_images({})
        else:
            if self._current_game_last_key is None:
                self._clear_current_game_details("Détection du jeu en cours...")

        api_key = self.api_key.get().strip()
        if not api_key:
            if fallback_game_id > 0:
                self.current_game_note.set("Jeu estimé localement (clé API manquante pour la détection en direct).")
            else:
                self.current_game_note.set("Clé API manquante pour détecter le jeu en direct.")
            return

        if self._current_game_last_key is None or not same_fallback:
            self.current_game_note.set("Détection du jeu en cours via RetroAchievements...")
        self._current_game_fetch_token += 1
        fetch_token = self._current_game_fetch_token
        worker = threading.Thread(
            target=self._fetch_current_game_worker,
            args=(api_key, username, fallback_game_id, fallback_title, games_lookup, fetch_token),
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
        fetch_token: int,
    ) -> None:
        game_id = fallback_game_id
        title_hint = fallback_title
        source_label = "Fallback local"
        rich_presence = ""
        images: dict[str, bytes] = {}
        next_achievement: dict[str, str] | None = None
        achievement_rows: list[dict[str, str]] = []
        error: str | None = None

        client = RetroAchievementsClient(api_key)
        try:
            summary = client.get_user_summary(username, include_recent_games=True)
            live_game_id, live_title, rich_presence, is_online = self._extract_live_current_game(summary)
            if live_game_id > 0:
                game_id = live_game_id
                if live_title:
                    title_hint = live_title
                source_label = "Live émulateur" if (is_online or rich_presence) else "Live API"
            elif fallback_game_id <= 0:
                source_label = "Inconnu"
        except (RetroAPIError, OSError, ValueError):
            if fallback_game_id > 0:
                source_label = "Fallback local"
            else:
                source_label = "Inconnu"

        detected_key = (username, game_id if game_id > 0 else 0)
        if self._current_game_last_key == detected_key:
            self._queue_ui_callback(
                lambda: self._on_current_game_unchanged(
                    fetch_token=fetch_token,
                    note="Jeu en cours inchangé.",
                )
            )
            return

        if game_id <= 0:
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
                    note="Aucun jeu en cours détecté.",
                )
            )
            return

        try:
            payload = client.get_game_info_and_user_progress(username, game_id)
            total_players = self._safe_int(payload.get("NumDistinctPlayers"))
            boxart_url = self._normalize_media_url(str(payload.get("ImageBoxArt", "")))
            boxart_bytes = self._fetch_image_bytes(boxart_url)
            if boxart_bytes:
                images["boxart"] = boxart_bytes

            all_achievements = self._extract_game_achievements(payload)
            all_achievements.sort(key=lambda achievement: 1 if self._is_achievement_unlocked(achievement) else 0)
            first_locked_image_key = ""
            for index, achievement in enumerate(all_achievements):
                ach_id = self._safe_int(achievement.get("ID"))
                image_key = f"achievement_{ach_id if ach_id > 0 else (index + 1)}_{index}"
                tooltip = self._build_achievement_tooltip(achievement)
                is_unlocked = self._is_achievement_unlocked(achievement)
                achievement_rows.append(
                    {
                        "image_key": image_key,
                        "tooltip": tooltip,
                    }
                )

                badge_url = self._achievement_badge_url(achievement)
                badge_bytes: bytes | None = None
                if badge_url:
                    preferred_url = badge_url if is_unlocked else self._locked_badge_url(badge_url)
                    badge_bytes = self._fetch_image_bytes(preferred_url)
                    if badge_bytes is None and preferred_url != badge_url:
                        badge_bytes = self._fetch_image_bytes(badge_url)
                if badge_bytes:
                    images[image_key] = badge_bytes

                if next_achievement is None and not is_unlocked:
                    next_achievement = self._build_next_achievement_summary(
                        achievement,
                        total_players=total_players,
                    )
                    first_locked_image_key = image_key

            if first_locked_image_key and first_locked_image_key in images:
                images["next_badge"] = images[first_locked_image_key]
        except (RetroAPIError, OSError, ValueError) as exc:
            error = str(exc)

        title_value, console_value, progress_value, last_unlock_value, source_value, _ = self._build_current_game_local_rows(
            game_id,
            title_hint,
            games_lookup,
            source=source_label,
            rich_presence=rich_presence,
        )
        note = "Jeu détecté en direct." if source_label.startswith("Live") else "Détails chargés."
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
                note=note,
            )
        )

    # Method: _on_current_game_unchanged - Conserve l'affichage actuel quand le jeu détecté n'a pas changé.
    def _on_current_game_unchanged(self, fetch_token: int, note: str) -> None:
        if fetch_token != self._current_game_fetch_token:
            return
        self.current_game_note.set(note)
        if self.current_game_achievement_tiles and self.current_game_achievement_scroll_job is None:
            self._restart_current_game_achievement_auto_scroll(immediate=False)

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
        note: str,
    ) -> None:
        if fetch_token != self._current_game_fetch_token:
            return
        self._current_game_last_key = key
        self.current_game_title.set(title_value)
        self.current_game_console.set(console_value)
        self.current_game_progress.set(progress_value)
        self.current_game_last_unlock.set(last_unlock_value)
        self._set_current_game_source(source_value)
        if error:
            self.current_game_note.set(f"Détails indisponibles: {error}")
            self._set_current_game_achievement_rows(None, has_achievements=False)
            self._set_current_game_achievement_gallery([], {})
            self._set_current_game_images({})
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
            and self.emulator_status_label
            and self.theme_toggle_frame
        ):
            for col in range(5):
                self.top_bar.columnconfigure(col, weight=0)
            self.top_bar.columnconfigure(2, weight=1)
            self.summary_label.configure(wraplength=max(200, width - 520))

            if width < 780:
                self.connection_button.grid_configure(row=0, column=0, columnspan=1, padx=(0, 8), pady=0, sticky=W)
                self.profile_button.grid_configure(row=0, column=1, columnspan=1, padx=(0, 8), pady=0, sticky=W)
                self.summary_label.grid_configure(row=1, column=0, columnspan=5, pady=(4, 0), sticky="ew")
                self.emulator_status_label.grid_configure(row=0, column=3, columnspan=1, padx=(8, 6), pady=0, sticky="e")
                self.theme_toggle_frame.grid_configure(row=0, column=4, columnspan=1, padx=(0, 0), pady=0, sticky="e")
            elif width < 980:
                self.connection_button.grid_configure(row=0, column=0, columnspan=1, padx=(0, 8), pady=(0, 4), sticky=W)
                self.profile_button.grid_configure(row=0, column=1, columnspan=1, padx=(0, 8), pady=(0, 4), sticky=W)
                self.summary_label.grid_configure(row=1, column=0, columnspan=5, pady=(4, 0), sticky="ew")
                self.emulator_status_label.grid_configure(row=0, column=3, columnspan=1, padx=(8, 6), pady=(0, 4), sticky="e")
                self.theme_toggle_frame.grid_configure(row=0, column=4, columnspan=1, padx=(0, 0), pady=(0, 4), sticky="e")
            else:
                self.connection_button.grid_configure(row=0, column=0, columnspan=1, padx=(0, 8), pady=0, sticky=W)
                self.profile_button.grid_configure(row=0, column=1, columnspan=1, padx=(0, 8), pady=0, sticky=W)
                self.summary_label.grid_configure(row=0, column=2, columnspan=1, pady=0, sticky=W)
                self.emulator_status_label.grid_configure(row=0, column=3, columnspan=1, padx=(8, 6), pady=0, sticky="e")
                self.theme_toggle_frame.grid_configure(row=0, column=4, columnspan=1, padx=(0, 0), pady=0, sticky="e")

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
        self.root.after_idle(lambda: self._apply_saved_window_geometry(defaults.get("window_geometry", "")))

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
        self._refresh_connection_summary()
        if not self._ensure_db_ready(show_errors=True):
            return
        self.sync_now(show_errors=False)
        self._restart_auto_sync(immediate=False)

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
        self._restart_auto_sync(immediate=False)
        self.status_text.set("Connexion enregistrée effacée.")
        self.open_connection_window()

    # Method: _has_valid_connection - Vérifie si la condition attendue est satisfaite.
    def _has_valid_connection(self) -> bool:
        return bool(self.api_key.get().strip() and self._tracked_username())

    # Method: _has_saved_valid_connection - Vérifie si la condition attendue est satisfaite.
    def _has_saved_valid_connection(self) -> bool:
        return self.has_saved_connection_record and self._has_valid_connection()

    # Method: _restart_auto_sync - Réalise le traitement lié à restart auto sync.
    def _restart_auto_sync(self, immediate: bool = False) -> None:
        if self.is_closing:
            return
        if self.auto_sync_job is not None:
            try:
                self.root.after_cancel(self.auto_sync_job)
            except TclError:
                pass
            self.auto_sync_job = None

        delay = 1_000 if immediate else AUTO_SYNC_INTERVAL_MS
        self.auto_sync_job = self.root.after(delay, self._auto_sync_tick)

    # Method: _auto_sync_tick - Exécute un traitement automatique planifié.
    def _auto_sync_tick(self) -> None:
        self.auto_sync_job = None
        if self.is_closing:
            return
        if self._has_saved_valid_connection():
            self.sync_now(show_errors=False)
        self._restart_auto_sync(immediate=False)

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
            "modal_track_job",
            "startup_connection_job",
            "emulator_poll_job",
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

    # Method: _on_app_close - Traite l'événement correspondant.
    def _on_app_close(self) -> None:
        if self.is_closing:
            return
        self.is_closing = True
        self._hide_maintenance_tab_tooltip()
        self._hide_profile_maintenance_tooltip()
        self._cancel_scheduled_jobs()
        self._close_profile_window()
        self._close_connection_window()
        self._save_window_geometry()
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

        self.theme_colors = dict(colors)
        self.root.configure(bg=colors["root_bg"])

        self._safe_style_configure(".", background=colors["root_bg"], foreground=colors["text"])
        self._safe_style_configure("TFrame", background=colors["root_bg"])
        self._safe_style_configure("TLabel", background=colors["root_bg"], foreground=colors["text"])
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
        if self.current_game_achievement_tooltip_label is not None:
            self.current_game_achievement_tooltip_label.configure(style="Tooltip.TLabel")
        if self.maintenance_tab_tooltip_label is not None:
            self.maintenance_tab_tooltip_label.configure(style="Tooltip.TLabel")
        if self.profile_maintenance_tooltip_label is not None:
            self.profile_maintenance_tooltip_label.configure(style="Tooltip.TLabel")
        self._set_current_game_source(self.current_game_source.get())
        self._set_emulator_status(self.emulator_status_text.get().strip().casefold() == "live")
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
        if not db:
            if show_errors:
                messagebox.showerror("Erreur", "Le chemin de la base est obligatoire.")
            return False
        try:
            Path(db).parent.mkdir(parents=True, exist_ok=True)
            init_db(db)
        except (OSError, sqlite3.Error) as exc:
            if show_errors:
                messagebox.showerror("Erreur", f"Impossible d'initialiser la base de données: {exc}")
            return False
        return True

    # Method: sync_now - Synchronise les données concernées.
    def sync_now(self, show_errors: bool = True) -> None:
        if self.sync_in_progress:
            return
        api_key = self.api_key.get().strip()
        username = self._tracked_username()
        if not api_key or not username:
            if show_errors:
                messagebox.showerror("Erreur", "La clé API et l'utilisateur sont obligatoires.")
            return
        if not self._ensure_db_ready(show_errors=show_errors):
            return

        self.sync_in_progress = True
        if self.sync_button is not None:
            self.sync_button.state(["disabled"])
        self.status_text.set("Synchronisation en cours..." if show_errors else "Synchronisation auto en cours...")

        worker = threading.Thread(target=self._sync_worker, args=(show_errors,), daemon=True)
        worker.start()

    # Method: _sync_worker - Synchronise les données concernées.
    def _sync_worker(self, show_errors: bool) -> None:
        config = self._config_values()
        username = self._tracked_username()
        try:
            client = RetroAchievementsClient(config["api_key"])
            snapshot = client.fetch_snapshot(username)
            save_snapshot(config["db_path"], snapshot)
        except (RetroAPIError, OSError, sqlite3.Error, ValueError) as exc:
            error_message = str(exc)
            self._queue_ui_callback(lambda msg=error_message: self._on_sync_error(msg, show_errors))
            return
        self._queue_ui_callback(lambda: self._on_sync_success(show_errors))

    # Method: _queue_ui_callback - Planifie l'action sur le thread d'interface.
    def _queue_ui_callback(self, callback) -> None:
        if self.is_closing:
            return
        try:
            self.root.after(0, callback)
        except TclError:
            return

    # Method: _on_sync_error - Traite l'événement correspondant.
    def _on_sync_error(self, message: str, show_errors: bool) -> None:
        self.sync_in_progress = False
        if self.sync_button is not None:
            self.sync_button.state(["!disabled"])
        self.status_text.set("Synchronisation échouée." if show_errors else "Synchronisation auto échouée.")
        if show_errors:
            messagebox.showerror("Erreur de synchronisation", message)

    # Method: _on_sync_success - Traite l'événement correspondant.
    def _on_sync_success(self, show_errors: bool) -> None:
        self.sync_in_progress = False
        if self.sync_button is not None:
            self.sync_button.state(["!disabled"])
        self.status_text.set("Synchronisation terminée." if show_errors else "Synchronisation auto terminée.")
        self.refresh_dashboard(show_errors=show_errors)

    # Method: refresh_dashboard - Réalise le traitement lié à refresh dashboard.
    def refresh_dashboard(self, show_errors: bool = True) -> None:
        if not self._ensure_db_ready(show_errors=show_errors):
            return

        username = self._tracked_username()
        if not username:
            self._clear_dashboard("Aucun utilisateur configuré.")
            self._open_connection_if_missing()
            return

        dashboard = get_dashboard_data(self.db_path.get().strip(), username)
        latest = dashboard.get("latest")
        delta = dashboard.get("delta")

        if not latest:
            self._clear_dashboard("Aucune donnée locale. Synchronisation auto en attente.")
            return

        self.stat_points.set(self._with_delta(latest["total_points"], delta, "points"))
        self.stat_softcore.set(self._with_delta(latest["softcore_points"], delta, "softcore_points"))
        self.stat_true.set(self._with_delta(latest["true_points"], delta, "true_points"))
        self.stat_mastered.set(self._with_delta(latest["mastered_games"], delta, "mastered_games"))
        self.stat_beaten.set(self._with_delta(latest["beaten_games"], delta, "beaten_games"))
        self.stat_games.set(str(latest["total_games"]))
        self.stat_snapshot.set(str(latest["captured_at"]))

        self._fill_games_table(dashboard.get("games", []))
        self._fill_recent_table(dashboard.get("recent_achievements", []))
        self._update_current_game_tab(dashboard, username)
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

    # Method: _clear_dashboard - Réinitialise les données ciblées.
    def _clear_dashboard(self, status: str) -> None:
        self.stat_points.set("-")
        self.stat_softcore.set("-")
        self.stat_true.set("-")
        self.stat_mastered.set("-")
        self.stat_beaten.set("-")
        self.stat_games.set("-")
        self.stat_snapshot.set("-")
        self._fill_games_table([])
        self._fill_recent_table([])
        self._clear_current_game_details(status)
        self.status_text.set(status)

    # Method: show_about - Réalise le traitement lié à show about.
    def show_about(self) -> None:
        messagebox.showinfo(
            "À propos",
            "PyRA Tracker\nApplication desktop de suivi RetroAchievements.",
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


# Function: main - Démarre l'application.
def main() -> None:
    root = Tk()
    app = TrackerApp(root)
    app.root.mainloop()


if __name__ == "__main__":
    main()

