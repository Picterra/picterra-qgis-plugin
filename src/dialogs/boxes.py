# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import QMessageBox

from ..utils import Logger, get_plugin_metadata, tr

logger = Logger(__file__)


def info_box(title: str, body: str) -> QMessageBox:
    """Shows an info box"""
    obj = QMessageBox()
    obj.setIcon(QMessageBox.Information)
    obj.setWindowTitle("Picterra - " + str(title))
    obj.setText(str(body))
    return obj


def warn_box(text: str) -> QMessageBox:
    """Shows an info box"""
    obj = QMessageBox()
    obj.setIcon(QMessageBox.Warning)
    obj.setWindowTitle("Warning")
    obj.setText(text)
    return obj


def error_box(body: str) -> QMessageBox:
    """Shows an error box."""
    obj = QMessageBox()
    obj.setIcon(QMessageBox.Critical)
    obj.setWindowTitle(tr("Error from Picterra"))
    email = get_plugin_metadata()["email"]
    obj.setText(
        str(body) + "\n\n" + tr("If the problem persists, contact") + " " + email
    )
    logger.error(body)
    return obj


def consensus_box(title: str, body: str):
    """
    Shows dialog asking yes or no and return boolean response.

    https://doc.qt.io/qtforpython/PySide2/QtWidgets/QMessageBox.html#PySide2.QtWidgets.PySide2.QtWidgets.QMessageBox.question
    """
    return QMessageBox.question(None, title, body) == QMessageBox.Yes
