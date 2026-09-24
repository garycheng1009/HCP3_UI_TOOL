"""
single_instance.py — 確保 DA005ControlPanel 同時間只有一個 instance 在跑。

Windows：沿用 Named Mutex + Win32 API，第二份程式會嘗試把既有視窗帶到前景。
macOS / Linux：使用 OS 檔案鎖（flock）。鎖檔本身可以留在暫存目錄，真正的鎖由
作業系統跟 process 綁定；程式正常結束或 crash 後都會自動釋放，不會因殘留檔案
而永久誤判已有 instance。
"""

import os
import sys
import tempfile
import time

_IS_WINDOWS = sys.platform == 'win32'

_MUTEX_NAME = 'DA005ControlPanel_SingleInstance_Mutex_9F3E1B2C6C1B'
_mutex_handle = None
_lock_file_handle = None

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _user32 = ctypes.WinDLL('user32', use_last_error=True)

    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)

    _user32.FindWindowW.restype = wintypes.HWND
    _user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = (wintypes.HWND,)
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = (wintypes.HWND,)

    _ERROR_ALREADY_EXISTS = 183
    _SW_RESTORE = 9
else:
    import fcntl


def try_acquire_single_instance_lock():
    """True = 可以啟動；False = 已經有另一份程式在執行，或無法安全取得鎖。"""
    global _mutex_handle, _lock_file_handle

    if _IS_WINDOWS:
        _mutex_handle = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not _mutex_handle:
            error_code = ctypes.get_last_error()
            print(f'[single_instance] CreateMutexW failed (error={error_code})')
            return False
        return ctypes.get_last_error() != _ERROR_ALREADY_EXISTS

    lock_path = os.path.join(tempfile.gettempdir(), 'da005_control_panel.lock')
    try:
        _lock_file_handle = open(lock_path, 'a+', encoding='utf-8')
        fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file_handle.seek(0)
        _lock_file_handle.truncate()
        _lock_file_handle.write(str(os.getpid()))
        _lock_file_handle.flush()
        return True
    except (OSError, IOError) as error:
        print(f'[single_instance] another instance is already running or lock failed: {error}')
        if _lock_file_handle is not None:
            try:
                _lock_file_handle.close()
            except OSError:
                pass
            _lock_file_handle = None
        return False


def bring_existing_instance_to_front(window_title, max_wait_seconds=3.0, retry_interval_seconds=0.2):
    """Windows 會嘗試把既有視窗帶到前景；macOS/Linux 安全地不做前景切換。"""
    if not _IS_WINDOWS:
        return False

    deadline = time.monotonic() + max_wait_seconds
    hwnd = _user32.FindWindowW(None, window_title)
    while not hwnd and time.monotonic() < deadline:
        time.sleep(retry_interval_seconds)
        hwnd = _user32.FindWindowW(None, window_title)

    if not hwnd:
        return False

    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
    return bool(_user32.SetForegroundWindow(hwnd))
