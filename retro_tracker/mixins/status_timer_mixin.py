from __future__ import annotations

import time
from tkinter import TclError

from retro_tracker.runtime_constants import STATUS_TIMER_REFRESH_INTERVAL_MS


class StatusTimerMixin:
    def _apply_status_label_style(self, muted: bool) -> None:
        if self.status_label is None or not self.status_label.winfo_exists():
            return
        style_name = "StatusMuted.TLabel" if muted else "StatusDefault.TLabel"
        try:
            self.status_label.configure(style=style_name)
        except TclError:
            return

    # Method: _format_timer_duration - Formate une durée pour l'affichage compact de la barre d'état.
    def _format_timer_duration(self, seconds: float | None) -> str:
        if seconds is None:
            return "-"
        safe_value = max(0.0, float(seconds))
        if safe_value < 60.0:
            return f"{safe_value:.1f}s"
        minutes = int(safe_value // 60.0)
        remainder = safe_value - (minutes * 60.0)
        return f"{minutes}m{remainder:04.1f}s"

    # Method: _refresh_performance_timer_text - Met à jour le texte du timer dans la barre d'état.
    def _refresh_performance_timer_text(self) -> None:
        now = time.monotonic()
        loading_value = (
            now - self._loading_timer_started_monotonic
            if self._loading_timer_active and self._loading_timer_started_monotonic > 0.0
            else self._last_loading_duration_seconds
        )
        self.performance_timer_text.set(f"Chargement: {self._format_timer_duration(loading_value)}")

    # Method: _start_performance_timer_updates - Démarre le rafraîchissement périodique du timer affiché.
    def _start_performance_timer_updates(self) -> None:
        if self.is_closing or self.performance_timer_update_job is not None:
            return
        try:
            self.performance_timer_update_job = self.root.after(
                STATUS_TIMER_REFRESH_INTERVAL_MS,
                self._on_performance_timer_tick,
            )
        except TclError:
            self.performance_timer_update_job = None

    # Method: _on_performance_timer_tick - Met à jour le timer puis replanifie si une mesure est active.
    def _on_performance_timer_tick(self) -> None:
        self.performance_timer_update_job = None
        if self.is_closing:
            return
        self._refresh_performance_timer_text()
        if self._loading_timer_active:
            self._start_performance_timer_updates()

    # Method: _begin_loading_timer - Lance la mesure du temps de chargement du jeu en cours.
    def _begin_loading_timer(self) -> None:
        if self._loading_timer_active:
            return
        self._loading_timer_active = True
        self._loading_timer_started_monotonic = time.monotonic()
        self._refresh_performance_timer_text()
        self._start_performance_timer_updates()

    # Method: _end_loading_timer - Termine la mesure du temps de chargement du jeu en cours.
    def _end_loading_timer(self) -> None:
        if not self._loading_timer_active:
            return
        elapsed = time.monotonic() - self._loading_timer_started_monotonic
        self._last_loading_duration_seconds = max(0.0, elapsed)
        self._loading_timer_active = False
        self._loading_timer_started_monotonic = 0.0
        self._refresh_performance_timer_text()

    # Method: _begin_transition_timer - Lance la mesure de la transition de mode déclenchée par le refresh.
    def _begin_transition_timer(self) -> None:
        return

    # Method: _end_transition_timer - Termine la mesure de la transition de mode.
    def _end_transition_timer(self) -> None:
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

