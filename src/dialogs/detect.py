# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import QDialog

from ...dialog_detect import Ui_PicterraDialogDetect as Ui_Detect
from ..api import ApiError
from ..utils import Logger
from .boxes import error_box, info_box, warn_box
from .utils import DialogData, PicterraDialogSelect, tr

logger = Logger(__file__)


class PicterraDialogDetect(QDialog):
    """
    The Qt Dialog for prediction, from dialog_detect.ui
    .
    Allows the user to select a detector, one (or more) raster(s),
    and run the prediction on it (them)
    """

    def __init__(self, data: DialogData):
        """Constructor."""
        super().__init__()
        self.ui = Ui_Detect()
        self.ui.setupUi(self)
        # Store API wrapper and QGIS UI accessor in the class
        self.api = data["api"]
        self.iface = data["iface"]
        # TODO
        self.ui.detect_cta.setEnabled(False)
        self.ui.detect_cta.clicked.connect(self.detect)
        # TODO
        def detector_cb():
            if self.select_raster.get_id() is not None:
                self.ui.detect_cta.setEnabled(True)

        self.select_detector = PicterraDialogSelect(
            "detectors", {"is_runnable": True}, self.api, selected_callback=detector_cb
        )
        self.ui.detector_choice_layout.addWidget(self.select_detector)
        # TODO
        def folder_cb(v):
            self.select_raster.extend_params({"folder": v})
            self.select_raster.fill_select()
            self.ui.detect_cta.setEnabled(False)

        self.select_folder = PicterraDialogSelect(
            "folders", {}, self.api, True, folder_cb
        )
        self.ui.folder_choice_layout.addWidget(self.select_folder)
        # TODO
        def raster_cb(v):
            if self.select_detector.get_id() is not None:
                self.ui.detect_cta.setEnabled(True)

        self.select_raster = PicterraDialogSelect(
            "rasters", {}, self.api, False, raster_cb
        )
        self.ui.raster_choice_layout.addWidget(self.select_raster)

    def detect(self):
        """CTA callback for the prediction dialog"""
        # These below should not happen unless we have a bug in the PicterraDialogSelect callbacks
        d_id, r_id, f_id = (
            self.select_detector.get_id(),
            self.select_raster.get_id(),
            self.select_folder.get_id(),
        )
        if d_id is None:
            self.warn_box = warn_box("Select a detector first")
            self.warn_box.show()
            return
        elif f_id is None:
            self.warn_box = warn_box("Select a project first")
            self.warn_box.show()
            return
        elif r_id is None:
            self.warn_box = warn_box("Select a raster first")
            self.warn_box.show()
            return
        # TODO
        logger.debug("Detecting with: " + ",".join([d_id, f_id, r_id]))
        try:
            self.api.detect(d_id, r_id)
            self.info_box = info_box(
                "Detection started",
                "Prediction with %s on %s started, check the activity to track its progress."
                % (self.select_raster.get_name(), self.select_detector.get_name()),
            )
            self.info_box.show()
        except ApiError as e:
            err_msg = "Detect failed: " + str(e)
            logger.error(err_msg)
            self.err_box = error_box(err_msg)
            self.err_box.show()
