"""Qt binding shim: PyQt6 first, PySide6 as a fallback."""
try:
    from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: F401
    from PyQt6.QtCore import pyqtSignal as Signal  # noqa: F401
    QT_API = "PyQt6"
except ImportError:  # pragma: no cover - depends on the distro
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
    from PySide6.QtCore import Signal  # noqa: F401
    QT_API = "PySide6"

Qt = QtCore.Qt
