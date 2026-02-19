from __future__ import annotations

import ctypes
import os
import socket
from ctypes import wintypes


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


class SingleInstanceGuard:
    def __init__(
        self,
        *,
        mutex_name: str = "Local\\PyRA_SingleInstance",
        listen_host: str = "127.0.0.1",
        listen_port: int = 47653,
        window_title: str = "PyRA",
    ) -> None:
        self.mutex_name = mutex_name
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.window_title = window_title
        self._mutex_handle: int | None = None
        self._socket_lock: socket.socket | None = None

    def acquire(self) -> bool:
        self._mutex_handle = self._acquire_mutex_lock()
        self._socket_lock = self._acquire_socket_lock()
        mutex_taken = self._mutex_handle == 0
        socket_taken = self._socket_lock is None
        if mutex_taken or socket_taken:
            self.release()
            return False
        return True

    def release(self) -> None:
        self._release_socket_lock(self._socket_lock)
        self._release_mutex_lock(self._mutex_handle)
        self._socket_lock = None
        self._mutex_handle = None

    def focus_existing_window(self) -> None:
        if (
            os.name != "nt"
            or _find_window_w is None
            or _show_window is None
            or _set_foreground_window is None
        ):
            return
        try:
            hwnd = _find_window_w(None, self.window_title)
            if not hwnd:
                return
            _show_window(hwnd, _SW_RESTORE)
            _set_foreground_window(hwnd)
        except Exception:
            return

    def _acquire_mutex_lock(self) -> int | None:
        if os.name != "nt" or _create_mutex_w is None:
            return None
        ctypes.set_last_error(0)
        handle = _create_mutex_w(None, False, self.mutex_name)
        if not handle:
            return None
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            if _close_handle is not None:
                _close_handle(handle)
            return 0
        return int(handle)

    def _release_mutex_lock(self, handle: int | None) -> None:
        if os.name != "nt" or _close_handle is None:
            return
        if not handle:
            return
        try:
            _close_handle(handle)
        except Exception:
            return

    def _acquire_socket_lock(self) -> socket.socket | None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((self.listen_host, self.listen_port))
            sock.listen(1)
        except OSError:
            try:
                sock.close()
            except OSError:
                pass
            return None
        return sock

    def _release_socket_lock(self, sock: socket.socket | None) -> None:
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            return
