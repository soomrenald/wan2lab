"""Wan2Lab desktop entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from wan2lab.controller import DesktopController


def qml_path() -> Path:
    return Path(__file__).resolve().parent / "qml" / "Main.qml"


def build_engine(controller: DesktopController) -> QQmlApplicationEngine:
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("studio", controller)
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    return engine


def main() -> int:
    application = QGuiApplication(sys.argv)
    application.setApplicationName("Wan2Lab")
    application.setOrganizationName("soomrenald")
    controller = DesktopController()
    application.aboutToQuit.connect(controller.closeWorker)
    engine = build_engine(controller)
    if not engine.rootObjects():
        return 1
    return application.exec()


__all__ = ["build_engine", "main", "qml_path"]
