from __future__ import annotations

from tkinter import Tk

from retro_tracker.app_meta import APP_WINDOW_TITLE
from retro_tracker.debug_logger import install_global_exception_logging
from retro_tracker.single_instance import SingleInstanceGuard


def main() -> None:
    install_global_exception_logging()

    guard = SingleInstanceGuard(window_title=APP_WINDOW_TITLE)
    if not guard.acquire():
        guard.focus_existing_window()
        return

    try:
        # Import tardif pour éviter de charger l'UI lorsqu'une instance existe déjà.
        from app import TrackerApp

        root = Tk()
        app = TrackerApp(root)
        app.root.mainloop()
    finally:
        guard.release()


if __name__ == "__main__":
    main()
