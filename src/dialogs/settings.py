# -*- coding: utf-8 -*-
from urllib.parse import urlparse

from qgis.PyQt.QtCore import QLocale
from qgis.PyQt.QtWidgets import QDialogButtonBox, QWidget

try:
    from qgis.core import Qgis
except ImportError:
    from qgis.core import QGis as Qgis

from ...dialog_settings import Ui_Settings
from ..api import ApiError
from ..utils import (ALLOWED_SETTINGS, Logger, get_api_server,
                     get_plugin_metadata, get_setting, set_setting, tr)
from .utils import DialogData

logger = Logger(__file__)


class PicterraDialogSettings(QWidget):
    """
    QDialog representing the settings of the plugin

    For ref see https://doc.qt.io/qt-5/qdialogbuttonbox.html
    """

    def __init__(self, data: DialogData):
        """
        Constructor

        Creates the dialog, connect events
        """
        super().__init__()
        self.ui = Ui_Settings()
        self.ui.setupUi(self)
        self.api = data["api"]
        metadata = get_plugin_metadata()
        # "Picterra account" tab
        self.ui.api_key_text.setText(get_setting("api_key"))
        self.ui.api_server_text.setText(urlparse(get_api_server()).netloc)
        # "QGIS settings" tab
        self.ui.locale_text.setText(str(QLocale().name()))
        self.ui.sw_version_text.setText(str(Qgis.QGIS_VERSION))
        self.ui.plugin_category_text.setText(str(metadata["category"]))
        self.ui.plugin_version_text.setText(str(metadata["version"]))
        # Rejected-close connection is already in the .ui files
        self.ui.button_box_picterra.button(QDialogButtonBox.Apply).clicked.connect(
            self.save_settings
        )
        self.ui.button_box_qgis.button(QDialogButtonBox.Apply).clicked.connect(
            self.save_settings
        )
        self.ui.button_box_picterra.button(
            QDialogButtonBox.RestoreDefaults
        ).clicked.connect(self.restore_settings)
        self.ui.button_box_qgis.button(
            QDialogButtonBox.RestoreDefaults
        ).clicked.connect(self.restore_settings)

    def save_settings(self) -> None:
        """Update api key stored and in api, do not check if it's valid"""
        new_apikey = self.ui.api_key_text.text()
        set_setting("api_key", new_apikey)
        self.api.key = new_apikey
        # Closes and destroys dialog widget
        self.close()
        self.setParent(None)

    def restore_settings(self) -> None:
        """Restore default settings"""
        for setting in ALLOWED_SETTINGS:
            set_setting(setting, get_setting(setting))
        self.update()
