from __future__ import annotations

import re
from datetime import datetime


# Class: ParsingMixin - Fournit des méthodes utilitaires de parsing et de conversion.
class ParsingMixin:
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
