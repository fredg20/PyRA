from __future__ import annotations

import re
from tkinter import HORIZONTAL, VERTICAL, W, Canvas, Menu, TclError
from tkinter import font as tkfont
from tkinter import ttk

from retro_tracker.app_meta import APP_VERSION
from retro_tracker.runtime_constants import (
    BADGE_IMAGE_CORNER_RADIUS,
    COVER_IMAGE_CORNER_RADIUS,
    MAIN_TAB_CURRENT,
    MAIN_TAB_GAMES,
    MAIN_TAB_LABELS,
    MAIN_TAB_MAINTENANCE_KEYS,
    MAIN_TAB_ORDER,
    MAIN_TAB_RECENT,
    NOTEBOOK_TAB_SECTION_GAP,
)


class UiBuildMixin:
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
        file_menu.add_command(label="Ouvrir le dossier de la base", command=self.open_db_folder)
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
        self.theme_toggle_frame = ttk.Frame(self.top_bar)
        self.theme_toggle_frame.grid(row=0, column=2, padx=(0, 8), sticky="e")
        status_tab = ttk.Frame(self.theme_toggle_frame, style="StatusTab.TFrame", padding=(12, 6, 12, 5))
        status_tab.grid(row=0, column=0, padx=(0, 8), sticky=W)
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
        theme_mode_frame = ttk.Frame(self.theme_toggle_frame, style="MainTabBar.TFrame", padding=(0, 6, 0, 5))
        theme_mode_frame.grid(row=0, column=1, sticky=W)
        self.theme_light_label = ttk.Label(theme_mode_frame, text="Clair", style="ThemeToggle.TLabel", cursor="hand2")
        self.theme_light_label.grid(row=0, column=0, sticky=W)
        self.theme_light_label.bind("<Button-1>", lambda _event: self._set_theme("light"))
        self.theme_separator_label = ttk.Label(theme_mode_frame, text=" | ", style="ThemeToggleSep.TLabel")
        self.theme_separator_label.grid(row=0, column=1, sticky=W)
        self.theme_dark_label = ttk.Label(theme_mode_frame, text="Sombre", style="ThemeToggle.TLabel", cursor="hand2")
        self.theme_dark_label.grid(row=0, column=2, sticky=W)
        self.theme_dark_label.bind("<Button-1>", lambda _event: self._set_theme("dark"))

        content = ttk.Frame(self.root)
        content.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        tabs_row = ttk.Frame(content)
        tabs_row.grid(row=0, column=0, sticky="nsew")
        tabs_row.columnconfigure(0, weight=1)
        tabs_row.rowconfigure(0, weight=0)
        tabs_row.rowconfigure(1, weight=1)

        tabs_header = ttk.Frame(tabs_row, style="MainTabBar.TFrame")
        tabs_header.grid(row=0, column=0, sticky="ew", pady=(0, NOTEBOOK_TAB_SECTION_GAP))
        tabs_header.columnconfigure(0, weight=1)

        tab_button_bar = ttk.Frame(tabs_header, style="MainTabBar.TFrame")
        tab_button_bar.grid(row=0, column=0, sticky="w")
        self.main_tab_button_bar = tab_button_bar
        self.main_tabs = None

        tabs_content = ttk.Frame(tabs_row, style="CurrentTab.TFrame")
        tabs_content.grid(row=1, column=0, sticky="nsew")
        tabs_content.columnconfigure(0, weight=1)
        tabs_content.rowconfigure(0, weight=1)

        games_tab = ttk.Frame(tabs_content, style="CurrentTab.TFrame")
        recent_tab = ttk.Frame(tabs_content, style="CurrentTab.TFrame")
        current_tab = ttk.Frame(tabs_content, style="CurrentTab.TFrame")
        games_tab.rowconfigure(0, weight=1)
        games_tab.columnconfigure(0, weight=1)
        recent_tab.rowconfigure(0, weight=1)
        recent_tab.columnconfigure(0, weight=1)
        current_tab.rowconfigure(0, weight=1)
        current_tab.columnconfigure(0, weight=1)
        current_tab.grid(row=0, column=0, sticky="nsew")
        games_tab.grid(row=0, column=0, sticky="nsew")
        recent_tab.grid(row=0, column=0, sticky="nsew")
        self.main_tab_frames = {
            MAIN_TAB_CURRENT: current_tab,
            MAIN_TAB_GAMES: games_tab,
            MAIN_TAB_RECENT: recent_tab,
        }
        self.main_tab_buttons = {}
        for index, tab_key in enumerate(MAIN_TAB_ORDER):
            tab_button = ttk.Button(
                tab_button_bar,
                text=MAIN_TAB_LABELS[tab_key],
                style="TButton",
                command=lambda key=tab_key: self._on_main_tab_button_press(key),
            )
            right_gap = NOTEBOOK_TAB_SECTION_GAP if index < (len(MAIN_TAB_ORDER) - 1) else 0
            tab_button.grid(row=0, column=index, sticky="w", padx=(0, right_gap))
            if tab_key in MAIN_TAB_MAINTENANCE_KEYS:
                tab_button.state(["disabled"])
                tab_button.bind("<Enter>", self._on_maintenance_tab_button_enter)
                tab_button.bind("<Motion>", self._on_maintenance_tab_button_motion)
                tab_button.bind("<Leave>", self._on_maintenance_tab_button_leave)
            self.main_tab_buttons[tab_key] = tab_button
        self.game_tree = self._build_games_table(games_tab)
        self.recent_tree = self._build_recent_table(recent_tab)
        self._build_current_game_tab(current_tab)
        self._select_main_tab(MAIN_TAB_CURRENT, force=True)
        self._refresh_emulator_status_tab()

        self.status_bar = ttk.Frame(self.root)
        self.status_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.status_bar.rowconfigure(0, weight=1)
        self.status_bar.columnconfigure(0, weight=1)
        self.status_bar.columnconfigure(1, weight=0)
        self.status_bar.columnconfigure(2, weight=0)
        self.status_label = ttk.Label(self.status_bar, textvariable=self.status_text, style="StatusDefault.TLabel")
        self.status_label.grid(row=0, column=0, sticky="w")
        self.performance_timer_label = ttk.Label(
            self.status_bar,
            textvariable=self.performance_timer_text,
            style="StatusMuted.TLabel",
        )
        self.performance_timer_label.grid(row=0, column=1, sticky="e", padx=(0, 8))
        self.version_label = ttk.Label(self.status_bar, text=f"v{APP_VERSION}")
        self.version_label.grid(row=0, column=2, sticky="e")
        self._refresh_performance_timer_text()
        self._apply_responsive_layout(self.root.winfo_width())

    # Method: _build_games_table - Construit les composants d'interface concernés.
    def _build_games_table(self, parent: ttk.Frame) -> ttk.Treeview:
        container = ttk.Frame(parent, style="CurrentTab.TFrame")
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        columns = ("title", "console", "hardcore", "pct", "status", "updated")
        table = ttk.Treeview(container, columns=columns, show="headings", style="Borderless.Treeview")
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
        table = ttk.Treeview(container, columns=columns, show="headings", style="Borderless.Treeview")
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
        section_outer_padx = 0
        section_gap = 10
        section_inner_padding = (14, 14, 14, 12)
        field_gap_y = 3

        summary = ttk.LabelFrame(
            container,
            text="Résumé du jeu en cours",
            style="CurrentSummary.TLabelframe",
            padding=section_inner_padding,
        )
        summary.grid(row=0, column=0, sticky="ew", padx=(section_outer_padx, section_outer_padx), pady=(0, section_gap))
        summary.columnconfigure(0, weight=0)
        summary.columnconfigure(1, weight=0)
        summary.columnconfigure(2, weight=1)
        summary.grid_columnconfigure(1, minsize=124)
        summary_fields = [
            ("Jeu", self.current_game_title),
            ("Console", self.current_game_console),
            ("Progression", self.current_game_progress),
            ("Dernier succès", self.current_game_last_unlock),
        ]
        cover = ttk.Frame(summary, style="CurrentSummary.TFrame", padding=(0, 2, 12, 2))
        cover.grid(row=0, column=0, rowspan=len(summary_fields), sticky="nw")
        cover.columnconfigure(0, weight=1)
        cover_label = ttk.Label(
            cover,
            text="Image indisponible",
            style="CurrentSummary.TLabel",
            anchor="center",
            justify="center",
        )
        cover_label.grid(row=0, column=0, sticky="nsew")
        self.current_game_image_labels = {"boxart": cover_label}
        self._track_rounded_image_widget(cover_label, COVER_IMAGE_CORNER_RADIUS)

        for row, (label, var) in enumerate(summary_fields):
            ttk.Label(summary, text=f"{label} :", style="CurrentSummary.TLabel").grid(
                row=row,
                column=1,
                sticky=W,
                padx=(0, 10),
                pady=field_gap_y,
            )
            if label == "Jeu":
                title_label = ttk.Label(
                    summary,
                    textvariable=var,
                    style="CurrentGameTitle.TLabel",
                    justify="left",
                    anchor="w",
                    wraplength=520,
                )
                title_label.grid(row=row, column=2, sticky="ew", padx=(0, 0), pady=field_gap_y)
                self.current_game_title_value_label = title_label
            else:
                ttk.Label(summary, textvariable=var, style="CurrentSummary.TLabel").grid(
                    row=row,
                    column=2,
                    sticky="ew",
                    padx=(0, 0),
                    pady=field_gap_y,
                )

        next_achievement = ttk.LabelFrame(
            container,
            text="Succès à débloquer",
            style="CurrentNext.TLabelframe",
            padding=section_inner_padding,
        )
        next_achievement.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=(section_outer_padx, section_outer_padx),
            pady=(0, section_gap),
        )
        next_achievement.columnconfigure(0, weight=1)
        next_achievement.columnconfigure(1, weight=1)
        next_achievement.rowconfigure(0, weight=1)

        next_content = ttk.Frame(next_achievement, style="CurrentNext.TFrame")
        next_content.grid(row=0, column=0, columnspan=2, sticky="")
        next_content.columnconfigure(0, weight=0)
        next_content.columnconfigure(1, weight=0)

        next_badge_frame = ttk.Frame(next_content, style="CurrentNext.TFrame", padding=(0, 0, 12, 0))
        next_badge_frame.grid(row=0, column=0, sticky="")
        next_badge_label = ttk.Label(
            next_badge_frame,
            text="Image indisponible",
            style="CurrentNext.TLabel",
            anchor="center",
            justify="center",
        )
        next_badge_label.grid(row=0, column=0, sticky="nsew")
        self.current_game_image_labels["next_badge"] = next_badge_label
        self._track_rounded_image_widget(next_badge_label, BADGE_IMAGE_CORNER_RADIUS)

        next_info = ttk.Frame(next_content, style="CurrentNext.TFrame", padding=(0, 0, 0, 0))
        next_info.grid(row=0, column=1, sticky="n")
        next_info.columnconfigure(0, weight=1)
        ttk.Label(next_info, textvariable=self.current_game_next_achievement_title, style="CurrentNext.TLabel").grid(
            row=0, column=0, sticky="ew", padx=(0, 0), pady=field_gap_y
        )
        desc_label = ttk.Label(
            next_info,
            textvariable=self.current_game_next_achievement_description,
            style="CurrentNext.TLabel",
            justify="left",
            anchor="w",
            wraplength=560,
        )
        desc_label.grid(row=1, column=0, sticky="ew", padx=(0, 0), pady=field_gap_y)
        self.current_game_next_achievement_desc_label = desc_label
        ttk.Label(
            next_info,
            textvariable=self.current_game_next_achievement_points,
            style="CurrentNext.TLabel",
        ).grid(row=2, column=0, sticky="ew", padx=(0, 0), pady=field_gap_y)
        ttk.Label(
            next_info,
            textvariable=self.current_game_next_achievement_feasibility,
            style="CurrentNext.TLabel",
        ).grid(row=3, column=0, sticky="ew", padx=(0, 0), pady=field_gap_y)
        nav_row = ttk.Frame(next_achievement, style="CurrentNext.TFrame")
        nav_row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, 0), pady=(10, 0))
        nav_row.columnconfigure(0, weight=0)
        nav_row.columnconfigure(1, weight=1, minsize=14)
        nav_row.columnconfigure(2, weight=0)
        nav_row.columnconfigure(3, weight=0)
        order_button = ttk.Button(
            nav_row,
            textvariable=self.current_game_achievement_order_label,
            command=self._cycle_current_game_achievement_order_mode,
            state="disabled",
            style="CurrentNext.TButton",
        )
        order_button.grid(row=0, column=0, sticky="w", padx=(0, 0))
        self.current_game_achievement_order_button = order_button
        previous_button = ttk.Button(
            nav_row,
            text="Précédent",
            command=self._show_previous_locked_achievement,
            state="disabled",
            style="CurrentNext.TButton",
        )
        previous_button.grid(row=0, column=2, sticky="e", padx=(0, 6))
        self.current_game_previous_achievement_button = previous_button
        next_button = ttk.Button(
            nav_row,
            text="Suivant",
            command=self._show_next_locked_achievement,
            state="disabled",
            style="CurrentNext.TButton",
        )
        next_button.grid(row=0, column=3, sticky="e", padx=(0, 0))
        self.current_game_next_achievement_button = next_button

        all_achievements = ttk.LabelFrame(
            container,
            text="Tous les succès du jeu en cours",
            style="CurrentGallery.TLabelframe",
            padding=section_inner_padding,
        )
        all_achievements.grid(row=2, column=0, sticky="nsew", padx=(section_outer_padx, section_outer_padx), pady=(0, 8))
        all_achievements.columnconfigure(0, weight=1)
        all_achievements.rowconfigure(1, weight=1)
        ttk.Label(all_achievements, textvariable=self.current_game_achievements_note, style="CurrentGallery.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 0), pady=(0, 8)
        )

        gallery_host = ttk.Frame(all_achievements, style="CurrentGallery.TFrame")
        gallery_host.grid(row=1, column=0, sticky="nsew", padx=(0, 0), pady=(0, 0))
        gallery_host.columnconfigure(0, weight=1)
        gallery_host.rowconfigure(0, weight=1)

        canvas = Canvas(
            gallery_host,
            highlightthickness=0,
            bd=0,
            yscrollincrement=1,
            bg=self.theme_colors.get("current_gallery_bg", "#afbfd2"),
        )
        canvas.grid(row=0, column=0, sticky="nsew")

        inner = ttk.Frame(canvas, style="CurrentGallery.TFrame")
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

