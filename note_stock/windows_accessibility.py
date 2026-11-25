"""Initialize Qt's UI Automation provider outside incoming synchronous calls."""
import ctypes
import sys

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget


class _AccessibilityStartup(QObject):
    def eventFilter(self, widget, event):
        if event.type() == QEvent.Type.Show and isinstance(widget, QWidget) and widget.isWindow():
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            send = user32.SendMessageW
            send.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            send.restype = ctypes.c_ssize_t
            # A same-thread WM_GETOBJECT initializes UIA before an external
            # synchronous query can trigger its first COM registration.
            result = send(int(widget.winId()), 0x003D, 0, -25)
            if result:
                self.parent().removeEventFilter(self)
        return False


def create_application(arguments):
    app = QApplication(arguments)
    if sys.platform == "win32":
        app._accessibility_startup = _AccessibilityStartup(app)
        app.installEventFilter(app._accessibility_startup)
    return app
