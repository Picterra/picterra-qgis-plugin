# -*- coding: utf-8 -*-
import json
import platform
from re import search
from time import sleep as time_sleep
from typing import (Any, Callable, Dict, List, Literal, Optional, Tuple,
                    TypedDict, Union)

from qgis.PyQt.QtCore import QT_VERSION_STR, QLocale, QThread

try:
    from qgis.core import Qgis, QgsSettings
except ImportError:
    from qgis.core import QGis as Qgis, QgsSettings

from .network import NetworkAccessManager, RequestsException
from .utils import (HttpMethod, Logger, Worker, get_api_base_url,
                    get_debug_flag, get_platform_url, get_plugin_metadata,
                    get_plugin_version, get_setting, tr)

logger = Logger(__file__)


class OperationMetadata(TypedDict):
    detector_id: str
    folder_id: str
    raster_id: str


class Operation(TypedDict):
    status: Literal["running", "failed", "success"]
    type: str
    errors: dict | None
    metadata: OperationMetadata | None
    results: dict[str, Any] | None


class API:
    """
    Wrapper for Picterra Public API.

    All API calls are blocking, but in case of polling requests (
    check raster processing and detection status) we launch worker
    threads to avoid blocking the whole QGIS interface.
    Network-wise, given that QGIS advice not to use requests, to be thread-safe
    for the API access, we create a NetworkManager for each request.
    """

    # HTTP timeout
    HTTP_TIMEOUT_SECONDS = 30
    # Max timeout for polling operations (e.g. wait for processing, detection)
    POLL_TIMEOUT_SECONDS = 7200
    # reduction of polling interval in dev mode (given API server is faster,
    # e.g. because ML params were reduced)
    POLL_SCALING_FACTOR = 10

    def __init__(self):
        """
        Constructor

        Creates the necessary parameters in order to access the API server (base
        URL and API Key), then prepares threads and workers for asynchronous ops.
        """
        # Store Picterra Public API root URL
        self.base_url = get_api_base_url()
        # Setup headers
        self.headers = {
            # Read the stored value for the api key
            "x-api-key": get_setting("api_key"),
            # These info simplify server-side debugging and auditing
            "user-agent": "%s - QGIS/%s (%s %s, %s, Qt %s)"
            % (
                "QGIS Picterra plugin v%s" % get_plugin_version(),
                Qgis.QGIS_VERSION,
                platform.machine(),
                platform.system(),
                QLocale().name()[:2],
                QT_VERSION_STR,
            ),
        }
        # Cannot use get_plugin_config because debug can be
        # overridden by environment
        self.debug = get_debug_flag()
        # Debug and timeouts for non-thread operations
        self.http_timeout = self.HTTP_TIMEOUT_SECONDS
        self.poll_timeout = self.POLL_TIMEOUT_SECONDS
        # Reduction of effective poll_interval sent by server (for testing)
        self.poll_scaling = self.POLL_SCALING_FACTOR if self.debug else 1
        # Multithreading (for async operations)
        self.workers = []
        self.threads = []
        # Log info
        logger.info("Built Api instance targeting %s" % self.base_url)
        logger.info("Debug level is %s" % self.debug)

    def _start_worker(
        self, worker_main: Callable, success_cb: Callable, error_cb: Callable
    ):
        def main():
            worker_main()

        def worker_finished(index: int, ret: bool) -> None:
            """
            Called when threads successfully completes executing worker

            The worker has successfully executed its code, but it does not
            mean that it has successfully fullfilled its task

            Args:
                index: The index of the worker.
                ret: Whether or not the thread returned success.

            Raises:
                ApiError: error during thread execution
            """
            # clean up the worker and thread
            self.workers[index].deleteLater()
            self.threads[index].quit()
            self.threads[index].wait()
            self.threads[index].deleteLater()
            try:
                if ret:
                    logger.debug("Thread %d finished returning %s" % (index, ret))
                    success_cb(ret)
                else:
                    logger.error("Thread %s terminated with error" % str(index))
                    error_cb(False)
            except ApiError as e:
                logger.error("Thread %d raised an ApiError %s" % (index, e))

        def worker_error(index: int, exception_string: str) -> None:
            """
            Executed when threads encounters an error during execution

            Args:
                index: The index of the worker.
                exception_string: Th exception raised by the worker.

            Raises:
                ApiError: error during thread execution
            """
            logger.error(
                "Worker %s raised an exception:\n%s" % (index, exception_string),
            )
            raise ApiError("Failure")

        # Creates thread
        self.threads.append(QThread())
        # Create a worker for the thread, that will call the "main"
        # function with the parameters in "args"
        self.workers.append(Worker(function=main, args=[]))
        assert len(self.threads) == len(self.workers)
        index = len(self.threads) - 1
        # Associate worker to threads
        self.workers[index].moveToThread(self.threads[index])
        # Assign worker success and fail callbacks
        self.workers[index].finished.connect(lambda x: worker_finished(index, x))
        self.workers[index].error.connect(lambda x: worker_error(index, x))
        # Execute worker when threads starts
        self.threads[index].started.connect(self.workers[index].run)
        # Start thread
        self.threads[index].start()

    def poll_sleep(self, poll_interval: int):
        """
        Sleep a given amount of seconds

        Seconds to sleep may be reduced for testing reasons
        """
        sleep_time = poll_interval / self.poll_scaling
        logger.debug("Sleeping %ds.." % sleep_time)
        time_sleep(sleep_time)

    def _wait_until_operation_completes(
        self, operation_id: str, poll_interval_s=30
    ) -> dict:
        """See Picterra Python wrapper"""
        timeout_s: int = self.poll_timeout
        while timeout_s > 0:  # Polling loop
            logger.debug(
                "Polling operation id %s every %d" % (operation_id, poll_interval_s)
            )
            resp = self._http(
                method=HttpMethod.GET, endpoint="operations/%s/" % operation_id
            )
            if resp["status"] != 200:
                raise ApiError("Error in GET operation: " + operation_id)
            status = resp["data"]["status"]
            logger.debug("status=%s" % status)
            if status == "success":
                return resp["data"]
            elif status == "failed":
                raise ApiError("Operation %s failed" % operation_id)
            else:
                timeout_s -= poll_interval_s
                self.poll_sleep(poll_interval_s)
        raise ApiError("Operation %s timed-out" % operation_id)

    def get_list(self, resource: str, params: dict) -> Tuple[List[dict], bool]:
        """See Picterra Python wrapper"""
        has_next = True
        while has_next:
            logger.debug("Fetching %s with %s" % (resource, params))
            resp = self._http(
                method=HttpMethod.GET, endpoint=resource + "/", query_params=params
            )
            if resp["status"] != 200:
                raise ApiError("Error in GET " + resource)
            r = resp["data"]
            has_next = bool(r["next"])
            return r["results"], has_next

    def start_async_polling(self, running_operation_ids: List[str], callback: Callable):
        """
        Launch an async operation. TODO update

        Starts a thread for the function "main" and arguments
        args: when it finishes it calls the callback function
        with the results, if there's no error

        Args:
            main: The function to call periodically.
            callback: The function called once main is finished.
            args: Non-keyworded arguments list for the main.

        Returns:
            The return value. True for success, False otherwise.
        """

        def main():
            while len(running_operation_ids) != 0:
                self.poll_sleep(30)
                for op_id in running_operation_ids:
                    status = self.get_operation(op_id)["status"]
                    if status != "running":
                        running_operation_ids.remove(op_id)
                        callback(op_id, status)
                    self.poll_sleep(0.1)

        def error(e):
            raise e

        self._start_worker(main, callback, error)

    @property
    def key(self) -> str:
        """Getter"""
        return self.headers["x-api-key"]

    @key.setter
    def key(self, value: str):
        """Setter"""
        # check api key length/format?
        self.headers["x-api-key"] = value

    @key.deleter
    def key(self):
        """Deleter"""
        del self.headers["x-api-key"]

    def _http(
        self,
        method: HttpMethod,
        endpoint: str,
        query_params: Optional[dict] = None,
        data: Optional[bytes] = None,
        mime: Optional[str] = None,
        size: int = 0,
        headers_override: dict = {},
    ):
        """
        Generic HTTP call to a Picterra API endpoint

        We assume response is always in json format.

        It always returns a status code: if the caller expected
        a different one it should raise an ApiError

        Args:
            method: The HTTP method to use.
            endpoint: The URL to target with the HTTP request.
            data: Text content of the request body
            mime: MIME type of the body
            size: Size in bytes of the request
            headers_override: Mapping of HTTP headers

        Returns:
            A dictionary containing
                The HTTP status code number relative to the operation outcome
                The data returned by the operation, if any
        """
        # Check method is correct
        if not isinstance(method, HttpMethod):
            return {"status": 500}
        # Build full API request URL
        url = self.base_url + endpoint
        # Assures thread-safety for network accesses
        network = NetworkAccessManager(debug=self.debug)
        headers = headers_override or self.headers
        if mime:
            headers["Content-Type"] = mime
        # Build request based on method
        try:
            if method == HttpMethod.GET:
                headers.pop("Content-Length", None)
                headers.pop("Content-Type", None)
                (response, content) = network.request(
                    url=url, headers=headers, query_params=query_params, blocking=True
                )
            elif method == HttpMethod.POST:
                headers["Content-Length"] = str(size)
                (response, content) = network.request(
                    url=url,
                    method="POST",
                    headers=headers,
                    query_params=query_params,
                    body=data,
                    blocking=True,
                )
            else:
                raise NotImplementedError(method)
        # Catch network errors
        except RequestsException as e:
            # Log error to QGIS console (and eventually report)
            logger.error(e)
            # Check network connection and server response error
            regex = r"#(\d+)"
            s = search(regex, str(e))
            status = 521 if not s else int(s.group(1))
            return {"status": status}
        # Return response
        status: int = response.status_code
        try:
            data: dict = json.loads(content.decode("utf-8"))
        except (json.decoder.JSONDecodeError, TypeError):
            return {"status": 500}
        return {"status": status, "data": data}

    def ping(self, test_apikey: str = "") -> int:
        """
        Check connection and API auth access

        Args:
            test_apikey: The API key to use

        Returns: HTTP status code, 200 if all good 401/403 otherwise
        """
        # Prepare request headers
        headers = {}
        if test_apikey:
            headers["x-api-key"] = test_apikey
        # Make request: we use the detectors endpoint because raster list
        # is not paginated and thus database can take a while to query
        res = self._http(
            method=HttpMethod.GET, endpoint="detectors/", headers_override=headers
        )
        # Log ping outcome and return HTTP status code
        logger.info("Pinged API with status %s" % res["status"])
        return res["status"]

    def get_resource(self, resource: str, id: str):
        """TODO"""
        logger.info("Getting %s with id %s" % (resource, id))
        path = "%s/%s/" % (resource, id)
        r = self._http(method=HttpMethod.GET, endpoint=path)
        if r["status"] != 200 or "data" not in r:
            raise ApiError("Error in GET %s" % path)
        return r["data"]

    def get_operation(self, operation_id: str, add_metadata: bool = False) -> Operation:
        op_data: Operation = self.get_resource("operations", operation_id)
        if add_metadata and op_data["metadata"] is not None:
            metadata = op_data["metadata"]
            if metadata("raster_id", None):
                op_data["raster"] = self.get_raster(metadata["raster_id"])
            else:
                op_data["raster"] = None
            if metadata("detector_id", None):
                op_data["detector"] = self.get_detector(metadata["detector_id"])
            else:
                op_data["detector"] = None
            if metadata("folder_id", None):
                op_data["folder"] = self.get_folder(metadata["folder_id"])
            else:
                op_data["folder"] = None
        return op_data

    def get_raster(self, raster_pk: str) -> dict:
        """
        Get raster details

        Args:
            raster_pk: str of the raster whose details we are interested on

        Returns:
            The raster metadata

        Raises:
            ApiError: if the server didn't send the right response code
        """
        return self.get_resource("rasters", raster_pk)

    def get_folder(self, folder_pk: str) -> dict:
        """
        Get folder details

        Args:
            folder_pk: str of the folder whose details we are interested on

        Returns:
            The folder metadata

        Raises:
            ApiError: if the server didn't send the right response code
        """
        return self.get_resource("folders", folder_pk)

    def get_detector(self, detector_pk: str) -> dict:
        """
        Get detector details

        Args:
            detector_pk: str of the detector whose details we are interested on

        Returns:
            The detector metadata

        Raises:
            ApiError: if the server didn't send the right response code
        """
        return self.get_resource("detectors", detector_pk)

    def get_vector_layer(self, vector_layer_pk: str) -> dict:
        """
        Get vector layer details

        Args:
            vector_layer_pk: str of the vector layer whose details we are interested on

        Returns:
            The vector layer metadata

        Raises:
            ApiError: if the server didn't send the right response code
        """
        return self.get_resource("vector_layers", vector_layer_pk)

    def get_detectionarea_upload(self, raster_pk: str, upload_pk: str) -> dict:
        """
        Get info on a Detection Area Upload

        Args:
            raster_pk: str of the raster whose Detection Area we are setting
            upload_pk: str of the particular upload we are targeting

        Returns:
            Metadata on the operation status

        Raises:
            ApiError: if the server didn't send the right response code
        """
        logger.info(
            "Getting raster=%s detection area upload=%s info" % (raster_pk, upload_pk)
        )
        r = self._http(
            method=HttpMethod.GET,
            endpoint="rasters/%s/detection_areas/upload/%s/" % (raster_pk, upload_pk),
        )
        if r["status"] != 200 or "data" not in r:
            raise ApiError("Error in GET rasters/detection_areas/upload/")
        return r["data"]

    def detect(self, detector_pk: str, raster_pk: str) -> str:
        """
        Start detection

        Args:
            detector_pk: str of the detector we want to use for prediction
            raster_pk: str of the raster we want to predict on
            callback: function to call when detection ends successfully

        Returns:
            Metadata on the operation status

        Raises:
            ApiError: if the prediction doesn't end in the rightful manner
        """
        # Log operation start
        logger.debug("Start detecting on %s with %s" % (raster_pk, detector_pk))
        # Build request body and execute HTTP call
        data = json.dumps({"raster_id": raster_pk}).encode("utf-8")
        r = self._http(
            method=HttpMethod.POST,
            endpoint="detectors/%s/run/" % detector_pk,
            data=data,
            size=len(data),
            mime="application/json",
        )
        # Check the operation started and return its identifier
        if r["status"] != 201:
            raise ApiError(tr("Error starting detection"))
        operation_id = r["data"]["operation_id"]
        self.add_operation(operation_id)
        return operation_id

    def _result_op_poll(
        self, operation_id: str, poll_interval: int, raster_pk: str
    ) -> Union[dict, bool]:
        """
        Periodically checks for detection results readiness

        Args:
            id: str of the result (Detector Run)
            poll_interval: seconds to wait between each request
            raster_pk: str of the raster we are predicting on

        Returns:
            A dictionary with the URL of the result and the id of the raster if all
            went good, False otherwise
        """
        # Log operation start
        logger.debug(
            "Init worker main polling result op %s every %ss"
            % (operation_id, poll_interval / self.poll_scaling)
        )
        logger.debug("Polling detection operation %s" % operation_id)
        op_data = self._wait_until_operation_completes(operation_id, poll_interval)
        download_url = op_data["results"]["url"]
        return {"geojson_url": download_url, "raster_id": raster_pk}

    def upload_raster(
        self,
        name: str,
        mime: str,
        content: bytes,
        size: int,
        folder_id: str,
        after_upload_cb: Callable,
        err_cb: Callable,
    ) -> None:
        """
        Upload and process an image from local

        Given a raster file, launches an upload and starts a polling
        in a thread for its completion

        Args:
            name: name for the raster
            mime: MIME type for the raster (e.g. 'image/tiff')
            content: byte content of the raster image
            size: size in bytes of the image
            folder_id: TODO

        Raises:
            ApiError: remote server encountered issues when starting upload
        """
        # Prepare request body and make HTTP call
        data = json.dumps({"name": name, "folder_id": folder_id}).encode("utf-8")
        resp = self._http(
            method=HttpMethod.POST,
            endpoint="rasters/upload/file/",
            data=data,
            size=len(data),
            mime="application/json",
        )
        # Check if upload was started correctly
        if resp["status"] != 201:
            raise ApiError(tr("Error while getting remote upload URL"))
        # Parse and store upload information (URL where upload the file to, id
        # of the raster in the Picterra platform)
        upload_url = resp["data"]["upload_url"]
        raster_id = resp["data"]["raster_id"]
        # Start file upload in a separate thread in order not to block QGIS
        logger.info("Start upload and process for %s" % raster_id)

        def _upload_and_start_process_raster():
            network = NetworkAccessManager(debug=self.debug)
            headers = {"Content-Length": str(size), "Content-Type": mime}
            try:
                (response, _) = network.request(
                    url=upload_url,
                    method="PUT",
                    headers=headers,
                    body=content,
                    blocking=True,
                )
            except RequestsException as e:
                raise ApiError(e)
            if response.status_code != 200:
                raise ApiError(tr("Error uploading image to remote cloud storage"))
            logger.info("Successfully uploaded %s" % str(raster_id))
            r = self._http(
                method=HttpMethod.POST,
                endpoint="rasters/%s/commit/" % raster_id,
                size=0,
            )
            if r["status"] != 201:
                raise ApiError(tr("Error starting raster processing"))
            op_id = r["data"]["operation_id"]
            self.add_operation(op_id)
            return op_id

        self._start_worker(
            _upload_and_start_process_raster, lambda: after_upload_cb, err_cb
        )

    def upload_detectionarea(
        self, raster_id: str, mime: str, content: bytes, size: int, callback: Callable
    ):
        """
        Upload and process a detection area local GeoJSON file for a remote image

        Args:
            raster_id: str of to the raster whose detection area we want to set
            mime: MIME type of the file (usually GeoJSON)
            content: byte content of the geometry file
            size: size in bytes of the geometry file
            callback: function to call once operation is finished

        Raises:
            ApiError: remote server encountered issues when starting upload
        """
        # Make HTTP request
        resp = self._http(
            method=HttpMethod.POST,
            endpoint="rasters/%s/detection_areas/upload/file/" % raster_id,
        )
        # Handle error rasing proper exception
        if resp["status"] != 201:
            raise ApiError(tr("Error while getting remote upload URL"))
        # Parse response
        upload_url = resp["data"]["upload_url"]
        upload_id = resp["data"]["upload_id"]
        # Start waiting upload and processing of the geometry file in another
        # thread in order not to block QGIS
        self.start_async_polling(
            self._upload_and_process_detectionarea,  # main
            callback,  # callback
            upload_url,
            raster_id,
            upload_id,
            content,
            mime,
            size,
        )

    def _upload_and_process_detectionarea(
        self,
        upload_url: str,
        raster_id: str,
        upload_id: str,
        content: bytes,
        mime: str,
        size: int,
    ) -> bool:
        """
        Send geometry data for the detection area of a raster and then
        polls its processing end

        Given a geometry file, uploads its content to a blobstore, then inform of the
        previous operation the Picterra API server and order it to start processing
        the file itself. Then starts periodically polling the above server in order
        to wait until the processing is finished.

        Args:
            upload_url: URL of the blobstore where send file data to
            raster_id: str assigned to the raster whose detection area we are setting
            upload_id: str assigned to the upload of the geometry file
            content: byte content of the file
            mime: MIME type of the file (usually JSON)
            size: size in bytes of the geometry file

        Returns:
            Whether or not upload and processing completed successfully
        Raises:
            ApiError: remote server encountered issues during operation
        """
        # Log operation start
        logger.info(
            "Start raster=%s detection area (size %d) upload=%s and process"
            % (raster_id, size, upload_id)
        )
        # Prepare HTTP request
        network = NetworkAccessManager(debug=self.debug)
        headers = {"Content-Length": str(size), "Content-Type": mime}
        # Upload the detection area geojson to the blobstore
        try:
            (response, content) = network.request(
                url=upload_url,
                method="PUT",
                headers=headers,
                body=content,
                blocking=True,
            )
        # Handle errors
        except RequestsException as e:
            raise ApiError(e)
        if response.status_code != 200:
            raise ApiError(
                tr("Error uploading detection area file to remote cloud storage")
            )
        # Log blobstore upload
        logger.info("Successfully uploaded detection area for %s" % raster_id)
        # Start remote processing of the geometry file
        r = self._http(
            method=HttpMethod.POST,
            endpoint="rasters/%s/detection_areas/upload/%s/commit/"
            % (raster_id, upload_id),
            size=0,
        )
        # Handle processing start error
        if r["status"] != 201:
            raise ApiError(tr("Error starting detection area processing"))
        # Parse response and polls
        op_id = r["data"]["operation_id"]
        poll_s = r["data"]["poll_interval"]
        logger.debug("Polling detection area commit operation %s" % op_id)
        self._wait_until_operation_completes(op_id, poll_s)
        return True

    def load_operations(self):
        """TODO"""
        s = QgsSettings()
        operations: dict[str, dict] = s.value("picterra-operations", {})
        for (id, op) in operations.items():
            if op.get("seen", False) is False:
                data = self.get_operation(id, True)
                op["status"] = data["status"]
                if data["status"] != "running":
                    op["seen"] = True
        return operations

    def add_operation(self, operation_id: str):
        """TODO"""
        operations = self.load_operations()
        operations[operation_id] = self.get_operation(operation_id, True)
        operations[operation_id]["seen"] = False
        s = QgsSettings()
        s.setValue("picterra-operations", operations)

    def remove_operation(self, operation_id: str):
        """TODO"""
        operations = self.load_operations()
        del operations[operation_id]
        s = QgsSettings()
        s.setValue("picterra-operations", operations)

    def reset_operations(self):
        """TODO"""
        s = QgsSettings()
        s.setValue("picterra-operations", {})

    def update_operation_status(self, operation_id: str, status: str):
        """TODO"""
        operations = self.load_operations()
        operations[operation_id]["status"] = status
        s = QgsSettings()
        s.setValue("picterra-operations", operations)

    def get_resource_link(self, resource: str, id1: str, id2: str | None = None):
        """TODO"""
        url = get_platform_url()
        if resource == "detector":
            url += "modes/training/detectors/" + id1
        elif resource == "raster":
            url += "modes/detection/projects/" + id1
        elif resource == "vector layer":
            url += "modes/detection/projects/%s/raster/%s/vectors" % (id1, id2)
        else:
            return url


# define Python user-defined exceptions
class ApiError(Exception):
    """
    Exception when interacting with the API

    Always log to critical.
    """

    def __init__(self, *args, **kwargs):
        Exception.__init__(self, *args, **kwargs)
        logger.error(args[0])


class AuthenticationError(Exception):
    """
    Exception when trying to access API without right credentials

    Always log to warning.
    """

    def __init__(self, *args, **kwargs):
        Exception.__init__(self, *args, **kwargs)
        logger.warning(args[0])
