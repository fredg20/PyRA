from __future__ import annotations

import base64
import os
import sqlite3
from pathlib import Path
from tkinter import TclError, messagebox

from retro_tracker.json_store import read_json_file, write_json_file
from retro_tracker.paths import config_path, current_game_cache_path, default_tracker_db_path
from retro_tracker.ra_api import RetroAPIError
from retro_tracker.runtime_constants import (
    ACHIEVEMENT_ORDER_NORMAL,
    SAVED_WINDOW_GEOMETRY_RETRY_COUNT,
    SAVED_WINDOW_GEOMETRY_RETRY_DELAY_MS,
    WINDOW_GEOMETRY_RE,
)


class ConfigPersistenceMixin:
    def _load_config(self) -> None:
        defaults = {
            "api_key": os.getenv("RA_API_KEY", ""),
            "api_username": os.getenv("RA_API_USERNAME", ""),
            "tracked_username": os.getenv("TRACKED_USERNAME", ""),
            "db_path": os.getenv("TRACKER_DB_PATH", str(default_tracker_db_path())),
            "theme_mode": os.getenv("PYRA_THEME_MODE", "light"),
            "window_geometry": "",
        }
        self.has_saved_connection_record = False

        file_path = config_path()
        if file_path.exists():
            try:
                data = read_json_file(file_path)
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
        self.root.after_idle(lambda: self._schedule_saved_window_geometry_apply(defaults.get("window_geometry", "")))

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
            write_json_file(path, cache_payload, ensure_ascii=False, indent=2)
            self._debug_log(
                f"_persist_current_game_cache saved game_id={key[1]} username='{key[0]}' path='{path}'"
            )
        except OSError as exc:
            self._debug_log(f"_persist_current_game_cache error: {exc}")

    # Method: _persist_current_game_cache_after_inactive_transition_if_needed - Sauvegarde le cache après transition Live -> Inactif.
    def _persist_current_game_cache_after_inactive_transition_if_needed(self, source_value: str = "") -> None:
        if not self.persist_current_game_cache_on_inactive_transition:
            return
        if self._is_live_source_label(source_value):
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
            raw = read_json_file(path)
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
        preferred_next = (
            next_achievement if self.current_game_achievement_order_mode == ACHIEVEMENT_ORDER_NORMAL else None
        )
        self._sync_locked_achievement_navigation(achievements, preferred_next)
        self.prefer_persisted_current_game_on_startup = True
        self._debug_log(
            f"_load_persisted_current_game_cache restored game_id={game_id} username='{key_username}'"
        )

    # Method: _cancel_saved_window_geometry_apply_job - Annule la réapplication différée de la géométrie sauvegardée.
    def _cancel_saved_window_geometry_apply_job(self) -> None:
        if self.saved_window_geometry_apply_job is None:
            return
        try:
            self.root.after_cancel(self.saved_window_geometry_apply_job)
        except TclError:
            pass
        self.saved_window_geometry_apply_job = None

    # Method: _schedule_saved_window_geometry_apply - Planifie plusieurs applications pour fiabiliser la position au démarrage.
    def _schedule_saved_window_geometry_apply(self, geometry_value: str) -> None:
        geometry = str(geometry_value).strip()
        self._cancel_saved_window_geometry_apply_job()
        if not geometry or not WINDOW_GEOMETRY_RE.fullmatch(geometry):
            self._saved_window_geometry_pending = ""
            self._saved_window_geometry_retry_remaining = 0
            return
        self._saved_window_geometry_pending = geometry
        self._saved_window_geometry_retry_remaining = SAVED_WINDOW_GEOMETRY_RETRY_COUNT
        self._apply_saved_window_geometry(geometry)
        self.saved_window_geometry_apply_job = self.root.after(
            SAVED_WINDOW_GEOMETRY_RETRY_DELAY_MS,
            self._reapply_saved_window_geometry_if_needed,
        )

    # Method: _reapply_saved_window_geometry_if_needed - Réapplique brièvement la géométrie pour contrer un repositionnement tardif.
    def _reapply_saved_window_geometry_if_needed(self) -> None:
        self.saved_window_geometry_apply_job = None
        geometry = self._saved_window_geometry_pending
        if not geometry:
            return
        if self._saved_window_geometry_retry_remaining <= 0:
            self._saved_window_geometry_pending = ""
            return
        self._saved_window_geometry_retry_remaining -= 1
        self._apply_saved_window_geometry(geometry)
        if self._saved_window_geometry_retry_remaining <= 0:
            self._saved_window_geometry_pending = ""
            return
        try:
            self.saved_window_geometry_apply_job = self.root.after(
                SAVED_WINDOW_GEOMETRY_RETRY_DELAY_MS,
                self._reapply_saved_window_geometry_if_needed,
            )
        except TclError:
            self.saved_window_geometry_apply_job = None
            self._saved_window_geometry_pending = ""

    # Method: _apply_saved_window_geometry - Applique les paramètres ou la transformation nécessaires.
    def _apply_saved_window_geometry(self, geometry_value: str) -> None:
        geometry = str(geometry_value).strip()
        if not geometry or not WINDOW_GEOMETRY_RE.fullmatch(geometry):
            return
        try:
            self.root.geometry(geometry)
            self.root.update_idletasks()
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
            write_json_file(config_path(), values, indent=2, ensure_ascii=False)
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
            write_json_file(config_path(), values, indent=2, ensure_ascii=False)
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
            state = self.root.state()
            if state == "iconic":
                return ""
            # wm_geometry est plus stable sur Windows quand la fenêtre est maximisée.
            geometry = self.root.wm_geometry().strip()
            if not WINDOW_GEOMETRY_RE.fullmatch(geometry):
                geometry = self.root.winfo_geometry().strip()
        except TclError:
            return ""
        if not WINDOW_GEOMETRY_RE.fullmatch(geometry):
            return ""
        return geometry

    def _save_window_geometry(self) -> None:
        values = self._config_values()
        try:
            write_json_file(config_path(), values, indent=2, ensure_ascii=False)
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
        if self.profile_button is not None:
            profile_label = self._tracked_username().strip() or "Profil"
            self.profile_button.configure(text=profile_label)
        if self.connection_button is not None:
            label = "Connecté" if self._has_saved_valid_connection() else "Connexion"
            self.connection_button.configure(text=label)


