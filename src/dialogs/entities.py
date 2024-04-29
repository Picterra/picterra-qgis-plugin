# -*- coding: utf-8 -*-
from typing import List

from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer
from qgis.PyQt.QtWidgets import QDialog

try:
    from qgis.core import QgsMarkerSymbol
except ImportError:
    from qgis.core import QGis as QgsMarkerSymbol

from ...dialog_entities import Ui_Dialog as UI_Entities
from ..api import API
from ..utils import Logger, download_file
from .boxes import error_box, warn_box
from .utils import PicterraDialogSelect

logger = Logger(__file__)


class PicterraDialogEntities(QDialog):
    """
    Qt dialog showing list of rasters (by folder), detectors and layers (by raster).
    """

    def __init__(self, api: API):
        super().__init__()
        self.ui = UI_Entities()
        self.ui.setupUi(self)
        # detectors
        self.detectors = PicterraDialogSelect("detectors", {}, api, hide_select=True)
        self.ui.detectors_list.addWidget(self.detectors)
        # imagery
        def folder_cb_1(v):
            self.imagery_rasters.extend_params({"folder": v})
            self.imagery_rasters.fill_select()

        self.imagery_folder = PicterraDialogSelect(
            "folders", {}, api, True, folder_cb_1, None
        )
        self.ui.folder_menu.addWidget(self.imagery_folder)

        def raster_cb(raster_id):  # adds raster to project
            data = api.get_raster(raster_id)
            xyz_url = data["tms_url"].replace("tms/{z}/{x}/{y}", "xyz/{z}/{x}/{y}")
            urlWithParams = "type=xyz&url=%s&zmax=%s&zmin=%s&crs=EPSG3857" % (
                xyz_url,
                data["tiles_max_zoom"],
                data["tiles_min_zoom"],
            )
            rlayer = QgsRasterLayer(urlWithParams, data["name"], "wms")
            if not rlayer.isValid():
                self.err_box = error_box("Can't load raster " + data["name"])
                self.err_box.show()
            else:
                QgsProject.instance().addMapLayer(rlayer)

        self.imagery_rasters = PicterraDialogSelect(
            "rasters", {}, api, False, raster_cb, select_text="Add to project"
        )
        self.ui.rasters_list.addWidget(self.imagery_rasters)
        # layers
        def folder_cb_2(v):
            self.layer_rasters.extend_params({"folder": v})
            self.layer_rasters.fill_select()
            self.vector_layers.reset()

        self.layer_folder = PicterraDialogSelect(
            "folders", {}, api, True, folder_cb_2, None
        )
        self.ui.folder_layout.addWidget(self.layer_folder)

        def raster_cb(raster_id):
            self.vector_layers.set_endpoint("/rasters/%s/vector_layers/" % raster_id)
            self.vector_layers.fill_select()

        self.layer_rasters = PicterraDialogSelect(
            "rasters", {}, api, False, raster_cb, None
        )
        self.ui.raster_layout.addWidget(self.layer_rasters)

        def vector_cb(id):
            data = api.get_vector_layer(id)
            urls: List[str] = data["geojson_urls"]
            if len(urls) > 1:
                self.warn_box = warn_box("Truncating layer to first part")
                self.warn_box.show()
            geojson_url = urls[0]
            logger.debug("Remote geojson url is %s" % geojson_url)
            geojson = download_file(geojson_url)
            if not geojson:
                self.err_box = error_box("Error while loading %s" % data["name"])
                self.err_box.show()
                return
            vl = QgsVectorLayer(geojson, data["name"])
            if not vl.isValid():
                self.err_box = error_box("Can't load vector layer " + data["name"])
                self.err_box.show()
            else:
                s = QgsMarkerSymbol.createSimple(
                    {"name": "square", "color": data["color"]}
                )
                vl.renderer().setSymbol(s)
                QgsProject.instance().addMapLayer(vl)

        self.vector_layers = PicterraDialogSelect(
            "vector_layers",
            {},
            api,
            False,
            vector_cb,
            "vector layers",
            "Add layer to project",
        )
        self.ui.layers_list.addWidget(self.vector_layers)
