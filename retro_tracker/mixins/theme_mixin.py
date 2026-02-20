from __future__ import annotations

from pathlib import Path
from tkinter import PhotoImage, TclError

from retro_tracker.json_store import write_json_file
from retro_tracker.paths import config_path
from retro_tracker.runtime_constants import NOTEBOOK_TAB_SECTION_GAP, THEME_MODES


class ThemeMixin:
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
            write_json_file(config_path(), values, indent=2, ensure_ascii=False)
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

    # Method: _safe_style_layout - Exécute la définition d'un layout ttk avec gestion d'erreur.
    def _safe_style_layout(self, style_name: str, layout: object) -> None:
        try:
            self.style.layout(style_name, layout)
        except TclError:
            return

    # Method: _paint_rounded_top_tab_image - Dessine une pastille d'onglet avec coins arrondis en haut.
    def _paint_rounded_top_tab_image(
        self,
        image: PhotoImage,
        color: str,
        radius: int = 10,
        side_gap: int = 3,
    ) -> None:
        width = max(1, int(image.width()))
        height = max(1, int(image.height()))
        gap = max(0, min(side_gap, max(0, (width // 2) - 1)))
        shape_width = max(2, width - (gap * 2))
        rad = max(2, min(radius, shape_width // 2, height - 1))
        image.blank()
        for y in range(height):
            left = gap
            right = (width - 1) - gap
            if y < rad:
                dy = (rad - 1) - y
                dx = int((max(0, (rad * rad) - (dy * dy))) ** 0.5)
                cut = max(0, rad - dx)
                left = gap + cut
                right = (width - 1) - gap - cut
            if right >= left:
                image.put(color, to=(left, y, right + 1, y + 1))

    # Method: _ensure_rounded_notebook_tab_element - Crée/actualise l'élément graphique des onglets arrondis.
    def _ensure_rounded_notebook_tab_element(self, normal_bg: str, selected_bg: str, disabled_bg: str) -> None:
        # Keep rounded corners with a compact tab size.
        tab_w = 32
        tab_h = 20
        tab_side_gap = max(0, NOTEBOOK_TAB_SECTION_GAP // 2)
        if self._notebook_tab_image_normal is None:
            self._notebook_tab_image_normal = PhotoImage(width=tab_w, height=tab_h)
        if self._notebook_tab_image_selected is None:
            self._notebook_tab_image_selected = PhotoImage(width=tab_w, height=tab_h)
        if self._notebook_tab_image_disabled is None:
            self._notebook_tab_image_disabled = PhotoImage(width=tab_w, height=tab_h)

        self._paint_rounded_top_tab_image(self._notebook_tab_image_normal, normal_bg, side_gap=tab_side_gap)
        self._paint_rounded_top_tab_image(self._notebook_tab_image_selected, selected_bg, side_gap=tab_side_gap)
        self._paint_rounded_top_tab_image(self._notebook_tab_image_disabled, disabled_bg, side_gap=tab_side_gap)

        element_name = "PyRARoundedTab"
        try:
            if element_name not in self.style.element_names():
                self.style.element_create(
                    element_name,
                    "image",
                    self._notebook_tab_image_normal,
                    ("selected", self._notebook_tab_image_selected),
                    ("disabled", self._notebook_tab_image_disabled),
                    border=(8, 6, 8, 0),
                    sticky="nsew",
                )
        except TclError:
            return

        self._safe_style_layout(
            "Borderless.TNotebook.Tab",
            [
                (
                    element_name,
                    {
                        "sticky": "nswe",
                        "children": [
                            (
                                "Notebook.padding",
                                {
                                    "side": "top",
                                    "sticky": "nswe",
                                    "children": [("Notebook.label", {"side": "top", "sticky": ""})],
                                },
                            )
                        ],
                    },
                )
            ],
        )

    # Method: _apply_theme - Applique les paramètres ou la transformation nécessaires.
    def _apply_theme(self, mode: str) -> None:
        if mode == "dark":
            colors = {
                "root_bg": "#1f2329",
                "panel_bg": "#2b313a",
                "text": "#e8ebef",
                "field_bg": "#262c34",
                "field_fg": "#e8ebef",
                "current_summary_bg": "#14191f",
                "current_next_bg": "#14191f",
                "current_gallery_bg": "#14191f",
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
            button_padding = (14, 8)
        else:
            colors = {
                "root_bg": "#f3f5f8",
                "panel_bg": "#ffffff",
                "text": "#1f2937",
                "field_bg": "#ffffff",
                "field_fg": "#1f2937",
                "current_summary_bg": "#bcc9da",
                "current_next_bg": "#bcc9da",
                "current_gallery_bg": "#bcc9da",
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
            button_padding = (14, 8)

        self.theme_colors = dict(colors)
        self.root.configure(bg=colors["root_bg"])
        section_bg = colors["current_summary_bg"]

        self._safe_style_configure(
            ".",
            background=colors["root_bg"],
            foreground=colors["text"],
            borderwidth=0,
            relief="flat",
        )
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
            borderwidth=0,
            relief="flat",
        )
        # Tk/ttk ne gère pas un vrai alpha transparent pour les Frames:
        # on utilise le fond racine pour un rendu visuellement transparent.
        self._safe_style_configure("MainTabBar.TFrame", background=colors["root_bg"])
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
            background=section_bg,
            foreground=title_color,
            font=("Segoe UI", 11, "bold"),
        )
        self._safe_style_configure(
            "CurrentSourceUnknown.TLabel",
            background=section_bg,
            foreground=colors["text"],
            font=("Segoe UI", 9, "bold"),
        )
        self._safe_style_configure(
            "CurrentSourceLive.TLabel",
            background=section_bg,
            foreground=source_live_color,
            font=("Segoe UI", 9, "bold"),
        )
        self._safe_style_configure(
            "CurrentSourceFallback.TLabel",
            background=section_bg,
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
        self._safe_style_configure("CurrentTab.TFrame", background=section_bg)
        self._safe_style_configure("CurrentSummary.TFrame", background=section_bg)
        self._safe_style_configure("CurrentSummary.TLabel", background=section_bg, foreground=colors["text"])
        self._safe_style_configure(
            "CurrentSummary.TLabelframe",
            background=section_bg,
            borderwidth=0,
            relief="flat",
            labelmargins=(14, 8, 14, 2),
        )
        self._safe_style_configure(
            "CurrentSummary.TLabelframe.Label",
            background=section_bg,
            foreground=colors["text"],
            font=("Segoe UI", 11, "bold"),
            padding=(10, 8, 10, 4),
        )

        self._safe_style_configure("CurrentNext.TFrame", background=section_bg)
        self._safe_style_configure("CurrentNext.TLabel", background=section_bg, foreground=colors["text"])
        self._safe_style_configure(
            "CurrentNext.TLabelframe",
            background=section_bg,
            borderwidth=0,
            relief="flat",
            labelmargins=(14, 8, 14, 2),
        )
        self._safe_style_configure(
            "CurrentNext.TLabelframe.Label",
            background=section_bg,
            foreground=colors["text"],
            font=("Segoe UI", 11, "bold"),
            padding=(10, 8, 10, 4),
        )
        self._safe_style_configure(
            "CurrentNext.Horizontal.TProgressbar",
            troughcolor=section_bg,
            background=colors["accent"],
            borderwidth=0,
            relief="flat",
        )

        self._safe_style_configure("CurrentGallery.TFrame", background=section_bg)
        self._safe_style_configure("CurrentGallery.TLabel", background=section_bg, foreground=colors["text"])
        self._safe_style_configure(
            "CurrentGallery.TLabelframe",
            background=section_bg,
            borderwidth=0,
            relief="flat",
            labelmargins=(14, 8, 14, 2),
        )
        self._safe_style_configure(
            "CurrentGallery.TLabelframe.Label",
            background=section_bg,
            foreground=colors["text"],
            font=("Segoe UI", 11, "bold"),
            padding=(10, 8, 10, 4),
        )

        self._safe_style_configure(
            "TLabelframe",
            background=colors["root_bg"],
            foreground=colors["text"],
            borderwidth=0,
            relief="flat",
        )
        self._safe_style_configure(
            "TLabelframe.Label",
            background=colors["root_bg"],
            foreground=colors["text"],
            font=("Segoe UI", 10, "bold"),
        )
        self._safe_style_configure(
            "TButton",
            background=colors["panel_bg"],
            foreground=colors["text"],
            borderwidth=0,
            relief="flat",
            focusthickness=0,
            focuscolor=colors["panel_bg"],
            padding=button_padding,
        )
        self._safe_style_map("TButton", background=[("active", colors["accent_hover"]), ("pressed", colors["accent"])])
        self._safe_style_configure(
            "CurrentNext.TButton",
            background=colors["panel_bg"],
            foreground=colors["text"],
            borderwidth=0,
            relief="flat",
            focusthickness=0,
            focuscolor=colors["panel_bg"],
            padding=button_padding,
        )
        self._safe_style_map(
            "CurrentNext.TButton",
            background=[
                ("disabled", colors["panel_bg"]),
                ("active", colors["accent_hover"]),
                ("pressed", colors["accent"]),
            ],
            foreground=[("disabled", status_muted_color)],
        )
        self._safe_style_configure("Modal.TFrame", background=colors["panel_bg"])
        self._safe_style_configure("Modal.TLabel", background=colors["panel_bg"], foreground=colors["text"])
        self._safe_style_configure(
            "Modal.TEntry",
            fieldbackground=colors["field_bg"],
            background=colors["field_bg"],
            foreground=colors["field_fg"],
            insertcolor=colors["field_fg"],
            borderwidth=0,
            fieldborderwidth=0,
            relief="flat",
            focusthickness=0,
        )
        self._safe_style_configure(
            "Modal.TButton",
            background=colors["panel_bg"],
            foreground=colors["text"],
            borderwidth=0,
            relief="flat",
            focusthickness=0,
            focuscolor=colors["panel_bg"],
            padding=button_padding,
        )
        self._safe_style_map("Modal.TButton", background=[("active", colors["accent_hover"]), ("pressed", colors["accent"])])
        self._safe_style_configure(
            "Tooltip.TLabel",
            background=colors["panel_bg"],
            foreground=colors["text"],
            borderwidth=0,
            relief="flat",
            padding=(8, 6),
        )
        self._safe_style_configure(
            "TEntry",
            fieldbackground=colors["field_bg"],
            background=colors["field_bg"],
            foreground=colors["field_fg"],
            insertcolor=colors["field_fg"],
            borderwidth=0,
            fieldborderwidth=0,
            relief="flat",
            focusthickness=0,
        )

        selected_tab_bg = section_bg
        selected_tab_fg = colors["text"]
        normal_tab_bg = colors["panel_bg"]

        self._safe_style_configure(
            "MainTab.TButton",
            background=normal_tab_bg,
            foreground=colors["text"],
            borderwidth=0,
            relief="flat",
            focusthickness=0,
            focuscolor=normal_tab_bg,
            padding=button_padding,
        )
        self._safe_style_map(
            "MainTab.TButton",
            background=[
                ("disabled", normal_tab_bg),
                ("active", colors["accent_hover"]),
                ("pressed", colors["accent"]),
            ],
            foreground=[("disabled", status_muted_color)],
        )
        self._safe_style_configure(
            "MainTabSelected.TButton",
            background=colors["panel_bg"],
            foreground=title_color,
            borderwidth=0,
            relief="flat",
            focusthickness=0,
            focuscolor=colors["panel_bg"],
            padding=button_padding,
        )
        self._safe_style_map(
            "MainTabSelected.TButton",
            background=[
                ("disabled", colors["panel_bg"]),
                ("active", colors["accent_hover"]),
                ("pressed", colors["accent"]),
            ],
            foreground=[("disabled", status_muted_color)],
        )

        self._safe_style_configure(
            "TNotebook",
            background=colors["root_bg"],
            borderwidth=0,
            relief="flat",
            padding=0,
            tabmargins=(0, 0, 0, 0),
        )
        self._safe_style_configure(
            "TNotebook.Tab",
            background=normal_tab_bg,
            foreground=colors["text"],
            padding=(16, 7),
            borderwidth=0,
            relief="flat",
        )
        self._safe_style_configure(
            "Borderless.TNotebook",
            background=colors["root_bg"],
            borderwidth=0,
            relief="flat",
            padding=0,
            tabmargins=(0, 0, 0, -2),
            lightcolor=colors["root_bg"],
            darkcolor=colors["root_bg"],
            bordercolor=colors["root_bg"],
        )
        self._safe_style_configure(
            "Borderless.TNotebook.Tab",
            background=normal_tab_bg,
            foreground=colors["text"],
            padding=(10, 4, 10, 4),
            borderwidth=0,
            relief="flat",
            focuscolor=normal_tab_bg,
            lightcolor=normal_tab_bg,
            darkcolor=normal_tab_bg,
            bordercolor=normal_tab_bg,
        )
        self._safe_style_map(
            "TNotebook.Tab",
            background=[("selected", selected_tab_bg), ("!selected", normal_tab_bg)],
            foreground=[("selected", selected_tab_fg), ("!selected", colors["text"])],
            padding=[("selected", (10, 4, 10, 4)), ("!selected", (10, 4, 10, 4)), ("disabled", (10, 4, 10, 4))],
            expand=[("selected", (0, 0, 0, 0)), ("!selected", (0, 0, 0, 0)), ("disabled", (0, 0, 0, 0))],
        )
        self._safe_style_map(
            "Borderless.TNotebook.Tab",
            background=[("selected", selected_tab_bg), ("!selected", normal_tab_bg)],
            foreground=[("selected", selected_tab_fg), ("!selected", colors["text"])],
            padding=[("selected", (10, 4, 10, 4)), ("!selected", (10, 4, 10, 4)), ("disabled", (10, 4, 10, 4))],
            expand=[("selected", (0, 0, 0, 0)), ("!selected", (0, 0, 0, 0)), ("disabled", (0, 0, 0, 0))],
            lightcolor=[("selected", selected_tab_bg), ("!selected", normal_tab_bg)],
            darkcolor=[("selected", selected_tab_bg), ("!selected", normal_tab_bg)],
            bordercolor=[("selected", selected_tab_bg), ("!selected", normal_tab_bg)],
            focuscolor=[("selected", selected_tab_bg), ("!selected", normal_tab_bg)],
        )
        self._safe_style_layout(
            "Borderless.TNotebook",
            [("Notebook.client", {"sticky": "nswe"})],
        )
        self._ensure_rounded_notebook_tab_element(
            normal_bg=normal_tab_bg,
            selected_bg=selected_tab_bg,
            disabled_bg=normal_tab_bg,
        )

        self._safe_style_configure(
            "Treeview",
            background=colors["field_bg"],
            fieldbackground=colors["field_bg"],
            foreground=colors["field_fg"],
            rowheight=24,
            borderwidth=0,
            relief="flat",
        )
        self._safe_style_configure(
            "Treeview.Heading",
            background=colors["panel_bg"],
            foreground=colors["text"],
            borderwidth=0,
            relief="flat",
        )
        self._safe_style_configure("TScrollbar", borderwidth=0, relief="flat", troughcolor=colors["root_bg"])
        self._safe_style_configure("Horizontal.TProgressbar", borderwidth=0, relief="flat")
        self._safe_style_configure("Vertical.TProgressbar", borderwidth=0, relief="flat")
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
        self._safe_style_configure(
            "Borderless.Treeview",
            background=colors["field_bg"],
            fieldbackground=colors["field_bg"],
            foreground=colors["field_fg"],
            rowheight=24,
            borderwidth=0,
            relief="flat",
        )
        self._safe_style_configure(
            "Borderless.Treeview.Heading",
            background=colors["panel_bg"],
            foreground=colors["text"],
            borderwidth=0,
            relief="flat",
        )
        self._safe_style_map(
            "Borderless.Treeview.Heading",
            background=[("active", colors["panel_bg"]), ("pressed", colors["accent"])],
            foreground=[("active", colors["text"]), ("pressed", "#ffffff")],
        )
        self._safe_style_map(
            "Borderless.Treeview",
            background=[("selected", colors["selected_bg"])],
            foreground=[("selected", colors["selected_fg"])],
        )
        self._safe_style_layout(
            "Borderless.Treeview",
            [("Treeview.treearea", {"sticky": "nswe"})],
        )
        self._safe_style_layout(
            "Borderless.Treeview.Heading",
            [
                (
                    "Treeheading.cell",
                    {
                        "sticky": "nswe",
                        "children": [
                            (
                                "Treeheading.padding",
                                {
                                    "sticky": "nswe",
                                    "children": [
                                        ("Treeheading.image", {"side": "right", "sticky": ""}),
                                        ("Treeheading.text", {"sticky": "we"}),
                                    ],
                                },
                            )
                        ],
                    },
                )
            ],
        )

        if self.connection_window is not None and self.connection_window.winfo_exists():
            self.connection_window.configure(bg=colors["panel_bg"], bd=0, highlightthickness=0, padx=0, pady=0)
        if self.profile_window is not None and self.profile_window.winfo_exists():
            self.profile_window.configure(bg=colors["root_bg"])
        if self.current_game_achievements_canvas is not None:
            try:
                self.current_game_achievements_canvas.configure(bg=colors["current_gallery_bg"])
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
        self._set_current_game_source(self.current_game_source.get())
        self._refresh_emulator_status_tab()
        modal = self._active_modal_window()
        if modal is not None:
            self._sync_modal_overlay()
            self._center_modal_window(modal)
        self.root.after_idle(self._apply_rounded_corners_to_widget_tree)

