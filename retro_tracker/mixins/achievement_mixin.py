from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any


# Class: AchievementMixin - Fournit les méthodes liées à l'analyse et à l'affichage des succès.
class AchievementMixin:
    # Method: _translate_achievement_description_to_french - Traduit une description en francais avec cache et fallback local.
    def _translate_achievement_description_to_french(self, description: str) -> str:
        text = " ".join(description.split())
        if not text:
            return ""

        now = time.time()
        cache = getattr(self, "_achievement_translation_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_achievement_translation_cache", cache)
        cached = cache.get(text)
        if isinstance(cached, str):
            return cached

        fail_cache = getattr(self, "_achievement_translation_fail_cache", None)
        if not isinstance(fail_cache, dict):
            fail_cache = {}
            setattr(self, "_achievement_translation_fail_cache", fail_cache)
        last_failed_at = fail_cache.get(text)
        if isinstance(last_failed_at, (int, float)) and (now - float(last_failed_at)) < 60:
            return text

        if bool(getattr(self, "_achievement_translation_disabled", False)):
            return text

        translator_factory = None
        translator = getattr(self, "_achievement_translator", None)
        if translator is None:
            try:
                from googletrans import Translator  # type: ignore[import-not-found]

                translator_factory = Translator
                translator = Translator()
                setattr(self, "_achievement_translator", translator)
            except Exception:
                setattr(self, "_achievement_translation_disabled", True)
                return text

        if translator_factory is None:
            try:
                from googletrans import Translator  # type: ignore[import-not-found]

                translator_factory = Translator
            except Exception:
                translator_factory = None

        # Heuristique simple pour forcer une 2e tentative avec source anglaise.
        lowered = f" {text.casefold()} "
        english_markers = (
            " the ",
            " and ",
            " with ",
            " without ",
            " unlock ",
            " defeated ",
            " defeat ",
            " collect ",
            " complete ",
            " kill ",
            " use ",
            " win ",
            " find ",
            " level ",
            " boss ",
        )
        looks_english = any(marker in lowered for marker in english_markers)
        sources: list[str | None] = [None]
        if looks_english:
            sources.append("en")

        translated = ""
        for source in sources:
            attempts = 2
            for _ in range(attempts):
                try:
                    kwargs: dict[str, str] = {"dest": "fr"}
                    if source is not None:
                        kwargs["src"] = source
                    result: Any = translator.translate(text, **kwargs)
                    if inspect.isawaitable(result):
                        try:
                            result = asyncio.run(result)
                        except RuntimeError:
                            loop = asyncio.new_event_loop()
                            try:
                                result = loop.run_until_complete(result)
                            finally:
                                loop.close()
                    translated = self._safe_text(getattr(result, "text", ""))
                    if translated:
                        break
                except Exception:
                    translated = ""
                    if translator_factory is not None:
                        try:
                            translator = translator_factory()
                            setattr(self, "_achievement_translator", translator)
                        except Exception:
                            pass
                if translated:
                    break
            if translated:
                # Si l'auto-détection n'a rien changé, on essaie la source anglaise.
                if source is None and translated.casefold() == text.casefold() and looks_english:
                    translated = ""
                    continue
                break

        if not translated:
            fail_cache[text] = now
            return text

        fail_cache.pop(text, None)
        cache[text] = translated
        return translated

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

    # Method: _compute_achievement_difficulty_score - Retourne un score numérique (plus bas = plus facile) pour trier les succès.
    def _compute_achievement_difficulty_score(
        self,
        awarded: int,
        total_players: int,
        true_ratio_value: float | None,
    ) -> tuple[bool, float]:
        if total_players > 0 and awarded >= 0:
            unlock_pct = (awarded * 100.0) / max(1, total_players)
            return True, max(0.0, min(100.0, 100.0 - unlock_pct))

        if true_ratio_value is not None and true_ratio_value > 0:
            return True, min(999.0, true_ratio_value)

        return False, 9999.0

    # Method: _build_next_achievement_summary - Prépare les champs de la section du premier succès non débloqué.
    def _build_next_achievement_summary(
        self,
        achievement: dict[str, object],
        total_players: int = 0,
        translate_description: bool = True,
    ) -> dict[str, str]:
        title = self._safe_text(achievement.get("Title")) or f"Succès #{self._safe_int(achievement.get('ID'))}"
        description = self._safe_text(achievement.get("Description")) or "Sans description."
        if translate_description:
            description = self._translate_achievement_description_to_french(description)
        points = self._safe_int(achievement.get("Points"))
        true_ratio_value = self._safe_float(achievement.get("TrueRatio"))
        awarded = self._safe_int(achievement.get("NumAwarded"))
        awarded_hardcore = self._safe_int(achievement.get("NumAwardedHardcore"))
        feasibility = self._build_achievement_feasibility(awarded, total_players, true_ratio_value)
        achievement_id = self._safe_int(achievement.get("ID"))
        return {
            "id": str(achievement_id) if achievement_id > 0 else "",
            "title": title,
            "description": description,
            "points": f"{points} points",
            "unlocks": f"{awarded} | {awarded_hardcore}",
            "feasibility": feasibility,
        }
