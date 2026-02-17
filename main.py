import ctypes
import os
import socket
from ctypes import wintypes
from tkinter import Tk

from app import TrackerApp
from retro_tracker.debug_logger import install_global_exception_logging


_SINGLE_INSTANCE_MUTEX_NAME = "Local\\PyRA_SingleInstance"
_SINGLE_INSTANCE_PORT = 47653
_APP_WINDOW_TITLE = "PyRA - RetroAchievements Tracker"
_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9

if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _create_mutex_w = _kernel32.CreateMutexW
    _create_mutex_w.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _create_mutex_w.restype = wintypes.HANDLE
    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL
    _find_window_w = _user32.FindWindowW
    _find_window_w.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _find_window_w.restype = wintypes.HWND
    _show_window = _user32.ShowWindow
    _show_window.argtypes = [wintypes.HWND, ctypes.c_int]
    _show_window.restype = wintypes.BOOL
    _set_foreground_window = _user32.SetForegroundWindow
    _set_foreground_window.argtypes = [wintypes.HWND]
    _set_foreground_window.restype = wintypes.BOOL
else:
    _create_mutex_w = None
    _close_handle = None
    _find_window_w = None
    _show_window = None
    _set_foreground_window = None


def _acquire_single_instance_lock() -> int | None:
    if os.name != "nt" or _create_mutex_w is None:
        return None
    ctypes.set_last_error(0)
    handle = _create_mutex_w(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
    if not handle:
        return None
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        if _close_handle is not None:
            _close_handle(handle)
        return 0
    return int(handle)


def _release_single_instance_lock(handle: int | None) -> None:
    if os.name != "nt" or _close_handle is None:
        return
    if not handle:
        return
    try:
        _close_handle(handle)
    except Exception:
        return


def _acquire_single_instance_socket_lock() -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
        sock.listen(1)
    except OSError:
        try:
            sock.close()
        except OSError:
            pass
        return None
    return sock


def _release_single_instance_socket_lock(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        return


def _focus_existing_instance_window() -> None:
    if (
        os.name != "nt"
        or _find_window_w is None
        or _show_window is None
        or _set_foreground_window is None
    ):
        return
    try:
        hwnd = _find_window_w(None, _APP_WINDOW_TITLE)
        if not hwnd:
            return
        _show_window(hwnd, _SW_RESTORE)
        _set_foreground_window(hwnd)
    except Exception:
        return


def main() -> None:
    install_global_exception_logging()
    single_instance_handle = _acquire_single_instance_lock()
    socket_lock = _acquire_single_instance_socket_lock()
    if single_instance_handle == 0 or socket_lock is None:
        _focus_existing_instance_window()
        _release_single_instance_lock(single_instance_handle)
        _release_single_instance_socket_lock(socket_lock)
        return
    root = Tk()
    app = TrackerApp(root)
    try:
        app.root.mainloop()
    finally:
        _release_single_instance_socket_lock(socket_lock)
        _release_single_instance_lock(single_instance_handle)


if __name__ == "__main__":
    main()
