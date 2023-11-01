# -*- coding: utf-8 -*-
import json
import platform
from re import search
from time import sleep as time_sleep
from typing import Callable, List, Optional, Union
from uuid import UUID

from qgis.PyQt.QtCore import QT_VERSION_STR, QLocale, QThread

try:
    from qgis.core import Qgis
except ImportError:
    from qgis.core import QGis as Qgis

from .network import NetworkAccessManager, RequestsException
from .utils import (
    HttpMethod,
    Logger,
    Worker,
    get_api_base_url,
    get_api_version,
    get_debug_flag,
    get_plugin_metadata,
    get_setting,
    tr,
)

logger = Logger(__file__)


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
                "QGIS Picterra plugin v%s" % get_api_version(),
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

    def _paginate_through_list(self, resource: str, params=None) -> dict:
        """See Picterra Python wrapper"""
        if params is None:
            params = {}
        params["page_number"] = 1
        data = []
        sleep_interval_s = 0
        has_next = True
        while has_next:
            logger.debug("Paginating %s, fetching with %s" % (resource, params))
            resp = self._http(
                method=HttpMethod.GET, endpoint=resource + "/", query_params=params
            )
            if resp["status"] != 200:
                raise ApiError("Error in GET " + resource)
            r = resp["data"]
            has_next = r["next"]
            data += r["results"]
            if r["count"] > 1000:
                sleep_interval_s = 5
            elif r["count"] > 500:
                sleep_interval_s = 3
            elif r["count"] > 100:
                sleep_interval_s = 1
            else:
                sleep_interval_s = 0.1
            if has_next:
                params["page_number"] += 1
                self.poll_sleep(sleep_interval_s)
            else:
                return data

    def _start_async_polling(self, main: Callable, callback: Callable, *args):
        """
        Launch an async operation.

        Starts a thread for the function "main" and arguments
        args: when it finishes it calls the callaback function
        with the results, if there's no error

        Args:
            main: The function to call periodically.
            callback: The function called once main is finished.
            args: Non-keyworded arguments list for the main.

        Returns:
            The return value. True for success, False otherwise.
        """

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
                    callback(ret)
                else:
                    logger.error("Thread %s terminated with error" % str(index))
                    callback(False)
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
            error = tr(
                """Upload operation failed: please try again.\n
            In case the error persists please contact """
            )
            email = get_plugin_metadata()["email"]
            raise ApiError(error + email)

        # Creates thread
        self.threads.append(QThread())
        # Create a worker for the thread, that will call the "main"
        # function with the parameters in "args"
        self.workers.append(Worker(function=main, args=args))
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
        # Log thread start
        logger.debug("Start thread %s for %s" % (index, main.__name__))

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
    ) -> dict:
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
        status = response.status_code
        try:
            data = json.loads(content.decode("utf-8"))
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

    def get_raster(self, raster_pk: UUID) -> dict:
        """
        Get raster details

        Args:
            raster_pk: UUID of the raster whose details we are interested on

        Returns:
            The raster metadata

        Raises:
            ApiError: if the server didn't send the right response code
        """
        # Log operation
        logger.info("Getting raster=%s info" % raster_pk)
        # Build URL and make request
        path = "rasters/%s/" % raster_pk
        r = self._http(method=HttpMethod.GET, endpoint=path)
        # Check response and return data or raise error
        if r["status"] != 200 or "data" not in r:
            raise ApiError("Error in GET %s" % path)
        return r["data"]

    def get_detector(self, detector_pk: UUID) -> dict:
        """
        Get detector details

        Args:
            detector_pk: UUID of the detector whose details we are interested on

        Returns:
            The detector metadata

        Raises:
            ApiError: if the server didn't send the right response code
        """
        # Log operation
        logger.info("Getting detector=%s info" % detector_pk)
        # Build URL and make request
        path = "detectors/%s/" % detector_pk
        r = self._http(method=HttpMethod.GET, endpoint=path)
        # Check response and return data or raise error
        if r["status"] != 200 or "data" not in r:
            raise ApiError("Error in GET %s" % path)
        return r["data"]

    def get_rasters(self) -> List[dict]:
        """
        Get rasters list

        Returns:
            The rasters metadata array

        Raises:
            ApiError: if the server didn't send the right response code
        """
        # Log operation
        logger.info("Getting rasters list")
        return self._paginate_through_list("rasters")

    def get_detectors(self, **kwargs) -> List[dict]:
        """
        Get detectors list

        Returns:
            The detectors metadata array

        Raises:
            ApiError: if the server didn't send the right response code
        """
        # Log operation
        logger.info("Getting detectors list")
        return self._paginate_through_list("detectors", kwargs)

    def get_rasters_count(self) -> int:
        resp = self._http(method=HttpMethod.GET, endpoint="rasters/")
        if resp["status"] != 200:
            raise ApiError("Error in GET rasters")
        return resp["data"]["count"]

    def get_detectors_count(self) -> int:
        resp = self._http(
            method=HttpMethod.GET,
            endpoint="detectors/",
            query_params={"is_runnable": True},
        )
        if resp["status"] != 200:
            raise ApiError("Error in GET detectors")
        return resp["data"]["count"]

    def check_detection(self) -> bool:
        """
        Check the user account has enough info to detect (1 raster and 1 detector)

        Returns:
            Whether or not we can launch a detection

        Raises:
            ApiError: if the server didn't send the right response code
        """
        if self.get_rasters_count() == 0:
            return False
        return self.get_detectors_count() != 0

    def get_detectionarea_upload(self, raster_pk: UUID, upload_pk: UUID) -> dict:
        """
        Get info on a Detection Area Upload

        Args:
            raster_pk: UUID of the raster whose Detection Area we are setting
            upload_pk: UUID of the particular upload we are targeting

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

    def detect(
        self,
        detector_pk: UUID,
        raster_pk: UUID,
        success_callback: Callable,
        error_callback: Callable,
    ) -> bool:
        """
        Start detection

        Args:
            detector_pk: UUID of the detector we want to use for prediction
            raster_pk: UUID of the raster we want to predict on
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
        # Check the operation started
        if r["status"] != 201:
            logger.error(
                "Detecting on %s with %s failed with HTTP code %d "
                % (raster_pk, detector_pk, r["status"])
            )
            error_callback()
            return False
        # Parse response arguments
        op_id: UUID = r["data"]["operation_id"]
        poll_interval: int = r["data"]["poll_interval"]
        # Launch asynchronous polling operation in a thread
        self._start_async_polling(
            self._result_op_poll,  # main
            success_callback,  # callback
            op_id,
            poll_interval,
            raster_pk,
        )
        # Return the detection has started
        return True

    def _result_op_poll(
        self, operation_id: UUID, poll_interval: int, raster_pk: UUID
    ) -> Union[dict, bool]:
        """
        Periodically checks for detection results readiness

        Args:
            id: UUID of the result (Detector Run)
            poll_interval: seconds to wait between each request
            raster_pk: UUID of the raster we are predicting on

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
        self, name: str, mime: str, content: bytes, size: int, callback: Callable
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
            callback: function to call when upload successfully ends

        Raises:
            ApiError: remote server encountered issues when starting upload
        """
        # Prepare request body and make HTTP call
        data = json.dumps({"name": name}).encode("utf-8")
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
        # Start file upload and polling in a separate thread in order not to block QGIS
        self._start_async_polling(
            self._upload_and_process_raster,  # main
            callback,  # callback
            upload_url,
            raster_id,
            content,
            mime,
            size,
        )

    def _upload_and_process_raster(
        self, upload_url: str, raster_id: UUID, content: bytes, mime: str, size: int
    ) -> bool:
        """
        Send raster data and then polls the raster processing

        Given a raster file, uploads its content to a blobstore, then inform of the
        previous operation the Picterra API server and order it to start processing
        the image. Then starts periodically polling the above server in order to wait
        until the image processing is finished.

        Args:
            upload_url: URL of the blobstore where send raster data to
            raster_id: UUID assigned to the raster (e.g. '123e4567-e89b-12d3-a456-426614174000')
            content: byte content of the raster image
            mime: MIME type of the image
            size: size in bytes of the image

        Returns:
            Whether or not upload and processing completed successfully
        Raises:
            ApiError: remote server encountered issues when starting upload
        """
        # Log operation start
        logger.info("Start upload and process for %s" % raster_id)
        # Prepare for HTTP request
        network = NetworkAccessManager(debug=self.debug)
        headers = {"Content-Length": str(size), "Content-Type": mime}
        # Upload file to blobstore server via PUT
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
            raise ApiError(tr("Error uploading image to remote cloud storage"))
        # Log blobstore upload end
        logger.info("Successfully uploaded %s" % str(raster_id))
        # Start remote processing
        r = self._http(
            method=HttpMethod.POST, endpoint="rasters/%s/commit/" % raster_id, size=0
        )
        # Handle error
        if r["status"] != 201:
            raise ApiError(tr("Error starting raster processing"))
        # Parse response and polls
        op_id = r["data"]["operation_id"]
        poll_s = r["data"]["poll_interval"]
        logger.debug("Polling raster upload operation %s" % op_id)
        self._wait_until_operation_completes(op_id, poll_s)
        return True

    def upload_detectionarea(
        self, raster_id: UUID, mime: str, content: bytes, size: int, callback: Callable
    ):
        """
        Upload and process a detection area local GeoJSON file for a remote image

        Args:
            raster_id: UUID of to the raster whose detection area we want to set
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
        self._start_async_polling(
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
        raster_id: UUID,
        upload_id: UUID,
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
            raster_id: UUID assigned to the raster whose detection area we are setting
            upload_id: UUID assigned to the upload of the geometry file
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
