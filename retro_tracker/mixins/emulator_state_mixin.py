from __future__ import annotations

import threading
import time
from tkinter import TclError

from retro_tracker.emulator_process import (
    collect_ra_emulator_window_titles_by_probe,
    detect_ra_emulator_game_probe_states,
    detect_ra_emulator_probe_matches,
)
from retro_tracker.measured_runtime_probe import probe_runtime_measured_progress
from retro_tracker.runtime_constants import (
    EMULATOR_POLL_IMMEDIATE_INTERVAL_MS,
    EMULATOR_POLL_INTERVAL_MS,
    EMULATOR_GAME_LOADED_CONFIRMATION_COUNT,
    EMULATOR_GAME_UNLOADED_CONFIRMATION_COUNT,
    EMULATOR_STATE_CONFIRMATION_COUNT,
    EVENT_SYNC_IDLE_MIN_GAP_MS,
    EVENT_SYNC_LIVE_MIN_GAP_MS,
    EMULATOR_STATUS_EMULATOR_LOADED,
    EMULATOR_STATUS_GAME_LOADED,
    EMULATOR_STATUS_INACTIVE,
    EMULATOR_STATUS_REFRESH_DEBOUNCE_MS,
    EMULATOR_STATUS_REFRESH_MIN_GAP_MS,
)


class EmulatorStateMixin:
    # Method: _probe_each_emulator - Émet une sonde dédiée pour chaque émulateur supporté.
    def _probe_each_emulator(self, probe_matches: dict[str, list[str]], stage: str) -> None:
        for emulator_name in sorted(probe_matches.keys()):
            matches = probe_matches.get(emulator_name, [])
            self._probe(
                f"emulator_process_{emulator_name}",
                stage=stage,
                live=bool(matches),
                matches=", ".join(matches[:3]) if matches else "-",
            )

    # Method: _probe_each_emulator_game_load - Émet une sonde "jeu chargé" pour chaque émulateur.
    def _probe_each_emulator_game_load(
        self,
        probe_matches: dict[str, list[str]],
        stage: str,
        game_probe_states: dict[str, bool] | None = None,
        game_probe_titles: dict[str, list[str]] | None = None,
    ) -> None:
        ui_status = self.emulator_status_text.get().strip().casefold()
        source_value = ""
        source_live = False
        runtime_states = game_probe_states if isinstance(game_probe_states, dict) else {}
        title_states = game_probe_titles if isinstance(game_probe_titles, dict) else {}
        try:
            source_value = self.current_game_source.get().strip()  # type: ignore[attr-defined]
        except Exception:
            source_value = ""
        try:
            source_live = bool(self._is_live_source_label(source_value))  # type: ignore[attr-defined]
        except Exception:
            source_live = False

        game_loaded_global = (ui_status == EMULATOR_STATUS_GAME_LOADED.casefold()) or source_live
        active_emulators = sorted(name for name, matches in probe_matches.items() if bool(matches))
        single_active = active_emulators[0] if len(active_emulators) == 1 else ""

        for emulator_name in sorted(probe_matches.keys()):
            matches = probe_matches.get(emulator_name, [])
            live = bool(matches)
            runtime_loaded = bool(runtime_states.get(emulator_name, False))
            if not live:
                state = "inactive"
                game_loaded = False
                confidence = "high"
                source_hint = source_value or "-"
            elif runtime_loaded:
                state = "game_loaded"
                game_loaded = True
                confidence = "high"
                source_hint = "Sonde runtime émulateur"
            elif not game_loaded_global:
                state = "emulator_loaded"
                game_loaded = False
                confidence = "high"
                source_hint = source_value or "-"
            elif single_active:
                game_loaded = (single_active == emulator_name)
                state = "game_loaded" if game_loaded else "emulator_loaded"
                confidence = "high"
                source_hint = source_value or "-"
            else:
                game_loaded = True
                state = "ambiguous"
                confidence = "low"
                source_hint = source_value or "-"

            self._probe(
                f"emulator_game_probe_{emulator_name}",
                stage=stage,
                state=state,
                live=live,
                game_loaded=game_loaded,
                runtime_loaded=runtime_loaded,
                confidence=confidence,
                source=source_hint,
                matches=", ".join(matches[:3]) if matches else "-",
                titles=" || ".join(title_states.get(emulator_name, [])[:3]) if title_states.get(emulator_name) else "-",
            )

    # Method: _probe_each_emulator_achievement_unlock - Émet une sonde dédiée "succès débloqué" pour chaque émulateur.
    def _probe_each_emulator_achievement_unlock(
        self,
        probe_matches: dict[str, list[str]],
        stage: str,
        unlocked: bool,
        achievement_id: int = 0,
        game_id: int = 0,
        title: str = "",
        unlock_marker: str = "",
    ) -> None:
        active_emulators = sorted(name for name, matches in probe_matches.items() if bool(matches))
        single_active = active_emulators[0] if len(active_emulators) == 1 else ""
        for emulator_name in sorted(probe_matches.keys()):
            matches = probe_matches.get(emulator_name, [])
            live = bool(matches)
            if not unlocked:
                state = "idle"
                unlocked_here = False
                confidence = "high"
            elif not live:
                state = "inactive"
                unlocked_here = False
                confidence = "high"
            elif single_active:
                unlocked_here = single_active == emulator_name
                state = "unlocked" if unlocked_here else "other_active"
                confidence = "high"
            else:
                unlocked_here = True
                state = "ambiguous"
                confidence = "low"

            self._probe(
                f"emulator_achievement_probe_{emulator_name}",
                stage=stage,
                state=state,
                live=live,
                unlocked=unlocked_here,
                confidence=confidence,
                achievement_id=achievement_id,
                game_id=game_id,
                title=title or "-",
                unlock_marker=bool(unlock_marker),
                matches=", ".join(matches[:3]) if matches else "-",
            )

    # Method: _probe_each_emulator_measured - Émet une sonde dédiée "measured runtime" pour chaque émulateur.
    def _probe_each_emulator_measured(
        self,
        probe_matches: dict[str, list[str]],
        stage: str,
        measured_event: dict[str, str] | None,
    ) -> None:
        measured_emulator = ""
        measured_id = 0
        measured_percent = ""
        measured_source = ""
        if isinstance(measured_event, dict):
            measured_emulator = str(measured_event.get("emulator", "")).strip().casefold()
            measured_id = self._safe_int(measured_event.get("achievement_id"))  # type: ignore[attr-defined]
            measured_percent = str(measured_event.get("measured_percent", "")).strip()
            measured_source = str(measured_event.get("source", "")).strip()

        for emulator_name in sorted(probe_matches.keys()):
            matches = probe_matches.get(emulator_name, [])
            live = bool(matches)
            has_measured = live and measured_emulator == emulator_name.casefold() and bool(measured_event)
            state = "measured" if has_measured else ("idle" if live else "inactive")
            self._probe(
                f"emulator_measured_probe_{emulator_name}",
                stage=stage,
                state=state,
                live=live,
                measured=has_measured,
                achievement_id=(measured_id if has_measured else 0),
                percent=(measured_percent if has_measured else "-"),
                source=(measured_source if has_measured else "-"),
                matches=", ".join(matches[:3]) if matches else "-",
            )

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

    # Method: _is_emulator_process_live - Retourne le dernier état détecté des processus émulateur.
    def _is_emulator_process_live(self) -> bool:
        return bool(self._last_emulator_probe_live)

    # Method: _set_emulator_status_text - Met à jour le libellé du statut sans déclencher de logique métier.
    def _set_emulator_status_text(self, status_text: str) -> None:
        previous = self.emulator_status_text.get().strip()
        self.emulator_status_text.set(status_text)
        self._refresh_emulator_status_tab()
        if previous != status_text:
            self._probe("status_text_change", previous=previous or "-", new=status_text)
            if not self.is_closing:
                # Garantit une actualisation après chaque transition de mode.
                force_refresh = status_text.strip().casefold() == EMULATOR_STATUS_GAME_LOADED.casefold()
                self._schedule_emulator_status_refresh(force_current_game_refresh=force_refresh)

    # Method: _prime_emulator_status_on_startup - Initialise l'état Live/Inactif dès l'ouverture de l'application.
    def _prime_emulator_status_on_startup(self) -> None:
        is_live = False
        probe_matches: dict[str, list[str]] = {}
        game_probe_states: dict[str, bool] = {}
        game_probe_titles: dict[str, list[str]] = {}
        try:
            probe_matches = detect_ra_emulator_probe_matches()
            is_live = any(bool(matches) for matches in probe_matches.values())
            game_probe_titles = collect_ra_emulator_window_titles_by_probe(probe_matches=probe_matches)
            game_probe_states = detect_ra_emulator_game_probe_states(
                probe_matches=probe_matches,
                window_titles_by_probe=game_probe_titles,
            )
        except Exception:
            is_live = False
            probe_matches = {}
            game_probe_states = {}
            game_probe_titles = {}
        if probe_matches:
            self._last_emulator_probe_matches = {name: list(matches) for name, matches in probe_matches.items()}
            self._last_emulator_probe_game_load_states = dict(game_probe_states)
            self._last_emulator_probe_window_titles = {name: list(titles) for name, titles in game_probe_titles.items()}
            self._probe_each_emulator(probe_matches, stage="startup")
            self._probe_each_emulator_game_load(
                probe_matches,
                stage="startup",
                game_probe_states=game_probe_states,
                game_probe_titles=game_probe_titles,
            )
        self._last_emulator_probe_live = is_live
        runtime_game_loaded = any(
            bool(game_probe_states.get(name, False)) and bool(matches)
            for name, matches in probe_matches.items()
        )
        self._runtime_game_loaded_confirmed = bool(runtime_game_loaded and is_live)
        self._runtime_game_loaded_candidate = None
        self._runtime_game_loaded_candidate_count = 0
        if not is_live:
            next_status = EMULATOR_STATUS_INACTIVE
        else:
            next_status = EMULATOR_STATUS_GAME_LOADED if runtime_game_loaded else EMULATOR_STATUS_EMULATOR_LOADED
        self._set_emulator_status_text(next_status)
        self._emulator_probe_candidate_live = None
        self._emulator_probe_candidate_count = 0
        self._debug_log(f"_prime_emulator_status_on_startup status='{next_status}'")

    # Method: _set_emulator_status - Met à jour le statut Live/Inactif affiché près du sélecteur de thème.
    def _set_emulator_status(self, is_live: bool) -> None:
        previous = self.emulator_status_text.get().strip()
        if is_live:
            next_status = EMULATOR_STATUS_EMULATOR_LOADED
        else:
            # Règle demandée: fermeture émulateur => Inactif immédiatement,
            # même si l'état précédent était "Jeu chargé".
            next_status = EMULATOR_STATUS_INACTIVE
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
            self._schedule_emulator_status_refresh(force_current_game_refresh=live_transition)

    # Method: _schedule_emulator_status_refresh - Regroupe les rafraichissements declenches par le statut emulation.
    def _schedule_emulator_status_refresh(self, force_current_game_refresh: bool = False) -> None:
        if self.is_closing:
            return
        if force_current_game_refresh:
            self._pending_emulator_status_force_refresh = True
        if self.emulator_status_refresh_job is not None:
            return
        delay_ms = EMULATOR_STATUS_REFRESH_DEBOUNCE_MS
        if self._last_emulator_status_refresh_monotonic > 0.0:
            elapsed_ms = (time.monotonic() - self._last_emulator_status_refresh_monotonic) * 1000.0
            if elapsed_ms < float(EMULATOR_STATUS_REFRESH_MIN_GAP_MS):
                delay_ms = max(delay_ms, int(EMULATOR_STATUS_REFRESH_MIN_GAP_MS - elapsed_ms))
        try:
            self.emulator_status_refresh_job = self.root.after(delay_ms, self._run_emulator_status_refresh)
        except TclError:
            self.emulator_status_refresh_job = None

    # Method: _run_emulator_status_refresh - Execute un rafraichissement unique apres changements de statut emulateur.
    def _run_emulator_status_refresh(self) -> None:
        self.emulator_status_refresh_job = None
        if self.is_closing:
            self._pending_emulator_status_force_refresh = False
            return
        self._begin_transition_timer()
        force_current_game_refresh = self._pending_emulator_status_force_refresh
        self._pending_emulator_status_force_refresh = False
        if self.current_game_fetch_in_progress:
            self._pending_emulator_status_force_refresh = (
                self._pending_emulator_status_force_refresh or force_current_game_refresh
            )
            try:
                self.emulator_status_refresh_job = self.root.after(120, self._run_emulator_status_refresh)
            except TclError:
                self.emulator_status_refresh_job = None
            return
        self._last_emulator_status_refresh_monotonic = time.monotonic()
        self.refresh_dashboard(
            show_errors=False,
            sync_before_refresh=False,
            force_current_game_refresh=force_current_game_refresh,
        )
        if not self.current_game_fetch_in_progress:
            self._end_transition_timer()

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
        delay = EMULATOR_POLL_IMMEDIATE_INTERVAL_MS if immediate else EMULATOR_POLL_INTERVAL_MS
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
        probe_matches: dict[str, list[str]] = {}
        game_probe_states: dict[str, bool] = {}
        game_probe_titles: dict[str, list[str]] = {}
        measured_state: dict[str, object] = {}
        measured_event: dict[str, str] | None = None
        try:
            probe_matches = detect_ra_emulator_probe_matches()
            is_live = any(bool(matches) for matches in probe_matches.values())
            game_probe_titles = collect_ra_emulator_window_titles_by_probe(probe_matches=probe_matches)
            game_probe_states = detect_ra_emulator_game_probe_states(
                probe_matches=probe_matches,
                window_titles_by_probe=game_probe_titles,
            )
        except Exception:
            is_live = False
            probe_matches = {}
            game_probe_states = {}
            game_probe_titles = {}
        state_snapshot = self._measured_probe_state if isinstance(self._measured_probe_state, dict) else {}
        try:
            measured_state, measured_event = probe_runtime_measured_progress(probe_matches, state=state_snapshot)
        except Exception:
            measured_state = dict(state_snapshot)
            measured_event = None
        self._queue_ui_callback(
            lambda live=is_live, matches=probe_matches, g_states=game_probe_states, g_titles=game_probe_titles, m_state=measured_state, m_event=measured_event: self._on_emulator_probe_result(
                live,
                matches,
                g_states,
                g_titles,
                m_state,
                m_event,
            )
        )

    # Method: _on_emulator_probe_result - Applique le résultat de détection et relance le polling.
    def _on_emulator_probe_result(
        self,
        is_live: bool,
        probe_matches: dict[str, list[str]] | None = None,
        game_probe_states: dict[str, bool] | None = None,
        game_probe_titles: dict[str, list[str]] | None = None,
        measured_state: dict[str, object] | None = None,
        measured_event: dict[str, str] | None = None,
    ) -> None:
        self.emulator_probe_in_progress = False
        self._last_emulator_probe_live = is_live
        if isinstance(measured_state, dict):
            self._measured_probe_state = measured_state
        runtime_states = game_probe_states if isinstance(game_probe_states, dict) else {}
        title_states = game_probe_titles if isinstance(game_probe_titles, dict) else {}
        self._last_emulator_probe_game_load_states = dict(runtime_states)
        self._last_emulator_probe_window_titles = {name: list(titles) for name, titles in title_states.items()}
        if probe_matches:
            self._last_emulator_probe_matches = {name: list(matches) for name, matches in probe_matches.items()}
            self._probe_each_emulator(probe_matches, stage="poll")
            self._probe_each_emulator_game_load(
                probe_matches,
                stage="poll",
                game_probe_states=runtime_states,
                game_probe_titles=title_states,
            )
            self._probe_each_emulator_measured(probe_matches, stage="poll", measured_event=measured_event)
        self._on_runtime_measured_probe_result(measured_event)  # type: ignore[attr-defined]
        status_before = self.emulator_status_text.get().strip().casefold()
        runtime_game_loaded = False
        if probe_matches:
            runtime_game_loaded = any(
                bool(runtime_states.get(name, False)) and bool(matches)
                for name, matches in probe_matches.items()
            )
        runtime_game_loaded_raw = bool(runtime_game_loaded and is_live)
        if not is_live:
            self._runtime_game_loaded_confirmed = False
            self._runtime_game_loaded_candidate = None
            self._runtime_game_loaded_candidate_count = 0
        elif runtime_game_loaded_raw == bool(self._runtime_game_loaded_confirmed):
            self._runtime_game_loaded_candidate = None
            self._runtime_game_loaded_candidate_count = 0
        else:
            if self._runtime_game_loaded_candidate != runtime_game_loaded_raw:
                self._runtime_game_loaded_candidate = runtime_game_loaded_raw
                self._runtime_game_loaded_candidate_count = 1
            else:
                self._runtime_game_loaded_candidate_count += 1
            required_runtime_count = (
                EMULATOR_GAME_LOADED_CONFIRMATION_COUNT
                if runtime_game_loaded_raw
                else EMULATOR_GAME_UNLOADED_CONFIRMATION_COUNT
            )
            if self._runtime_game_loaded_candidate_count >= required_runtime_count:
                self._runtime_game_loaded_confirmed = runtime_game_loaded_raw
                self._runtime_game_loaded_candidate = None
                self._runtime_game_loaded_candidate_count = 0
        runtime_game_loaded = bool(self._runtime_game_loaded_confirmed and is_live)
        fast_reprobe_needed = False
        current_live = self._is_emulator_live()
        self._probe(
            "emulator_probe_result",
            detected_live=is_live,
            current_live=current_live,
            status_before=status_before,
            candidate_count=self._emulator_probe_candidate_count,
            runtime_game_loaded_raw=runtime_game_loaded_raw,
            runtime_game_loaded_confirmed=runtime_game_loaded,
            runtime_candidate_count=self._runtime_game_loaded_candidate_count,
            runtime_required_count=(
                EMULATOR_GAME_LOADED_CONFIRMATION_COUNT
                if runtime_game_loaded_raw
                else EMULATOR_GAME_UNLOADED_CONFIRMATION_COUNT
            ),
        )
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
                fast_reprobe_needed = is_live

        if is_live:
            status_now = self.emulator_status_text.get().strip().casefold()
            if runtime_game_loaded and status_now != EMULATOR_STATUS_GAME_LOADED.casefold():
                self._set_emulator_status_text(EMULATOR_STATUS_GAME_LOADED)
                fast_reprobe_needed = True
            elif (not runtime_game_loaded) and status_now == EMULATOR_STATUS_GAME_LOADED.casefold():
                self._set_emulator_status_text(EMULATOR_STATUS_EMULATOR_LOADED)
                fast_reprobe_needed = True

        effective_live = self._is_emulator_process_live()
        if self._has_valid_connection():
            ui_status = self.emulator_status_text.get().strip().casefold()
            if ui_status == EMULATOR_STATUS_GAME_LOADED.casefold():
                min_gap_ms = EVENT_SYNC_LIVE_MIN_GAP_MS
            else:
                min_gap_ms = EVENT_SYNC_LIVE_MIN_GAP_MS if effective_live else EVENT_SYNC_IDLE_MIN_GAP_MS
            self._request_event_sync_throttled(
                "surveillance changements",
                delay_ms=120,
                min_gap_ms=min_gap_ms,
            )
        status_after = self.emulator_status_text.get().strip().casefold()
        if status_before != status_after and status_after == EMULATOR_STATUS_EMULATOR_LOADED.casefold():
            fast_reprobe_needed = True
        self._probe(
            "emulator_probe_apply",
            status_before=status_before,
            status_after=status_after,
            fast_reprobe=fast_reprobe_needed,
            effective_live=effective_live,
            runtime_game_loaded=runtime_game_loaded,
        )
        self._restart_emulator_probe(immediate=fast_reprobe_needed)

