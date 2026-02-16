from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests


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

    # Method: fetch_snapshot - Construit un instantané complet des données utilisateur.
    def fetch_snapshot(self, username: str) -> dict[str, Any]:
        profile = self.get_user_profile(username)
        games = self.get_user_completion_progress(username)
        recent_achievements = self.get_user_recent_achievements(username)
        return {
            "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "username": username,
            "profile": profile,
            "games": games,
            "recent_achievements": recent_achievements,
        }

    # Method: get_user_profile - Récupère les données demandées.
    def get_user_profile(self, username: str) -> dict[str, Any]:
        data = self._get("API_GetUserProfile.php", {"u": username})
        if not isinstance(data, dict):
            raise RetroAPIError("Reponse profile invalide.")
        return data

    # Method: get_user_completion_progress - Récupère les données demandées.
    def get_user_completion_progress(self, username: str, page_size: int = 500) -> list[dict[str, Any]]:
        offset = 0
        all_games: list[dict[str, Any]] = []

        while True:
            raw_page = self._get(
                "API_GetUserCompletionProgress.php",
                {"u": username, "c": page_size, "o": offset},
            )
            page = self._extract_results(raw_page)
            if not isinstance(page, list):
                raise RetroAPIError("Reponse completion progress invalide.")
            if not page:
                break
            all_games.extend(item for item in page if isinstance(item, dict))
            if len(page) < page_size:
                break
            offset += page_size

        return all_games

    # Method: get_user_recent_achievements - Récupère les données demandées.
    def get_user_recent_achievements(
        self, username: str, minutes: int = 60 * 24 * 7
    ) -> list[dict[str, Any]]:
        data = self._get("API_GetUserRecentAchievements.php", {"u": username, "m": minutes})
        if not isinstance(data, list):
            raise RetroAPIError("Reponse recent achievements invalide.")
        return [item for item in data if isinstance(item, dict)]

    # Method: get_user_summary - Récupère le résumé utilisateur et le jeu actif côté RA.
    def get_user_summary(self, username: str, include_recent_games: bool = True) -> dict[str, Any]:
        params: dict[str, Any] = {"u": username}
        if include_recent_games:
            params["g"] = 1
        data = self._get("API_GetUserSummary.php", params)
        if not isinstance(data, dict):
            raise RetroAPIError("Reponse user summary invalide.")
        return data

    # Method: get_game_info_and_user_progress - Récupère les données demandées.
    def get_game_info_and_user_progress(self, username: str, game_id: int) -> dict[str, Any]:
        params = {"u": username, "g": game_id}
        try:
            data = self._get("API_GetGameInfoAndUserProgress.php", params)
        except RetroAPIError:
            # Some clients/documentation variants use "i" instead of "g".
            data = self._get("API_GetGameInfoAndUserProgress.php", {"u": username, "i": game_id})
        if not isinstance(data, dict):
            raise RetroAPIError("Reponse game info invalide.")
        return data

    # Method: _get - Exécute une requête API et valide la réponse.
    def _get(self, endpoint: str, params: dict[str, Any]) -> Any:
        query = dict(params)
        query["y"] = self.api_key
        url = f"{self.BASE_URL}/{endpoint}"

        try:
            response = requests.get(url, params=query, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            raise RetroAPIError(f"Echec reseau vers {endpoint}: {exc}") from exc

        if response.status_code != 200:
            raise RetroAPIError(f"{endpoint} a retourne HTTP {response.status_code}.")

        try:
            payload = response.json()
        except ValueError as exc:
            raise RetroAPIError(f"JSON invalide recu depuis {endpoint}.") from exc

        if isinstance(payload, dict) and "Success" in payload and payload.get("Success") is False:
            message = payload.get("Error", "Erreur API inconnue.")
            raise RetroAPIError(str(message))

        return payload

    # Method: _extract_results - Extrait la liste de résultats selon le format de réponse RA.
    def _extract_results(self, payload: Any) -> Any:
        # Some RA endpoints return {"Count":..., "Total":..., "Results":[...]}.
        if isinstance(payload, dict) and "Results" in payload:
            return payload.get("Results")
        return payload
