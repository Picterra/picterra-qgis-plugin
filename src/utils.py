# -*- coding: utf-8 -*-
import enum
import os
import sys
import inspect
import mimetypes
from typing import Union, Optional
from traceback import format_exc
import configparser
from urllib.parse import quote

from qgis.core import QgsMessageLog, QgsLogger, Qgis, QgsSettings
from qgis.PyQt.QtGui import QDesktopServices

from qgis.PyQt.QtCore import QObject, pyqtSignal, QLocale, QUrl, \
    QCoreApplication

from .network import NetworkAccessManager, RequestsException

SETTINGS_PREFIX = "picterra/"
SETTINGS_TAG = "Picterra plugin"

ALLOWED_SETTINGS = ("api_key", )


class FaqLinks(enum.Enum):
    DETECTION_AREAS = 1
    SUPPORTED_IMAGES = 2


class BlogLinks(enum.Enum):
    GUIDE = 1


WEBSITE_URL = 'https://picterra.ch/'
FAQ_URL = WEBSITE_URL + 'faq/'
UTM_SOURCE = '?utm_source=qgisplugin'
EXTERNAL_LINKS = {
    FaqLinks.DETECTION_AREAS: (
        FAQ_URL + UTM_SOURCE + '#how-can-i-limit-the-area-in-which-the-detector-is-applied'
    ),
    FaqLinks.SUPPORTED_IMAGES: (
        FAQ_URL + UTM_SOURCE + '#the-quality-of-my-uploaded-images-is-lower-than-the-original'
    ),
    BlogLinks.GUIDE: (
        'https://picterra.ch/blog/introducing-the-picterra-qgis-plugin/'
    )
}


# API http methods
class HttpMethod(enum.Enum):
    GET = '1'
    POST = '2'
    PUT = '3'
    DELETE = '4'


class Logger():
    """
    Wrapper for QGIS logger

    See
    https://docs.qgis.org/testing/en/docs/pyqgis_developer_cookbook/communicating.html?highlight=message%20bar#logging
    """
    class LoggerLevel(enum.Enum):
        DEBUG = 1
        INFO = 2
        WARNING = 3
        ERROR = 4
    DEBUG = LoggerLevel.DEBUG
    INFO = LoggerLevel.INFO
    WARNING = LoggerLevel.WARNING
    ERROR = LoggerLevel.ERROR

    def __init__(self, file):
        """Constructor."""
        self.tag = SETTINGS_TAG
        # function file
        self.file = file
        self.debug_flag = get_debug_flag()
        # Network manager
        self.network = NetworkAccessManager(debug=self.debug_flag)
        QgsMessageLog.logMessage("Init logger with debug=%s" % self.debug_flag, level=Qgis.Info)

    def debug(self, message):
        return self._log(message, self.DEBUG)

    def info(self, message):
        return self._log(message, self.INFO)

    def warning(self, message):
        return self._log(message, self.WARNING)

    def error(self, message):
        return self._log(message, self.ERROR)

    def _log(self, message: str, level: LoggerLevel) -> None:
        """
        Print message to QGIS log, depending on release or debug mode.

        Print either to the QGIS error console, if in release mode,
        (https://qgis.org/pyqgis/master/core/QgsLogger.html#qgis.core.QgsLogger)
        or to the QGIS GUI message logger (Log Message Panel)
        (https://qgis.org/pyqgis/master/core/QgsMessageLog.html#qgis.core.QgsMessageLog).
        Always use the above thread-safe classess to print out messages.
        """
        message = str(message)
        # Get caller info
        callerframerecord = inspect.stack()[1]
        frame = callerframerecord[0]
        info = inspect.getframeinfo(frame)
        # prepare level message
        msg = 'file=%s, func=%s, line=%s: %s' % (
            info.filename,
            info.function,
            str(info.lineno),
            message)
        # Print to Log Message Panel
        # (https://qgis.org/pyqgis/master/core/QgsMessageLog.html#qgis.core.QgsMessageLog.logMessage)
        if self.debug_flag:
            if level == self.DEBUG:
                QgsMessageLog.logMessage(msg, self.tag, level=Qgis.Info)
            elif level == self.INFO:
                QgsMessageLog.logMessage(msg, self.tag, level=Qgis.Success)
            elif level == self.ERROR:
                QgsMessageLog.logMessage(msg, self.tag, level=Qgis.Critical)
            elif level == self.WARNING:
                QgsMessageLog.logMessage(msg, self.tag, level=Qgis.Warning)
            else:
                raise Exception('Invalid log level: %s' % level)
        # Print to QGIS Python console
        else:
            # Do not use QgsLogger.fatal(msg), as it will crash QGIS process
            if level == self.ERROR:
                QgsLogger.critical(msg)
            elif level == self.WARNING:
                QgsLogger.warning(msg)
            elif level in (self.DEBUG, self.INFO):
                QgsLogger.debug(
                    msg=message, file=info.filename,
                    function=info.function, line=info.lineno)
        # always print to QGIS log file
        # must configure the QGIS_LOG_FILE environment variable
        QgsLogger.logMessageToFile("Picterra plugin - " + msg)


class Worker(QObject):
    '''
    Worker that is executed asynchronously by threads
    '''
    def __init__(self, function, args):
        QObject.__init__(self)
        self.function = function
        self.args = args
        self.killed = False

    def run(self):
        ret = None
        try:
            ret = self.function(*self.args)
        except Exception as e:
            # forward the exception upstream
            self.error.emit(e, format_exc())
        self.finished.emit(ret)

    def kill(self):
        self.killed = True
    finished = pyqtSignal(object)
    error = pyqtSignal(Exception)
    progress = pyqtSignal(float)


def get_file_info(filename: str) -> dict:
    """Given a file path returns its size (in bytes) and its name"""
    if not os.path.isfile(filename):
        return {}
    st = os.stat(filename)
    name = os.path.basename(filename)
    mime = mimetypes.MimeTypes().guess_type(filename)[0]
    return {
        "size": st.st_size,
        "name": name,
        "mime": mime
    }


# Plugin configuration
def get_plugin_config():
    """
    Get the dict of plugin parameters
    """
    file_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), os.pardir,
        'metadata.txt'
    ))
    config = configparser.ConfigParser()
    config.read(file_path)
    return config["configuration"]


def get_api_server() -> str:
    config = get_plugin_config()
    return str(
        os.getenv('QGIS_PICTERRA_API_BASE_URL', config['api_server'])
    )


def get_api_version() -> int:
    config = get_plugin_config()
    return int(
        os.getenv('QGIS_PICTERRA_API_VERSION', config['api_version'])
    )


def get_api_base_url() -> str:
    return get_api_server() + '/v' + str(get_api_version()) + '/'


def get_platform_url() -> str:
    return get_plugin_config()['platform_server']


def get_debug_flag() -> bool:
    config = get_plugin_config()
    return (int(os.getenv('QGIS_PICTERRA_DEBUG', 0)) == 1) or config['debug']


def get_plugin_metadata():
    """Get the dict of plugin metadata"""
    file_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), os.pardir,
        'metadata.txt'
    ))
    # https://docs.python.org/3/library/configparser.html
    config = configparser.ConfigParser()
    config.read(file_path)
    return config["general"]


def set_setting(key: str, val: str) -> None:
    """
    Set a parameter in QGIS setting storage.

    https://qgis.org/api/classQgsSettings.html
    """
    if key not in ALLOWED_SETTINGS:
        raise KeyError("Invalid setting value: " + key)
    QgsMessageLog.logMessage(
        "Settings key: %s" % (key, ),
        SETTINGS_TAG,
        level=Qgis.Info)
    s = QgsSettings()
    s.setValue(SETTINGS_PREFIX + key, val)


def get_setting(key: str) -> str:
    """
    Get a parameter in QGIS setting storage or its default.

    https://qgis.org/api/classQgsSettings.html
    """
    if key not in ALLOWED_SETTINGS:
        raise KeyError("Invalid setting value: " + key)
    QgsMessageLog.logMessage(
        "Settings key: %s" % (key, ),
        SETTINGS_TAG,
        level=Qgis.Info)
    s = QgsSettings()
    y = get_plugin_config()
    # get the setting value or its default one
    return s.value(SETTINGS_PREFIX + key, y.get(key, ""))


def showPluginHelp() -> None:
    url = EXTERNAL_LINKS[BlogLinks.GUIDE]
    QDesktopServices.openUrl(QUrl(url))


def showPluginDoc(
    packageName: Optional[str] = None, filename: str = "index", section: str = ""
) -> None:
    """
    Shows a help in the user's html browser.

    The help file should be named index-ll_CC.html or index-ll.html
    """
    if get_debug_flag():
        helpfile = os.path.dirname(__file__) + "/help/build/html/index.html"
    else:
        source = ""
        if packageName is None:
            import inspect
            source = inspect.currentframe().f_back.f_code.co_filename  # type: ignore
        else:
            source = sys.modules[packageName].__file__
        path = os.path.dirname(source)
        locale = str(QLocale().name())
        helpfile = os.path.join(path, filename + "-" + locale + ".html")
        if not os.path.exists(helpfile):
            helpfile = os.path.join(
                path,
                filename + "-" + locale.split("_")[0] + ".html")
        if not os.path.exists(helpfile):
            helpfile = os.path.join(path, filename + "-en.html")
        if not os.path.exists(helpfile):
            helpfile = os.path.join(path, filename + "-en_US.html")
        if not os.path.exists(helpfile):
            helpfile = os.path.join(path, filename + ".html")
    if os.path.exists(helpfile):
        url = "file://" + helpfile
        if section != "":
            url = url + "#" + section
        QDesktopServices.openUrl(QUrl(url))
    else:
        logger = Logger(__file__)
        logger.error("No file %s" % helpfile)


# noinspection PyMethodMayBeStatic
def tr(message: str) -> str:
    """
    Get the translation for a string using Qt translation API.

    We implement this ourselves since we do not inherit QObject.

    args:
        message: String for translation.

    returns:
        Translated version of message.
    """
    # noinspection PyTypeChecker,PyArgumentList,PyCallByClass
    return QCoreApplication.translate('Picterra', message)


def download_file(url: str) -> Union[str, bool]:
    """Download a file and return its UTF-8 representation"""
    network = NetworkAccessManager(debug=get_debug_flag())
    try:
        (res, data) = network.request(url=quote(url), blocking=True)
        assert res.status_code == 200
        return data.decode("utf-8")
    except (RequestsException, AssertionError):
        return False


def open_faq_link(faq_enum) -> None:
    url = EXTERNAL_LINKS[faq_enum]
    QDesktopServices.openUrl(QUrl(url))
