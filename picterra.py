# -*- coding: utf-8 -*-
from qgis.core import QgsMapLayer, QgsRasterDataProvider, QgsVectorDataProvider
from qgis.gui import QgisInterface
from qgis.PyQt.QtCore import Qt, QSettings, QTranslator, QCoreApplication, QUrl
from qgis.PyQt.QtGui import QIcon, QDesktopServices
from qgis.PyQt.QtWidgets import QAction, QWidget, QMenu
# Initialize Qt resources from file resources.py
from .resources import * # noqa
from .src.api import API, ApiError
from .src.utils import Logger, showPluginHelp, get_plugin_metadata, get_debug_flag, tr, \
    get_plugin_config
# Import the code for the dialog
from .src.dialogs import PicterraDialogDetect, PicterraDialogUpload, PicterraDialogSettings, \
    error_box

from typing import Callable, List, Optional, Mapping, Match, Any

import os.path
from re import search

logger = Logger(__file__)


class Picterra:
    """QGIS Plugin Implementation."""

    def __init__(self, iface: QgisInterface) -> None:
        """
        Class constructor

        Args:
            iface: interface instance that provides the hook by which you
                can manipulate the QGIS application at run time.
        """
        # Setup plugin info
        self.plugin_name = "Picterra"
        self.category = get_plugin_metadata()["category"]
        if self.category:
            assert self.category.lower() in (
                "database", "raster", "vector", "web")
        # Setup Picterra Public API access
        self.api = API()
        # Define the sub-menus of the plugin
        self.items = "upload", "detect", "help", "settings"
        # Put only main items in the toolbar
        self.toolbar_categories = self.items[:2]
        # Save reference to the QGIS interface
        self.iface: QgisInterface = iface
        # Initialize plugin directory
        self.plugin_dir = os.path.dirname(__file__)
        # Initialize locale
        locale = QSettings().value('locale/userLocale')[0:2]
        locale_path = os.path.join(
            self.plugin_dir,
            'i18n',
            '%s_%s.qm' % (self.plugin_name.lower(), locale))
        # Install translations
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)
        # Setup QActions for the plugin
        self.actions: Mapping[str, List[QAction]] = dict()
        self.actions['menus'] = []
        self.actions['layers'] = []
        self.menu = u'&%s' % tr(self.plugin_name)  # Main menu
        # Check if plugin was started the first time in current QGIS session
        # Must be set in initGui() to survive plugin reloads
        self.first_start: Optional[bool] = None
        # Log start
        debug = ('enabled' if get_debug_flag() else 'disabled')
        logger.debug('Starting Picterra QGIS plugin with debug %s' % debug)

    def add_menu_action(
        self,
        icon_path: str,
        text: str,
        callback: Callable,
        enabled_flag: bool = True,
        add_to_menu: bool = True,
        menu_type: str = None,
        add_to_toolbar: bool = True,
        status_tip: str = None,
        whats_this: str = None,
        parent: QWidget = None
    ) -> QAction:
        """
        Add a toolbar icon to the toolbar, associating it with an action linked
        to a function called on click; the action is also added to self.actions['menus'] list.

        Args:

            icon_path: Path to the icon for this action. Can be a resource
                path (e.g. ':/plugins/foo/bar.png') or a normal file system path.
            text: Text that should be shown in menu items for this action.
            callback: Function to be called when the action is triggered.
            enabled_flag: Whether the action should be enabled by default or not.
            add_to_menu: Whether the action should be added to the menu.
            menu_type: Type of menu add the action to, other than the main one (None).
            add_to_toolbar: Whether the action should also be added to the toolbar.
            status_tip: Optional text to show in a popup when mouse pointer hovers over the action.
            whats_this: Optional text to show in the status bar when the mouse pointer
                hovers over the action.
            parent: Parent Qt widget for the new action

        Returns:
            The action that was created.
        """
        if not menu_type:
            logger.debug(
                "Add action \"%s\" to \"plugin\" toolbar menu" % text)
        else:
            logger.debug(
                "Add action \"%s\" to \"%s\" toolbar menu" % (
                    text, menu_type))
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)
        # Optionally setup "Status" tip dialog
        if status_tip is not None:
            action.setStatusTip(status_tip)
        # Optionally setup "what's this?" tip dialog
        if whats_this is not None:
            action.setWhatsThis(whats_this)
        # Optionally adds plugin menu icon to Plugins toolbar
        if add_to_toolbar:
            self.iface.addToolBarIcon(action)
        # Add menu items to proper plugin category
        if add_to_menu:
            if not menu_type:
                self.iface.addPluginToMenu(self.menu, action)
            elif menu_type.lower() == "database":
                self.iface.addPluginToDatabaseMenu(self.menu, action)
            elif menu_type.lower() == "raster":
                self.iface.addPluginToRasterMenu(self.menu, action)
            elif menu_type.lower() == "vector":
                self.iface.addPluginToVectorMenu(self.menu, action)
            elif menu_type.lower() == "web":
                self.iface.addPluginToWebMenu(self.menu, action)
            else:
                logger.error("Invalid menu choice")
                raise RuntimeError(
                    "Invalid choice for action adding: %s" % menu_type)
        # Updates actions array and return inserted elements
        self.actions['menus'].append(action)
        return action

    def initGui(self) -> None:
        """
        Create the menu entries and toolbar icons inside the QGIS GUI.

        As for https://plugins.qgis.org/publish/ "Recommendations" section
        we should "Put the plugin into the appropriate menu (Vector, Raster,
        Web, Database)"
        """
        # Clear menus actions
        self.unload()
        # Check all plugin actions were cleared
        if len(self.actions['menus']) + len(self.actions['layers']) > 0:
            logger.error('Some plugin actions were not cleared')
            return
        # Create menus actions
        for item in [i.lower() for i in self.items]:
            self.add_menu_action(
                ':/plugins/picterra/assets/%s.png' % item,
                text=tr(u'%s' % item.capitalize()),
                callback=getattr(self, item),  # Each action is named as the menu item
                parent=self.iface.mainWindow(),
                menu_type=self.category,
                add_to_toolbar=item in self.toolbar_categories
            )
        # Create layers actions
        icon = QIcon(':/plugins/picterra/assets/upload.png')
        raster_upload_action = QAction(icon, u"Upload Layer as raster", self.iface)
        raster_upload_action.triggered.connect(self._raster_layer_upload_cb)
        self.iface.addCustomActionForLayerType(
            raster_upload_action, u"Picterra", QgsMapLayer.RasterLayer, True
        )
        logger.debug("Add action \"Upload raster\" to \"plugin\" layers menu")
        vector_upload_action = QAction(icon, u"Upload Layer as detection area", self.iface)
        vector_upload_action.triggered.connect(self._vector_layer_upload_cb)
        self.iface.addCustomActionForLayerType(
            vector_upload_action, u"Picterra", QgsMapLayer.VectorLayer, True
        )
        logger.debug("Add action \"Upload detection area\" to \"plugin\" layers menu")
        # Log plugin GUI loading
        logger.info("GUI initialization finished")

    def _raster_layer_upload_cb(self) -> None:
        """Loads the current raster layer and start its upload"""
        # Returns a pointer to the active layer (layer selected in the legend)
        layer: QgsMapLayer.RasterLayer = self.iface.activeLayer()
        # Retrieve raster layer file URI
        data: QgsRasterDataProvider = layer.dataProvider()
        file_path: str = data.dataSourceUri()
        # Log operation start
        logger.info('Trying to upload %s raster from layers' % file_path)
        # Tries to open upload dialog
        self.upload()  # After that if API is accessible, dlg should be PicterraDialogUpload
        # Fill the upload dialog with raster, focus on upload vector tab and start upload
        if isinstance(self.dlg, PicterraDialogUpload):
            self.dlg.ui.file_selector.setFilePath(file_path)
            self.dlg.ui.tab_upload.setCurrentIndex(0)
            self.dlg.start_raster_upload()

    def _vector_layer_upload_cb(self) -> None:
        """
        Opens the upload detection area dialog and loads the current
        vector layer in the file
        """
        # Returns a pointer to the active layer (layer selected in the legend)
        layer: QgsMapLayer.VectorLayer = self.iface.activeLayer()
        # Retrieve raster layer file URI
        data: QgsVectorDataProvider = layer.dataProvider()
        file_path: str = data.dataSourceUri()
        # QGIS appends a "layerXX" to the above URI, we need to remove it
        regex = r'^(.*)\|(?:layerid|layername)=.*$'
        search_result: Optional[Match[Any]] = search(regex, file_path)
        if search_result:
            file_path = search_result.group(1)
        # Log operation start
        logger.info('Setting %s vector (as detection area) from layers' % file_path)
        # Tries to open upload dialog
        self.upload()  # After that if API is accessible, dlg should be PicterraDialogUpload
        # Fill the upload dialog with the vector layer and focus on detection area tab
        if isinstance(self.dlg, PicterraDialogUpload):
            self.dlg.ui.detectionarea_file_selector.setFilePath(file_path)
            self.dlg.ui.tab_upload.setCurrentIndex(1)

    def unload(self) -> None:
        """Removes the plugin items from the QGIS GUI."""
        # Remove layers actions
        while self.actions['layers']:
            action = self.actions['layers'].pop()
            self.iface.removeCustomActionForLayerType(action)
        # Remove menu actions
        if not self.category:
            remover = self.iface.removePluginMenu
        else:
            cat = self.category.lower()
            if cat == "database":
                remover = self.iface.removePluginDatabaseMenu
            elif cat == "raster":
                remover = self.iface.removePluginRasterMenu
            elif cat == "vector":
                remover = self.iface.removePluginVectorMenu
            elif cat == "web":
                remover = self.iface.removePluginWebMenu
            else:
                logger.error("Invalid category choice")
                raise RuntimeError("Invalid choice for category: %s" % cat)
        while self.actions['menus']:
            action = self.actions['menus'].pop()
            remover(self.menu, action)
            self.iface.removeToolBarIcon(action)
            logger.debug("Unload plugin menu item %s" % action)

    def _check_api_access(self) -> bool:
        """
        Check if API is reachable

        Returns:
            Whether or not the API can be accessed
        """
        error = False
        try:
            ping = self.api.ping()
        except ApiError:
            error = True
        # No authentication: ask to check API key
        if ping in (401, 403):
            self.dlg = error_box(
                tr("""It seems your API key is invalid: please go to plugin
configuration (Web > Picterra > Settings) and put a valid one.""")
            )
            self.dlg.show()
            logger.warning('API auth failed')
            return False
        # Network error (client/server)
        elif ping != 200:
            error = True
        if error:
            logger.warning('API ping failed')
            email = get_plugin_metadata()["email"]
            self.dlg = error_box(
                tr("Network error, check your connection.") + "\n"
                + tr("If the problem persists, contact") + " " + email
            )
            self.dlg.show()
            return False
        logger.debug('API ping succeeded')
        return True

    def check_access(fn):
        """Decorator for checking remote Picterra API availability"""
        def wrapped(self=None):
            # Check access and authentication
            if not self._check_api_access():
                return
            else:
                fn(self)
        return wrapped

    @check_access  # type: ignore
    def detect(self):
        """
        Show the detector run dialog

        This dialog will load the detectors and rasters list, show
        them in select menus; when the user run detection it will invoke
        the API detection and show results/progress (in other dialog that
        closes its parent).

        Needs network access and authentication credentials.
        """
        # Check we have at least one detector and one raster
        try:
            self.api.check_detection()
        except ApiError as e:
            self.dlg = error_box(e)
            self.dlg.show()
            return
        # Prepare API wrapper and QgisInterface object references
        data = {
            "api": self.api,
            "iface": self.iface
        }
        # Creates and display the detect dialog, logging the event
        self.dlg = PicterraDialogDetect(data=data)
        self.dlg.show()
        logger.debug("Open detection dialog")

    @check_access  # type: ignore
    def upload(self):
        """
        Show the dialog the allows the image upload.

        Needs network access and authentication credentials.
        """
        # Instantiate the dialog, passing the API wrapper
        self.dlg = PicterraDialogUpload(data={"api": self.api})
        # Show the dialog
        self.dlg.show()
        # Log dialog opening
        logger.debug("Upload dialog opened")

    def settings(self):
        """Show the dialog to manage plugin settings"""
        # Instantiate the dialog, passing the API wrapper
        self.dlg = PicterraDialogSettings(data={"api": self.api})
        # Show the dialog
        self.dlg.show()
        # Log dialog opening
        logger.debug("Settings dialog opened")

    def help(self):
        """Open link to plugin doc / guide"""
        showPluginHelp()

    def platform(self):
        """Open link to web app"""
        url = QUrl(get_plugin_config()['platform_server'])
        QDesktopServices.openUrl(url)
