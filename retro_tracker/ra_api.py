from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from retro_tracker.debug_logger import log_debug


# Class: RetroAPIError - Définit une erreur liée aux appels API.
class RetroAPIError(Exception):
    """Raised when the RetroAchievements API request fails."""


# Class: RetroAchievementsClient - Encapsule les appels à l'API RetroAchievements.
class RetroAchievementsClient:
    BASE_URL = "https://retroachievements.org/API"

    # Method: __init__ - Initialise l'objet et prépare son état interne.
    def __init__(self, api_key: str, timeout_seconds: int = 15) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        log_debug(
            f"RetroAchievementsClient init timeout_seconds={self.timeout_seconds} "
            f"api_key_present={'yes' if bool(self.api_key) else 'no'}"
        )

    # Method: fetch_snapshot - Construit un instantané complet des données utilisateur.
    def fetch_snapshot(self, username: str) -> dict[str, Any]:
        log_debug(f"fetch_snapshot start username='{username}'")
        profile = self.get_user_profile(username)
        games = self.get_user_completion_progress(username)
        recent_achievements = self.get_user_recent_achievements(username)
        summary = self.get_user_summary(username, include_recent_games=True)
        last_played_game_id, last_played_game_title = self._extract_last_played_from_summary(summary)
        log_debug(
            f"fetch_snapshot done username='{username}' games={len(games)} "
            f"recent_achievements={len(recent_achievements)} last_played_game_id={last_played_game_id}"
        )
        return {
            "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "username": username,
            "profile": profile,
            "games": games,
            "recent_achievements": recent_achievements,
            "last_played_game_id": last_played_game_id,
            "last_played_game_title": last_played_game_title,
        }

    # Method: get_user_profile - Récupère les données demandées.
    def get_user_profile(self, username: str) -> dict[str, Any]:
        log_debug(f"get_user_profile username='{username}'")
        data = self._get("API_GetUserProfile.php", {"u": username})
        if not isinstance(data, dict):
            log_debug("get_user_profile invalid response type")
            raise RetroAPIError("Reponse profile invalide.")
        return data

    # Method: get_user_completion_progress - Récupère les données demandées.
    def get_user_completion_progress(self, username: str, page_size: int = 500) -> list[dict[str, Any]]:
        log_debug(f"get_user_completion_progress username='{username}' page_size={page_size}")
        offset = 0
        all_games: list[dict[str, Any]] = []

        while True:
            log_debug(f"completion_progress page request offset={offset}")
            raw_page = self._get(
                "API_GetUserCompletionProgress.php",
                {"u": username, "c": page_size, "o": offset},
            )
            page = self._extract_results(raw_page)
            if not isinstance(page, list):
                log_debug("completion_progress invalid response type")
                raise RetroAPIError("Reponse completion progress invalide.")
            if not page:
                log_debug("completion_progress empty page stop")
                break
            all_games.extend(item for item in page if isinstance(item, dict))
            log_debug(f"completion_progress received page_size={len(page)} total={len(all_games)}")
            if len(page) < page_size:
                break
            offset += page_size

        log_debug(f"get_user_completion_progress done total={len(all_games)}")
        return all_games

    # Method: get_user_recent_achievements - Récupère les données demandées.
    def get_user_recent_achievements(
        self, username: str, minutes: int = 60 * 24 * 7
    ) -> list[dict[str, Any]]:
        log_debug(f"get_user_recent_achievements username='{username}' minutes={minutes}")
        data = self._get("API_GetUserRecentAchievements.php", {"u": username, "m": minutes})
        if not isinstance(data, list):
            log_debug("get_user_recent_achievements invalid response type")
            raise RetroAPIError("Reponse recent achievements invalide.")
        items = [item for item in data if isinstance(item, dict)]
        log_debug(f"get_user_recent_achievements done count={len(items)}")
        return items

    # Method: get_user_summary - Récupère le résumé utilisateur et le jeu actif côté RA.
    def get_user_summary(self, username: str, include_recent_games: bool = True) -> dict[str, Any]:
        params: dict[str, Any] = {"u": username}
        if include_recent_games:
            params["g"] = 1
        log_debug(f"get_user_summary username='{username}' include_recent_games={include_recent_games}")
        data = self._get("API_GetUserSummary.php", params)
        if not isinstance(data, dict):
            log_debug("get_user_summary invalid response type")
            raise RetroAPIError("Reponse user summary invalide.")
        return data

    # Method: get_game_info_and_user_progress - Récupère les données demandées.
    def get_game_info_and_user_progress(self, username: str, game_id: int) -> dict[str, Any]:
        log_debug(f"get_game_info_and_user_progress username='{username}' game_id={game_id}")
        params = {"u": username, "g": game_id}
        try:
            data = self._get("API_GetGameInfoAndUserProgress.php", params)
        except RetroAPIError as exc:
            # Some clients/documentation variants use "i" instead of "g".
            log_debug(f"get_game_info_and_user_progress retry_with_i game_id={game_id} cause='{exc}'")
            data = self._get("API_GetGameInfoAndUserProgress.php", {"u": username, "i": game_id})
        if not isinstance(data, dict):
            log_debug("get_game_info_and_user_progress invalid response type")
            raise RetroAPIError("Reponse game info invalide.")
        return data

    # Method: _get - Exécute une requête API et valide la réponse.
    def _get(self, endpoint: str, params: dict[str, Any]) -> Any:
        query = dict(params)
        query["y"] = self.api_key
        url = f"{self.BASE_URL}/{endpoint}"
        safe_query = {k: query[k] for k in query if k != "y"}
        log_debug(f"api_get start endpoint='{endpoint}' params={safe_query}")

        try:
            response = requests.get(url, params=query, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            log_debug(f"api_get request_error endpoint='{endpoint}' error='{exc}'")
            raise RetroAPIError(f"Echec reseau vers {endpoint}: {exc}") from exc

        log_debug(
            f"api_get response endpoint='{endpoint}' status={response.status_code} "
            f"content_length={len(response.text) if response.text is not None else 0}"
        )
        if response.status_code != 200:
            raise RetroAPIError(f"{endpoint} a retourne HTTP {response.status_code}.")

        try:
            payload = response.json()
        except ValueError as exc:
            log_debug(f"api_get invalid_json endpoint='{endpoint}'")
            raise RetroAPIError(f"JSON invalide recu depuis {endpoint}.") from exc

        if isinstance(payload, dict) and "Success" in payload and payload.get("Success") is False:
            message = payload.get("Error", "Erreur API inconnue.")
            log_debug(f"api_get api_error endpoint='{endpoint}' message='{message}'")
            raise RetroAPIError(str(message))

        payload_type = type(payload).__name__
        payload_size = len(payload) if isinstance(payload, (dict, list)) else -1
        log_debug(f"api_get done endpoint='{endpoint}' payload_type={payload_type} payload_size={payload_size}")
        return payload

    # Method: _extract_results - Extrait la liste de résultats selon le format de réponse RA.
    def _extract_results(self, payload: Any) -> Any:
        # Some RA endpoints return {"Count":..., "Total":..., "Results":[...]}.
        if isinstance(payload, dict) and "Results" in payload:
            return payload.get("Results")
        return payload

    # Method: _extract_last_played_from_summary - Extrait le dernier jeu joue depuis le resume utilisateur.
    def _extract_last_played_from_summary(self, summary: dict[str, Any]) -> tuple[int, str]:
        recent = summary.get("RecentlyPlayed")
        if isinstance(recent, list):
            for item in recent:
                if not isinstance(item, dict):
                    continue
                game_id = self._to_int(item.get("GameID") or item.get("ID"))
                if game_id <= 0:
                    continue
                title = self._to_text(item.get("Title") or item.get("GameTitle") or item.get("Name"))
                return game_id, title

        direct_pairs = (
            ("MostRecentGameID", "MostRecentGameTitle"),
            ("LastGameID", "LastGame"),
            ("GameID", "GameTitle"),
        )
        for game_id_field, title_field in direct_pairs:
            game_id = self._to_int(summary.get(game_id_field))
            if game_id <= 0:
                continue
            return game_id, self._to_text(summary.get(title_field))
        return 0, ""

    # Method: _to_int - Convertit une valeur en entier de facon tolerante.
    def _to_int(self, value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    # Method: _to_text - Convertit une valeur en texte simple.
    def _to_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value).strip()
        return ""
