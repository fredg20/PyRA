from __future__ import annotations

import os
from pathlib import Path


APP_DIR_NAME = "PyRA"


def data_dir() -> Path:
    base = Path(os.getenv("APPDATA", Path.home()))
    directory = base / APP_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def config_path() -> Path:
    return data_dir() / "config.json"


def current_game_cache_path() -> Path:
    return data_dir() / "current_game_cache.json"


def debug_log_path_candidates() -> list[Path]:
    return [data_dir() / "debug.log"]


def default_tracker_db_path() -> Path:
    return data_dir() / "tracker.db"
