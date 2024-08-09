# -*- coding: utf-8 -*-
import json
from typing import List

from qgis.gui import QgsFileWidget
from qgis.PyQt.QtCore import QStringListModel
from qgis.PyQt.QtGui import QMovie
from qgis.PyQt.QtWidgets import QDialog

from ...dialog_upload import Ui_Dialog as Ui_Upload
from ..api import ApiError
from ..utils import Logger, get_file_info, tr
from .boxes import error_box, info_box
from .utils import DialogData, PicterraDialogSelect

logger = Logger(__file__)


class PicterraDialogUpload(QDialog):
    def __init__(self, data: DialogData):
        """
        Constructor: set icons and signal/slots.

        Loads the api accessor, set the upload CTO icon button
        but temporarily hides it; setup the file selection
        widget to accept only file image(s), connect the callback
        displaying the CTO to the file selection.

        Uses
          *  https://qgis.org/api/classQgsFileWidget.html,
          *  https://doc.qt.io/qt-5/qstringlistmodel.html
        """
        super().__init__()
        # Trace upload status
        self.counter = 0
        self.file_num = 0
        # Type of upload
        self.upload_type = ""
        # Model for all selected file names (not paths)
        self.model = QStringListModel()
        # API to access upload function
        self.api = data["api"]
        self.iface = data["iface"]
        # Root reference to all Qt widgets
        self.ui = Ui_Upload()
        # From here we can access dialog ui objects
        self.ui.setupUi(self)
        # The model will be linked to the file name list
        self.ui.file_list.setModel(self.model)
        # Hides widgets used only when we have at least one selected image
        self.ui.start_upload_button_raster.setEnabled(False)
        self.ui.start_upload_button_detectionareas.setEnabled(False)
        # Setup file selection for the raster upload
        r_file_widget: QgsFileWidget = self.ui.file_selector_raster
        r_file_widget.setFilter("Images (*.png *.xpm *.jpg *.tif *.tiff)")
        r_file_widget.fileChanged.connect(self._on_geojson_selection_raster)
        # Setup file selection for the detection area upload
        da_file_widget: QgsFileWidget = self.ui.file_selector_detectionareas
        da_file_widget.setFilter("GeoJSON (*.json *.geojson)")
        da_file_widget.fileChanged.connect(self._on_geojson_selection_detectionareas)
        # TODO
        self.ui.spinner_label.hide()
        # raster
        self.folder_id_raster = None

        def folder_cb(v):
            self.folder_id_raster = v

        self.raster_imagery_folder = PicterraDialogSelect(
            "folders", {}, self.api, selected_callback=folder_cb
        )
        self.ui.raster_folder_select.addWidget(self.raster_imagery_folder)
        self.ui.start_upload_button_raster.clicked.connect(self.start_raster_upload)
        # detection areas
        self.raster_id_detectionareas = None

        def folder_cb_2(v):
            self.imagery_rasters_das.extend_params({"folder": v})
            self.imagery_rasters_das.fill_select()

        self.imagery_folder_das = PicterraDialogSelect(
            "folders", {}, self.api, True, folder_cb_2
        )
        self.ui.detectionareas_folder_select.addWidget(self.imagery_folder_das)

        def raster_cb(raster_id):
            self.raster_id_detectionareas = raster_id

        self.imagery_rasters_das = PicterraDialogSelect(
            "rasters", {}, self.api, False, raster_cb
        )
        self.ui.detectionareas_raster_select.addWidget(self.imagery_rasters_das)
        self.ui.start_upload_button_detectionareas.clicked.connect(
            self.start_detectionarea_upload
        )

    def _on_geojson_selection_raster(self) -> None:
        if self.folder_id_raster:
            self.ui.start_upload_button_raster.setEnabled(True)
        file_paths = QgsFileWidget.splitFilePaths(self.ui.file_selector_raster.filePath())
        self.model.setStringList(get_file_info(f)["name"] for f in file_paths)
        self.ui.label_filelist.setText(
            "%s (%d):" % (tr("Selected images"), self.model.rowCount())
        )

    def _on_geojson_selection_detectionareas(self) -> None:
        if self.raster_id_detectionareas:
            self.ui.start_upload_button_detectionareas.setEnabled(True)

    def start_detectionarea_upload(self) -> None:
        self.file_num = 1
        self.upload_type = "detection area"
        raster_id = self.raster_id_detectionareas
        geojson_filepath: str = self.ui.file_selector_detectionareas.filePath()
        logger.debug("Getting detection area from %s" % geojson_filepath)
        # Disable CTO, display progress bar
        self.ui.start_upload_button_detectionareas.setEnabled(False)
        # Shows spinner
        # Hide file selector
        self.ui.file_selector_detectionareas.hide()
        self.ui.upload_detectionarea_selection_label.hide()
        # Check file is not empty
        info = get_file_info(geojson_filepath)
        if info is None:
            logger.error(
                "Error upload invalid detection area file %s" % geojson_filepath
            )
            self.err_box = error_box("Failed to open " + geojson_filepath)
            self.err_box.show()
            self._reset()
            return
        file_size = info["size"]
        if file_size == 0:
            self.err_box = error_box("File %s should not be empty" % geojson_filepath)
            self.err_box.show()
            self._reset()
            return
        # Check file is JSON (open it as text)
        with open(geojson_filepath, "rt") as file_to_check:
            try:
                json.load(file_to_check)
            except ValueError as e:
                logger.error("Error upload invalid vector layer detection area %s" % e)
                self.err_box = error_box("Detection areas should be GeoJSON")
                self.err_box.show()
                self._reset()
                return
        with open(geojson_filepath, "rb") as f:      
            content = f.read()
            f.close()
            logger.debug("Uploading detection area for %s .." % raster_id)
            try: # Launch upload thread
                self.api.upload_detectionarea(
                    raster_id,
                    "application/json",
                    content,
                    file_size,
                    self._upload_callback,
                )
            except ApiError as e:
                error = str(e)
                self.err_box = error_box(error)
                self.err_box.show()
                self._reset()

    def start_raster_upload(self) -> None:
        """
        Use the API to (asynchronously) upload files to Picterra.

        Default callback for clicking on CTO (default virtual slot for "OK")
        1. Parse file list to obtain array of file paths, size and contents
        2. For each file launch the async API upload and setup callback

        The upload and processing is done in parallel by a set of worker
        threads.
        In case of errors from the API wrapper it shows an error dialog.

        https://qgis.org/pyqgis/3.2/gui/File/QgsFileWidget.html#qgis.gui.QgsFileWidget.filePath
        """
        self.upload_type = "raster"
        self.ui.label_select.show()
        self.ui.file_selector_raster.show()
        file_paths = QgsFileWidget.splitFilePaths(
            self.ui.file_selector_raster.filePath()
        )
        self.file_num = len(file_paths)
        if self.file_num == 0:
            self.err_box = error_box("Please select at least one file")
            self.err_box.show()
            return
        folder_id = self.folder_id_raster
        if folder_id is None:
            self.err_box = error_box("Please select the upload folder")
            self.err_box.show()
            return
        self.ui.label_select.hide()
        self.ui.file_selector_raster.hide()
        self.ui.start_upload_button_raster.setEnabled(False)
        logger.debug("%s files to upload" % str(self.file_num))
        self.movie = QMovie(":/plugins/picterra/assets/spinner.gif")
        self.ui.spinner_label.show()
        self.ui.spinner_label.setMovie(self.movie)
        self.movie.start()
        failed_files: List[str] = []
        for file_path in file_paths:
            info = get_file_info(file_path)
            if not info:
                failed_files.append(file_path)
                continue
            with open(file_path, "rb") as f:
                content = f.read()
                f.close()
                logger.debug("Uploading %s .." % info["name"])
                try:
                    self.api.upload_raster(  # Launch upload thread (commit will be just launched)
                        info["name"], info["mime"], content, info["size"], folder_id,
                    )
                except ApiError as e:
                    error = str(e)
                    self.err_box = error_box(error)
                    self.err_box.show()
                    failed_files.append(file_path)
        self.movie.stop()
        self.ui.spinner_label.hide()
        self.info_box = info_box(
            "Raster uploads started",
            "Started uploading %s rasters, %d failed (%s)."
            % (
                len(file_paths) - len(failed_files),
                len(failed_files),
                ', '.join(failed_files)
            ),
        )
        self.info_box.show()
        self._reset()

    def _reset(self) -> None:
        """Resets the upload dialog tabs elements"""
        self.ui.file_selector_raster.setFilePath("")
        self.counter = 0
        self.file_num = 0
        self.ui.start_upload_button_raster.setEnabled(False)
        self.ui.label_select.show()
        self.ui.file_selector_raster.show()
        self.ui.upload_detectionarea_selection_label.show()
        self.ui.file_selector_detectionareas.show()
        if self.movie:
            self.movie.stop()
        self.ui.spinner_label.hide()
