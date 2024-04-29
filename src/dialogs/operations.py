# -*- coding: utf-8 -*-
from typing import Dict

from qgis.PyQt.QtGui import QMovie
from qgis.PyQt.QtWidgets import QDialog, QTableWidgetItem

from ...dialog_operations import Ui_Dialog as Ui_Operations
from ..api import ApiError
from ..utils import Logger
from .boxes import error_box
from .utils import DialogData

logger = Logger(__file__)


class PicterraDialogOperations(QDialog):
    """
    Qt dialog showing TODO.

    Its Qt base UI is in dialog_operations.ui (Ui_Operationss).
    """

    def __init__(self, data: DialogData):
        super().__init__()
        self.ui = Ui_Operations()
        self.ui.setupUi(self)
        self.iface = data["iface"]
        self.api = data["api"]
        self.ui.reset_button.clicked.connect(lambda: self.api.reset_operations())
        try:
            self.operations = self.api.load_operations()
        except ApiError as e:
            err_msg = "Error loading recent activity: " + str(e)
            logger.error(err_msg)
            self.err_box = error_box(err_msg)
            self.err_box.show()
            self.operations = {}
        op_cnt = len(self.operations.keys())
        logger.info("Loaded %s operations" % str(op_cnt))
        table = self.ui.tableWidget
        table.setRowCount(op_cnt)
        labels = ["Date", "Name", "Status", "Raster", "Detector"]
        table.setColumnCount(len(labels))
        table.setHorizontalHeaderLabels(labels)
        running_ops: Dict[str, int] = {}
        for i, op in enumerate(self.operations.values()):
            table.setItem(i, 0, QTableWidgetItem(op["type"]))
            table.setItem(i, 1, QTableWidgetItem(op["status"]))
            if op["status"] == "running":
                o_id = op["id"]
                running_ops[o_id] = i
            if op["raster"]:
                table.setItem(i, 2, QTableWidgetItem(op["raster"]["name"]))
            if op["detector"]:
                table.setItem(i, 3, QTableWidgetItem(op["detector"]["name"]))
        if len(running_ops) != 0:
            spn_lbl = self.ui.spinner_label
            movie = QMovie(":/plugins/picterra/assets/spinner.gif")
            spn_lbl.setMovie(movie)
            spn_lbl.show()
            movie.start()

            def cb(op_id, new_status):
                self.operations[op_id]["status"] = new_status
                idx = running_ops.pop(op_id)
                table.setItem(idx, 1, QTableWidgetItem(new_status))
                if len(running_ops) == 0:
                    spn_lbl.hide()

            self.api.start_async_polling(running_ops.keys(), cb)
