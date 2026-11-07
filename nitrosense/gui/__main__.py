"""Entry point: python3 -m nitrosense.gui [--demo] [--native-frame]"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="nitrosense", description="NitroSense pour Linux")
    parser.add_argument("--demo", action="store_true", help="matériel simulé, sans le service")
    parser.add_argument("--native-frame", action="store_true", help="garder la barre de titre du système")
    parser.add_argument("--socket", default=None, help="socket du service nitrosensed")
    parser.add_argument("--open", choices=["lighting", "settings"], help="ouvrir une fenêtre au démarrage")
    parser.add_argument("--screenshot", metavar="PNG", help="capture la fenêtre après --delay ms puis quitte")
    parser.add_argument("--delay", type=int, default=4000)
    args = parser.parse_args(argv)

    from .qt import QtCore, QtGui, QtWidgets
    from . import theme
    from .app import MainWindow, Prefs
    from ..client import SOCKET_PATH, DaemonClient, LocalClient

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("NitroSense Linux")
    app.setDesktopFileName("nitrosense-linux")
    app.setStyle("Fusion")
    palette = QtGui.QPalette()
    for role, value in ((QtGui.QPalette.ColorRole.Window, theme.BG), (QtGui.QPalette.ColorRole.Base, "#101010"),
                        (QtGui.QPalette.ColorRole.Text, theme.TEXT), (QtGui.QPalette.ColorRole.WindowText, theme.TEXT),
                        (QtGui.QPalette.ColorRole.Button, "#232323"),
                        (QtGui.QPalette.ColorRole.ButtonText, theme.TEXT),
                        (QtGui.QPalette.ColorRole.Highlight, theme.RED)):
        palette.setColor(role, QtGui.QColor(value))
    app.setPalette(palette)
    app.setStyleSheet(theme.STYLESHEET)

    if args.demo:
        from ..demo import make_demo_controller
        controller, _world = make_demo_controller()
        client = LocalClient(controller)
    else:
        client = DaemonClient(args.socket or SOCKET_PATH)

    window = MainWindow(client, Prefs(), native_frame=args.native_frame, demo=args.demo)
    window.show()
    dialogs = []
    if args.open:
        opener = window.open_lighting if args.open == "lighting" else window.open_settings
        QtCore.QTimer.singleShot(1500, lambda: dialogs.append(opener(modal=False)))
    if args.screenshot:
        def capture():
            window.grab().save(args.screenshot)
            if dialogs:
                dialogs[0].grab().save(args.screenshot.replace(".png", "-dialog.png"))
            app.quit()
        QtCore.QTimer.singleShot(args.delay, capture)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
