from __future__ import annotations

import sqlite3
from typing import Any


# Function: init_db - Initialise le schéma SQLite de l'application.
def init_db(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                total_points INTEGER NOT NULL DEFAULT 0,
                softcore_points INTEGER NOT NULL DEFAULT 0,
                true_points INTEGER NOT NULL DEFAULT 0,
                total_games INTEGER NOT NULL DEFAULT 0,
                mastered_games INTEGER NOT NULL DEFAULT 0,
                beaten_games INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS game_progress (
                snapshot_id INTEGER NOT NULL,
                game_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                console_name TEXT,
                max_possible INTEGER NOT NULL DEFAULT 0,
                num_awarded INTEGER NOT NULL DEFAULT 0,
                num_awarded_hardcore INTEGER NOT NULL DEFAULT 0,
                highest_award_kind TEXT,
                highest_award_date TEXT,
                most_recent_awarded_date TEXT,
                PRIMARY KEY (snapshot_id, game_id),
                FOREIGN KEY(snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recent_achievements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                achievement_id INTEGER,
                game_id INTEGER,
                game_title TEXT,
                title TEXT NOT NULL,
                points INTEGER NOT NULL DEFAULT 0,
                unlocked_hardcore INTEGER NOT NULL DEFAULT 0,
                unlocked_at TEXT,
                FOREIGN KEY(snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


# Function: save_snapshot - Enregistre un instantané complet dans la base locale.
def save_snapshot(db_path: str, snapshot: dict[str, Any]) -> None:
    profile = _dict(snapshot.get("profile"))
    games = _list_of_dict(snapshot.get("games"))
    recent = _list_of_dict(snapshot.get("recent_achievements"))
    username = str(snapshot.get("username", ""))
    captured_at = str(snapshot.get("captured_at", ""))

    if not username or not captured_at:
        raise ValueError("Snapshot invalide: username/captured_at manquant.")

    points = _to_int(profile.get("TotalPoints") or profile.get("Points"))
    softcore_points = _to_int(profile.get("TotalSoftcorePoints"))
    true_points = _to_int(profile.get("TotalTruePoints"))

    normalized_games = []
    seen_game_ids: set[int] = set()
    for game in games:
        game_id = _to_int(game.get("GameID"))
        if game_id <= 0 or game_id in seen_game_ids:
            continue
        seen_game_ids.add(game_id)
        normalized_games.append((game_id, game))

    mastered_games = sum(
        1
        for _, game in normalized_games
        if str(game.get("HighestAwardKind", "")).lower().startswith("mastered")
    )
    beaten_games = sum(
        1
        for _, game in normalized_games
        if "beaten" in str(game.get("HighestAwardKind", "")).lower()
        and "mastered" not in str(game.get("HighestAwardKind", "")).lower()
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.execute(
            """
            INSERT INTO snapshots (
                username,
                captured_at,
                total_points,
                softcore_points,
                true_points,
                total_games,
                mastered_games,
                beaten_games
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                captured_at,
                points,
                softcore_points,
                true_points,
                len(normalized_games),
                mastered_games,
                beaten_games,
            ),
        )
        snapshot_id = cursor.lastrowid

        conn.executemany(
            """
            INSERT INTO game_progress (
                snapshot_id,
                game_id,
                title,
                console_name,
                max_possible,
                num_awarded,
                num_awarded_hardcore,
                highest_award_kind,
                highest_award_date,
                most_recent_awarded_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    snapshot_id,
                    game_id,
                    str(game.get("Title", "Jeu inconnu")),
                    str(game.get("ConsoleName", "")),
                    _to_int(game.get("MaxPossible")),
                    _to_int(game.get("NumAwarded")),
                    _to_int(game.get("NumAwardedHardcore")),
                    str(game.get("HighestAwardKind", "")),
                    str(game.get("HighestAwardDate", "")),
                    str(game.get("MostRecentAwardedDate", "")),
                )
                for game_id, game in normalized_games
            ],
        )

        conn.executemany(
            """
            INSERT INTO recent_achievements (
                snapshot_id,
                achievement_id,
                game_id,
                game_title,
                title,
                points,
                unlocked_hardcore,
                unlocked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    snapshot_id,
                    _to_int(entry.get("AchievementID") or entry.get("ID")),
                    _to_int(entry.get("GameID")),
                    str(entry.get("GameTitle", "")),
                    str(entry.get("Title", "Succes inconnu")),
                    _to_int(entry.get("Points")),
                    int(bool(entry.get("HardcoreMode"))),
                    str(entry.get("DateAwarded") or entry.get("Date", "")),
                )
                for entry in recent
            ],
        )
        conn.commit()


# Function: get_dashboard_data - Construit les données agrégées pour le tableau de bord.
def get_dashboard_data(db_path: str, username: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "latest": None,
        "delta": None,
        "games": [],
        "recent_achievements": [],
    }
    if not username:
        return data

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        snapshots = conn.execute(
            """
            SELECT id, captured_at, total_points, softcore_points, true_points,
                   total_games, mastered_games, beaten_games
            FROM snapshots
            WHERE username = ?
            ORDER BY id DESC
            LIMIT 2
            """,
            (username,),
        ).fetchall()

        if not snapshots:
            return data

        latest = dict(snapshots[0])
        data["latest"] = latest

        if len(snapshots) > 1:
            previous = dict(snapshots[1])
            data["delta"] = {
                "points": latest["total_points"] - previous["total_points"],
                "softcore_points": latest["softcore_points"] - previous["softcore_points"],
                "true_points": latest["true_points"] - previous["true_points"],
                "mastered_games": latest["mastered_games"] - previous["mastered_games"],
                "beaten_games": latest["beaten_games"] - previous["beaten_games"],
            }

        games = conn.execute(
            """
            SELECT game_id, title, console_name, max_possible, num_awarded_hardcore,
                   highest_award_kind, most_recent_awarded_date
            FROM game_progress
            WHERE snapshot_id = ?
            ORDER BY
                CASE
                    WHEN max_possible = 0 THEN 0
                    ELSE CAST(num_awarded_hardcore AS REAL) / max_possible
                END DESC,
                title ASC
            LIMIT 50
            """,
            (latest["id"],),
        ).fetchall()

        data["games"] = [
            {
                **dict(game),
                "completion_pct": _completion_pct(
                    _to_int(game["num_awarded_hardcore"]), _to_int(game["max_possible"])
                ),
            }
            for game in games
        ]

        recent = conn.execute(
            """
            SELECT achievement_id, game_id, game_title, title, points, unlocked_hardcore, unlocked_at
            FROM recent_achievements
            WHERE snapshot_id = ?
            ORDER BY id DESC
            LIMIT 30
            """,
            (latest["id"],),
        ).fetchall()
        data["recent_achievements"] = [dict(row) for row in recent]

    return data


# Function: _to_int - Convertit une valeur en entier de façon tolérante.
def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# Function: _dict - Retourne un dictionnaire valide à partir de la valeur fournie.
def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# Function: _list_of_dict - Normalise une valeur en liste de dictionnaires.
def _list_of_dict(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


# Function: _completion_pct - Calcule le pourcentage de progression hardcore.
def _completion_pct(num_awarded_hardcore: int, max_possible: int) -> float:
    if max_possible <= 0:
        return 0.0
    return round((num_awarded_hardcore / max_possible) * 100.0, 1)
