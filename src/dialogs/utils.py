import json
from typing import Any, Callable, Optional, TypedDict

from qgis.gui import QgisInterface
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QListWidgetItem, QWidget

from ...dialog_select import Ui_select as Ui_Select
from ..api import API
from ..utils import tr
from .boxes import error_box, info_box, warn_box


class DialogData(TypedDict):
    api: API
    iface: QgisInterface


class PicterraDialogSelect(QWidget):
    def __init__(
        self,
        endpoint: str,
        params: dict,
        api: API,
        fill_on_init: bool = True,
        selected_callback: Optional[Callable] = None,
        resource_name: Optional[str] = None,
        select_text: Optional[str] = None,
        hide_select: bool = False,
    ):
        super().__init__()
        # state setup
        self.api = api
        self.endpoint = endpoint
        self.params: dict = params
        if "page_number" not in params:
            self.params["page_number"] = 1
        if "search" not in params:
            self.params["search"] = ""
        self.has_next = True
        if resource_name is None:
            resource_name = self.endpoint[:-1]
        self.resource = resource_name
        # ui setup
        self.ui = Ui_Select()
        self.ui.setupUi(self)
        # CTAs setup
        self.ui.search_cta.clicked.connect(self.fill_select)
        self.ui.info_selected.clicked.connect(self.show_object_info)
        self.ui.link_selected.setIcon(QIcon(":/plugins/picterra/assets/platform.png"))
        self.ui.link_selected.clicked.connect(self.open_object_web)
        if select_text is None:
            select_text = "Select " + self.resource
        self.ui.command_select.setText(tr(select_text))
        if hide_select:
            self.ui.command_select.hide()
        if fill_on_init:
            self.fill_select()
        if selected_callback:

            def cb():
                id = self.get_id()
                if id is None:
                    self.warn_box = warn_box("Select a %s first" % self.resource)
                    self.warn_box.show()
                else:
                    selected_callback(id)

            self.ui.command_select.clicked.connect(cb)

    def get_id(self) -> Optional[str]:
        return (
            self.ui.list.currentItem().data(Qt.StatusTipRole)
            if self.ui.list.currentItem()
            else None
        )

    def get_name(self) -> Optional[str]:
        return (
            self.ui.list.currentItem().data(Qt.EditRole)
            if self.ui.list.currentItem()
            else None
        )

    def extend_params(self, data: dict):
        self.params = {**self.params, **data}

    def set_endpoint(self, endpoint: str):
        self.endpoint = endpoint

    def reset(self):
        self.ui.list.clear()

    def fill_select(self):
        search = self.ui.filter_text.text()
        page = self.ui.page.value()
        if (
            self.params["search"] == search
            and page > self.params["page_number"]
            and self.has_next is False
        ):
            self.ui.page.setValue(1)
            return
        if self.params["search"] != search:
            page = 1
            self.ui.page.setValue(1)
        self.params["search"] = search
        self.params["page_number"] = page
        data, has_next = self.api.get_list(self.endpoint, self.params)
        self.has_next = has_next
        self.ui.list.clear()
        if len(data) == 0:
            self.warn_box = warn_box("No %s, check search parameters" % self.resource)
            self.warn_box.show()
        else:
            for d in data:
                widget = QListWidgetItem()
                widget.setData(Qt.EditRole, d["name"])
                widget.setData(Qt.StatusTipRole, d["id"])
                self.ui.list.addItem(widget)

    def show_object_info(self) -> None:
        """Open an info dialog showing TODO metadata"""
        id = self.get_id()
        if id is None:
            self.warn_box = warn_box("Select a %s first" % self.resource)
            self.warn_box.show()
            return
        data: dict[str, Any] = self.api.get_resource(self.endpoint, id)
        body = ""
        for key, val in data.items():
            body += "%s: %s\n" % (key, json.dumps(val, indent=4, sort_keys=True))
        self.info_box = info_box(self.resource, body)
        self.info_box.show()

    def open_object_web(self) -> None:
        """TODO"""
        id = self.get_id()
        if id is None:
            self.errbox = error_box("Select a %s first" % self.resource)
            self.errbox.show()
        else:
            url = self.api.get_resource_link(self.resource, id)
            QDesktopServices.openUrl(QUrl(url))
