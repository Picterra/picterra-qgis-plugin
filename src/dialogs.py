# -*- coding: utf-8 -*-
import json
from math import floor
from time import sleep
from typing import Optional
from urllib.parse import urlparse

from qgis.gui import QgsFileWidget
from qgis.PyQt.QtCore import QLocale, QStringListModel, Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon, QMovie, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QWidget,
)

try:
    from qgis.core import Qgis
except ImportError:
    from qgis.core import QGis as Qgis

from ..dialog_detect import Ui_PicterraDialogDetect as Ui_Detect
from ..dialog_results import Ui_results_dialog as Ui_Results
from ..dialog_settings import Ui_Settings
from ..dialog_upload import Ui_Dialog as Ui_Upload
from .api import ApiError
from .utils import (
    ALLOWED_SETTINGS,
    FaqLinks,
    Logger,
    download_file,
    get_api_server,
    get_file_info,
    get_platform_url,
    get_plugin_metadata,
    get_setting,
    open_faq_link,
    set_setting,
    tr,
)

logger = Logger(__file__)


class PicterraDialogDetect(QDialog):
    """
    The Qt Dialog for prediction, from dialog_detect.ui
    .
    Allows the user to select a detector, one (or more) raster(s),
    and run the prediction on it (them)
    """

    def __init__(self, parent=None, data=None):
        """Constructor."""
        super().__init__()
        self.ui = Ui_Detect()
        self.ui.setupUi(self)
        # Store API wrapper and QGIS UI accessor in the class
        self.api = data["api"]
        self.iface = data["iface"]
        # No raster is selected by default
        self.ui.button_box.button(QDialogButtonBox.Ok).setEnabled(False)
        # Signal on selecting elements
        self.ui.rasters_menu.itemSelectionChanged.connect(self.select_raster)
        # Signal on info button click
        self.ui.button_box.helpRequested.connect(self.help)
        # Info button shows raster info dialog when clicked
        self.ui.info_selected_detector.clicked.connect(
            lambda: self.show_object_info("detector"))
        self.ui.link_selected_detector.setIcon(
            QIcon(':/plugins/picterra/assets/platform.png'))
        self.ui.link_selected_detector.clicked.connect(
            lambda: self.open_object_web())
        # Fill the dropdown menus with the list of available
        # (trained) detectors and (uploaded) rasters names,
        # ordered by name (while API returns order by timestamp)
        try:
            rasters_list = self.api.get_rasters()
            detectors_list = self.api.get_detectors()
            for r in sorted(rasters_list, key=lambda x: x['name']):
                widget = QListWidgetItem()
                widget.setData(Qt.EditRole, r["name"])
                widget.setData(Qt.StatusTipRole, r["id"])
                self.ui.rasters_menu.addItem(widget)
            for d in sorted(detectors_list, key=lambda x: x['name']):
                self.ui.detector_menu.addItem(d["name"], d["id"])
            # Actions when dialog is opened with a non-empty selection
            if 'index' in data:
                if data['type'] == 'raster':
                    self.ui.rasters_menu.setCurrentRow(data['index'])
                elif data['type'] == 'detector':
                    self.ui.detector_menu.setCurrentIndex(data['index'])
        # Show error if above API calls fails
        except ApiError as e:
            error = str(e)
            logger.error(error)
            self.err_box = error_box(error)
            self.err_box.show()

    def accept(self):
        """
        Default CTO callback for the prediction dialog: start detection and show progress

        Firstly we get the chosen raster and detectors identifers,
        then we launch an asynchronous detection thread (via API),
        passing a callback that updates the operation progress dialog:
        the latter shows a spinner until the finish callback is executed
        or an error is raised by the API wrapper.
        """
        # Gets raster and detector id (for the API call)
        raster_items = self.ui.rasters_menu.selectedItems()
        detector_id = self.ui.detector_menu.currentData()
        # Shows upload progress dialog
        data = {
            "iface": self.iface,
            "detection_data": {
                "detector": self.ui.detector_menu.currentText(),
                "rasters": raster_items,
            },
        }
        # Creates dialog result
        self.ui.dlg_result = PicterraDialogResults(parent=self, data=data)
        # Fill the dictionary that will map rasters with their results
        self.ui.dlg_result.results_dict = {
            r.data(Qt.StatusTipRole): None for r in raster_items
        }
        self.ui.dlg_result._update_current_raster()
        # Launch async detection on thread and returns
        try:
            for raster_item in raster_items:
                raster_id = raster_item.data(Qt.StatusTipRole)
                self.api.detect(
                    detector_id,
                    raster_id,
                    self.ui.dlg_result._result_callback
                )
        except ApiError as e:
            logger.error(str(e))
            self.ui.dlg_result._error_callback(raster_id)
        # Show loading spinner while waiting detection to finish
        spn_lbl = self.ui.dlg_result.ui.spinner_label
        movie = QMovie(":/plugins/picterra/assets/spinner.gif")
        spn_lbl.setMovie(movie)
        spn_lbl.show()
        movie.start()
        # Opens PicterraDialogResults QDialog
        self.ui.dlg_result.ui.detection_name_label.hide()
        self.ui.dlg_result.show()

    def help(self) -> None:
        """Show an info box with help info"""
        self.info_box = info_box(
            tr("Help"), tr("Select detector and  raster you want to search objects on")
        )
        self.info_box.show()

    def select_raster(self) -> None:
        sel_rasters_no = len(self.ui.rasters_menu.selectedItems())
        self.ui.label_selected_rasters.setText(
            str(sel_rasters_no) + tr(" rasters selected")
            if sel_rasters_no
            else tr("Select detector and raster(s) to use")
        )
        self.ui.button_box.button(QDialogButtonBox.Ok).setEnabled(sel_rasters_no > 0)

    def show_object_info(self, type: Optional[str]) -> None:
        """Open an info dialog showing raster/detector metadata"""
        body = ""
        if type == "detector":
            id = self.ui.detector_menu.currentData()
            name = self.ui.detector_menu.currentText()
            body = "Type: detector\nId: %s\nName: '%s'" % (id, name)
        self.info_box = info_box("object", body)
        self.info_box.show()

    def open_object_web(self, object_type: Optional[str] = None) -> None:
        """Opens the Picterra web app, optionally on a given entity"""
        host = get_platform_url()
        if object_type == "detector":
            object_id = self.detector_menu.currentData()
            url = host + "/detectors/custom/" + object_id + "/run"
        else:
            url = host
        QDesktopServices.openUrl(QUrl(url))


class PicterraDialogResults(QDialog):
    """
    Qt dialog showing the status of a prediction.

    Its Qt base UI is in dialog_results.ui (Ui_Results).
    """

    def __init__(self, parent=None, data=None):
        """Class constructor."""
        super().__init__()
        # Load and stores Qt UI widget
        self.ui = Ui_Results()
        self.ui.setupUi(self)
        # Maps raster UUID with their result GeoJSON: this is fulfilled
        # by the detectdialog when launching the predictions
        self.results_dict = dict()
        self.current_raster_id = None
        # Access to QGIS UI
        self.iface = data["iface"]
        self.detection_data = data["detection_data"]
        for item in self.detection_data["rasters"]:
            self.ui.rasters_selector_menu.addItem(
                item.data(Qt.DisplayRole), item.data(Qt.StatusTipRole)
            )
        self.ui.rasters_selector_menu.currentIndexChanged.connect(
            self._update_current_raster
        )
        rasters_num = len(self.detection_data["rasters"])
        self.ui.title_label.setText(
            "%s <i>%s</i> %s %d %s"
            % (
                tr("Detection with"),
                self.detection_data["detector"],
                tr("on"),
                rasters_num,
                tr("images" if rasters_num > 1 else "image"),
            )
        )
        # Signal
        self.ui.add_to_project_button.clicked.connect(self._add_to_project)
        self.ui.add_to_project_button.setIcon(
            QIcon(":/plugins/picterra/assets/add.png")
        )
        self.ui.add_to_project_button.show()
        # show "download" button and setup signal
        self.ui.download_button.clicked.connect(self._download)
        self.ui.download_button.setIcon(QIcon(":/plugins/picterra/assets/download.png"))
        # Hide buttons for managing detection results
        self.ui.add_to_project_button.hide()
        self.ui.download_button.hide()
        parent.close()

    def _add_to_project(self) -> None:
        """Add detection results to current QGIS project"""
        geojson_url = self.results_dict[self.current_raster_id]
        logger.debug("Remote geojson url is %s" % geojson_url)
        geojson = download_file(geojson_url)
        if not geojson:
            self.err_box = error_box("Error while adding result to project")
            self.err_box.show()
            logger.error("Error while adding result to project")
            return
        name = self.ui.detection_name_label.text()
        vlayer = self.iface.addVectorLayer(geojson, name, "ogr")
        if not vlayer.isValid():
            logger.warning("Layer failed to load!")
        else:
            logger.info("Layer loaded")

    def _download(self) -> None:
        """Open the dialog allowing saving results on disk"""
        # Retrieve results URL and download geometry file
        geojson_url = self.results_dict[self.current_raster_id]
        geojson = download_file(geojson_url)
        if not geojson:
            self.err_box = error_box("Error while adding result to project")
            self.err_box.show()
            logger.error("Error while adding result to project")
            return
        # Shows "Save as" file dialog
        name = self.ui.detection_name_label.text()
        file_name = QFileDialog.getSaveFileName(
            caption=tr("Select where to save file"),
            directory=name + ".geojson",
        )
        if len(file_name[0]) > 0:
            # Save on disk
            with open(file_name[0], "w") as f:
                f.write(geojson)  # type: ignore
                f.close()

    def _update_current_raster(self) -> None:
        """Updates the label of the currently selected raster"""
        self.current_raster_id = self.ui.rasters_selector_menu.currentData()
        self.ui.download_button.hide()
        self.ui.add_to_project_button.hide()
        # By default the status is processing (ongoing detection)
        label = tr("processing")
        # Information on this single prediction
        raster_name = self.ui.rasters_selector_menu.currentText()
        detector_name = self.detection_data["detector"]
        result_name = "detection with %s on %s" % (detector_name, raster_name)
        # Check detection has finished (successfully or not)
        if self.results_dict[self.current_raster_id]:
            # Show dialog for name of the detection/file with the result
            self.ui.detection_name_label.setText(result_name)
            self.ui.detection_name_label.show()
            # Show "add to project" button and setup signal
            self.ui.download_button.show()
            self.ui.add_to_project_button.show()
            # Prepare new status label
            label = tr("finished")
        elif self.results_dict[self.current_raster_id] is False:
            # Prepare new status label
            logger.error(result_name + " failed")
            label = tr("failed")
        # Update status label in the UI
        logger.debug("Updating %s with status=%s" % (result_name, label))
        self.ui.selected_raster_status_label.setText(label)

    def _result_callback(self, return_dict: Optional[dict]):
        """
        Function called once detection is finished.

        Adds to the dialog two buttons (with icons) to download or save the
        current results, connecting them to the relative callbacks; replaces
        the detection progress spinner with an "Ok" icon.
        """
        if not return_dict:
            error = tr(
                """Operation failed: please try again.
                 In case the error persists please contact """
            )
            email = get_plugin_metadata()["email"]
            self.err_box = error_box(error + email)
            self.err_box.show()
            logger.error("Detection failed")
            return
        geojson = return_dict["geojson_url"]
        raster_id = return_dict["raster_id"]
        # Save the result url
        self.results_dict[raster_id] = geojson
        self._update_current_raster()
        # Update overall detections status
        self.update_overall_status()
        # Log end of (partial) detection
        logger.info("Detection on %s has finished successfully" % raster_id)

    def _error_callback(self, raster_id):
        self.results_dict[raster_id] = False
        self.update_overall_status()

    def update_overall_status(self) -> None:
        # All detections went well
        if all(self.results_dict.values()):
            self.ui.spinner_label.setPixmap(QPixmap(":/plugins/picterra/assets/ok.png"))
            self.ui.spinner_label.unsetCursor()
            logger.info(
                "All %d detections were completed" % len(self.results_dict.values())
            )
            self.info_box = info_box(
                tr("Detections ended"), tr("All the predictions finished")
            )
            self.info_box.show()
        # All detections failed
        elif all([r is False for r in self.results_dict.values()]):
            self.ui.spinner_label.setPixmap(
                QPixmap(":/plugins/picterra/assets/error.png")
            )
            self.ui.spinner_label.unsetCursor()
        # All detections ended, at least one with an error
        elif all([r is not None for r in self.results_dict.values()]):
            self.ui.spinner_label.setPixmap(
                QPixmap(":/plugins/picterra/assets/warning.png")
            )
            self.ui.spinner_label.unsetCursor()
        else:
            # We simply keep the spinner
            pass


class PicterraDialogUpload(QDialog):
    def __init__(self, parent=None, data=None):
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
        self.ui.start_upload_button.setIcon(
            QIcon(":/plugins/picterra/assets/upload2.png")
        )
        self.ui.start_detectionarea_upload_pushbutton.setIcon(
            QIcon(":/plugins/picterra/assets/upload2.png")
        )
        # Set the link actions for the "info" buttons
        self.ui.info_detection_areas.clicked.connect(
            lambda: open_faq_link(FaqLinks.DETECTION_AREAS)
        )
        self.ui.info_upload_images.clicked.connect(
            lambda: open_faq_link(FaqLinks.SUPPORTED_IMAGES)
        )
        # Hides widgets used only when we have at least one selected image
        self.ui.start_upload_button.setEnabled(False)
        self.ui.progress_bar.hide()
        self.ui.progress_bar_label.hide()
        # Hides widgets used only when we have one file and one raster
        self.ui.start_detectionarea_upload_pushbutton.setEnabled(False)
        self.ui.detectionarea_progressbar.hide()
        self.ui.detectionarea_progress_bar_label.hide()
        # Fill the list of rasters
        try:
            rasters_list = self.api.get_rasters()
            for r in sorted(rasters_list, key=lambda x: x['name']):
                self.ui.raster_selection_combobox.addItem(r["name"], r["id"])
        except ApiError as e:
            error = str(e)
            self.err_box = error_box(error)
            self.err_box.show()
            logger.error(error)
            return
        # Setup file selection for the raster upload
        r_file_widget: QgsFileWidget = self.ui.file_selector
        r_file_widget.fileChanged.connect(self._on_raster_selection_changed)
        r_file_widget.setStorageMode(QgsFileWidget.GetMultipleFiles)
        r_file_widget.setFilter("Images (*.png *.xpm *.jpg *.tif *.tiff)")
        # Setup file selection for the detection area upload
        da_file_widget: QgsFileWidget = self.ui.detectionarea_file_selector
        da_file_widget.fileChanged.connect(self._on_geojson_selection)
        da_file_widget.setStorageMode(QgsFileWidget.GetFile)
        da_file_widget.setFilter("GeoJSON (*.json *.geojson)")
        # Setup CTO signals
        self.ui.start_upload_button.clicked.connect(
            self.start_raster_upload)
        self.ui.start_detectionarea_upload_pushbutton.clicked.connect(
            self.start_detectionarea_upload)
        # Actions when dialog is opened with a non-empty selection
        if 'type' in data:
            if data['type'] == 'detection_area':
                self.ui.tab_upload.setCurrentIndex(1)
                self.ui.raster_selection_combobox.setCurrentIndex(data['index'])

    def _on_geojson_selection(self) -> None:
        self.ui.start_detectionarea_upload_pushbutton.setEnabled(True)
        self.ui.detectionarea_progressbar.setValue(0)

    def _on_raster_selection_changed(self) -> None:
        """
        Executed when the user select one or more files, shows upload CTO.

        Once at least one file is selected when can proceed with the upload.
        """
        # Show CTO, reset progress bar
        self.ui.start_upload_button.setEnabled(True)
        self.ui.progress_bar.setValue(0)
        # create model/view for file list
        file_paths = QgsFileWidget.splitFilePaths(self.ui.file_selector.filePath())
        self.model.setStringList(get_file_info(f)["name"] for f in file_paths)
        self.ui.label_filelist.setText(
            "%s (%d):" % (tr("Selected images"), self.model.rowCount())
        )

    def start_detectionarea_upload(self) -> None:
        self.file_num = 1
        self.upload_type = "detection area"
        raster_id = self.ui.raster_selection_combobox.currentData()
        geojson_filepath = self.ui.detectionarea_file_selector.filePath()
        logger.debug("Getting detection area from %s" % geojson_filepath)
        # Disable CTO, display progress bar
        self.ui.start_detectionarea_upload_pushbutton.setEnabled(False)
        self.ui.detectionarea_progressbar.show()
        self.ui.progress_bar.setValue(0)
        # Shows spinner
        spn_lbl = self.ui.da_progress_label
        movie = QMovie(":/plugins/picterra/assets/spinner.gif")
        spn_lbl.setMovie(movie)
        spn_lbl.show()
        movie.start()
        # Show progress bar
        self.ui.detectionarea_progress_bar_label.show()
        # Hide file selector
        self.ui.detectionarea_file_selector.hide()
        self.ui.upload_detectionarea_selection_label.hide()
        # Check file is not empty
        info = get_file_info(geojson_filepath)
        file_size = info["size"]
        if file_size == 0:
            self.err_box = error_box("File should not be empty")
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
        # Open image file as binary
        with open(geojson_filepath, "rb") as f:
            # Load file content
            content = f.read()
            f.close()
            logger.debug("Uploading detection area for %s .." % raster_id)
            # Launch upload thread
            try:
                self.api.upload_detectionarea(
                    raster_id,
                    "application/json",
                    content,
                    file_size,
                    self._upload_callback,
                )
            except ApiError as e:
                error = str(e)
                logger.error(error)
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
        self.ui.file_selector.show()
        file_paths = QgsFileWidget.splitFilePaths(self.ui.file_selector.filePath())
        self.file_num = len(file_paths)
        if self.file_num == 0:
            self.err_box = error_box("Please select at least one file")
            self.err_box.show()
            return
        # Shows progress bar
        self.ui.progress_bar.show()
        self.ui.progress_bar_label.show()
        # Shows spinner
        spn_lbl = self.ui.progress_label
        movie = QMovie(":/plugins/picterra/assets/spinner.gif")
        spn_lbl.setMovie(movie)
        spn_lbl.show()
        movie.start()
        self.ui.label_select.hide()
        self.ui.file_selector.hide()
        self.ui.start_upload_button.setEnabled(False)
        logger.debug("%s files to upload" % str(self.file_num))
        # Loop over files
        for file_path in file_paths:
            # Check whether or not file exists
            info = get_file_info(file_path)
            if not info:
                continue
            # Open image file as binary
            with open(file_path, "rb") as f:
                content = f.read()
                f.close()
                # Note the name is the one of the file, not the layer
                logger.debug("Uploading %s .." % info["name"])
                # Launch upload thread
                try:
                    self.api.upload_raster(
                        info["name"],
                        info["mime"],
                        content,
                        info["size"],
                        self._upload_callback,
                    )
                except ApiError as e:
                    error = str(e)
                    logger.error(error)
                    self.err_box = error_box(error)
                    self.err_box.show()
                    self._reset()

    def _upload_callback(self, flag: bool) -> None:
        """
        Updates dialog showing the positive upload outcome of a single raster / area.

        This function is called as a callback by the API wrapper triggered by the CTO
        in the upload dialog: in case the argument is valid (meaning the object was
        successfully uploaded), the progress bar is updated (percentage on the
        total number of items selected), then if all objects were processed resets
        the internal counts and displays a text about the whole operation completion.
        """
        if flag:
            self.counter += 1
            logger.info("Upload %s terminated" % str(self.counter))
            # Updates progress bar
            self.ui.progress_bar.setValue(floor(100 * (self.counter / self.file_num)))
            sleep(1)
            # Check if all uploads are finished
            if self.ui.progress_bar.value() == 100:
                sleep(1)
                self.ui.progress_label.setText(tr("Upload finished"))
                logger.info("All %s uploads were completed" % str(self.file_num))
                # Reset status and disable CTO until new file selection
                self.info_box = info_box(
                    tr("Upload completed"),
                    tr("The " + self.upload_type + " upload finished"),
                )
                self.info_box.show()
                self._reset()
        else:
            logger.error("Upload failure")
            error = tr(
                """Upload operation failed: please try again.
            In case the error persists please contact """
            )
            email = get_plugin_metadata()["email"]
            self.err_box = error_box(error + email)
            self.err_box.show()

    def _reset(self) -> None:
        """Resets the upload dialog tabs elements"""
        self.ui.file_selector.setFilePath("")
        self.counter = 0
        self.file_num = 0
        self.ui.start_upload_button.setEnabled(False)
        self.ui.progress_bar.hide()
        self.ui.progress_bar_label.hide()
        self.ui.label_select.show()
        self.ui.file_selector.show()
        self.ui.upload_detectionarea_selection_label.show()
        self.ui.da_progress_label.setText(tr("Upload a new detection area"))
        self.ui.progress_label.setText(tr("Upload a new raster"))
        self.ui.detectionarea_file_selector.show()


class PicterraDialogSettings(QWidget):
    """
    QDialog representing the settings of the plugin

    For ref see https://doc.qt.io/qt-5/qdialogbuttonbox.html
    """

    def __init__(self, parent=None, data=None):
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
        try:
            if self.api.ping() == 200:
                self.ui.nr_rasters.setText(str(
                    len(self.api.get_rasters())))
                self.ui.nr_detectors.setText(str(
                    len(self.api.get_detectors())))
        except ApiError as e:
            logger.error(str(e))
            self.ui.nr_rasters.setText(tr("N/A"))
            self.ui.nr_detectors.setText(tr("N/A"))
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


def info_box(title: str, body: str) -> QMessageBox:
    """Shows an info box"""
    obj = QMessageBox()
    obj.setIcon(QMessageBox.Information)
    obj.setWindowTitle("Picterra - " + str(title))
    obj.setText(str(body))
    return obj


def error_box(body: str) -> QMessageBox:
    """Shows an error box."""
    obj = QMessageBox()
    obj.setIcon(QMessageBox.Critical)
    obj.setWindowTitle("Error from Picterra")
    obj.setText(str(body))
    return obj


def consensus_box(title: str, body: str):
    """
    Shows dialog asking yes or no and return boolean response.

    https://doc.qt.io/qtforpython/PySide2/QtWidgets/QMessageBox.html#PySide2.QtWidgets.PySide2.QtWidgets.QMessageBox.question
    """
    return QMessageBox.question(None, title, body) == QMessageBox.Yes
