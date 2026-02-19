from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from types import TracebackType
from typing import Any


LOGGER_NAME = "pyra.debug.current_game"
_HOOKS_INSTALLED = False


# Function: _data_dir - Retourne le dossier de donnees de l'application.
def _data_dir() -> Path:
    base = Path(os.getenv("APPDATA", Path.home()))
    directory = base / "PyRA"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


# Function: debug_log_path_candidates - Retourne les emplacements possibles pour debug.log.
def debug_log_path_candidates() -> list[Path]:
    # Chemin unique pour éviter toute confusion entre mode source et exécutable.
    return [_data_dir() / "debug.log"]


# Function: get_debug_logger - Retourne un logger configure vers le fichier debug.log.
def get_debug_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    for candidate in debug_log_path_candidates():
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(candidate, encoding="utf-8")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.info("debug.log initialise: %s", candidate)
            return logger
        except OSError:
            continue

    logger.addHandler(logging.NullHandler())
    return logger


# Function: log_debug - Ecrit une ligne dans debug.log sans lever d'exception.
def log_debug(message: str) -> None:
    try:
        get_debug_logger().info(message)
    except Exception:
        return


# Function: install_global_exception_logging - Active la capture des exceptions non gerees.
def install_global_exception_logging() -> None:
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    _HOOKS_INSTALLED = True

    previous_sys_hook = sys.excepthook

    def _sys_hook(exc_type: type[BaseException], exc_value: BaseException, exc_tb: TracebackType | None) -> None:
        try:
            get_debug_logger().exception(
                "Exception non geree (sys.excepthook)",
                exc_info=(exc_type, exc_value, exc_tb),
            )
        except Exception:
            pass
        try:
            previous_sys_hook(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = _sys_hook

    if hasattr(threading, "excepthook"):
        previous_thread_hook = threading.excepthook

        def _thread_hook(args: Any) -> None:
            thread_name = "inconnu"
            try:
                if getattr(args, "thread", None) is not None:
                    thread_name = str(args.thread.name)
            except Exception:
                pass
            try:
                get_debug_logger().exception(
                    "Exception non geree (thread=%s)",
                    thread_name,
                    exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
                )
            except Exception:
                pass
            try:
                previous_thread_hook(args)
            except Exception:
                pass

        threading.excepthook = _thread_hook
