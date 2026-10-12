import ctypes
import sys


class WindowsAPI:
    """Small, typed Win32 wrapper. Construct only on Windows, after QApplication."""
    def __init__(self) -> None:
        from ctypes import wintypes as w
        self.w = w
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.u.GetForegroundWindow.argtypes = []
        self.u.GetForegroundWindow.restype = w.HWND
        self.u.GetAncestor.argtypes = [w.HWND, w.UINT]
        self.u.GetAncestor.restype = w.HWND
        self.u.WindowFromPoint.argtypes = [w.POINT]
        self.u.WindowFromPoint.restype = w.HWND
        self.u.IsWindow.argtypes = [w.HWND]
        self.u.IsWindow.restype = w.BOOL
        self.u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.u.GetAsyncKeyState.restype = ctypes.c_short
        self.u.SetWindowDisplayAffinity.argtypes = [w.HWND, w.DWORD]
        self.u.SetWindowDisplayAffinity.restype = w.BOOL
        self.u.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, w.UINT]
        self.u.SetWindowPos.restype = w.BOOL
        pointer_long = ctypes.c_ssize_t
        self.get_style = getattr(self.u, "GetWindowLongPtrW", self.u.GetWindowLongW)
        self.get_style.argtypes = [w.HWND, ctypes.c_int]
        self.get_style.restype = pointer_long
        self.set_style = getattr(self.u, "SetWindowLongPtrW", self.u.SetWindowLongW)
        self.set_style.argtypes = [w.HWND, ctypes.c_int, pointer_long]
        self.set_style.restype = pointer_long
        self.dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self.dwm.DwmFlush.argtypes = []
        self.dwm.DwmFlush.restype = ctypes.c_long

    def flush(self) -> None:
        self.dwm.DwmFlush()

    def key_down(self, virtual_key: int) -> bool:
        return bool(self.u.GetAsyncKeyState(virtual_key) & 0x8000)

    def root_at(self, x: int, y: int) -> int:
        hwnd = self.u.WindowFromPoint(self.w.POINT(x, y))
        return int(self.u.GetAncestor(hwnd, 2) or 0)  # GA_ROOT

    def foreground(self) -> int:
        return int(self.u.GetForegroundWindow() or 0)

    def configure_overlay(self, hwnd: int) -> bool:
        # Qt flags already express this, but native flags reinforce click-through
        # across OTHER processes, not merely other widgets in this application.
        style = self.get_style(hwnd, -20)  # GWL_EXSTYLE
        style |= 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080
        # WS_EX_LAYERED | TRANSPARENT | NOACTIVATE | TOOLWINDOW
        self.set_style(hwnd, -20, style)
        self.u.SetWindowPos(hwnd, self.w.HWND(-1), 0, 0, 0, 0,
                            0x0001 | 0x0002 | 0x0010 | 0x0020)
        # HWND_TOPMOST; NOSIZE | NOMOVE | NOACTIVATE | FRAMECHANGED.
        if sys.getwindowsversion().build < 19041:
            return False
        return bool(self.u.SetWindowDisplayAffinity(hwnd, 0x00000011))