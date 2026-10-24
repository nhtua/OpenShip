"""Invocation-scoped native AWS SDK clients with bounded execution."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import re
import uuid
from dataclasses import dataclass, field

import boto3
import botocore.session
from botocore.config import Config
from botocore.configprovider import ConstantProvider
from botocore.exceptions import ClientError

from src.openship.providers.errors import ProviderError

from .aws_transport import AwsRequestBudget, AwsSdkError, BoundedAwsHttpSession

_REGION = re.compile(
    r"^(?:(?:af|ap|ca|eu|il|me|mx|sa)-[a-z0-9-]+-\d|"
    r"us-(?!gov-|iso-|isob-|isof-|isoeast-)[a-z0-9-]+-\d)$"
)
_MAX_WORKERS = 4
_POLL_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class AwsSdkAuth:
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    session_token: str | None = field(default=None, repr=False)


class AwsSdkContext:
    """Create explicit-credential clients and run SDK operations off-loop."""

    def __init__(
        self,
        *,
        auth: AwsSdkAuth,
        region: str,
        control,
        max_workers: int = 1,
        max_requests: int = 8,
        max_response_bytes: int = 2 * 1024 * 1024,
        timeout_seconds: float = 30.0,
        transport_factory=None,
        request_budget: AwsRequestBudget | None = None,
    ):
        if (
            not isinstance(auth, AwsSdkAuth)
            or not auth.access_key_id
            or not auth.secret_access_key
            or (auth.session_token is not None and not auth.session_token)
            or not _REGION.fullmatch(region)
            or not 1 <= max_workers <= _MAX_WORKERS
        ):
            raise AwsSdkError("aws_configuration_error")
        # SDK debug output includes signed headers and secret-bearing STS bodies.
        for namespace in ("boto3", "botocore"):
            logging.getLogger(namespace).setLevel(logging.WARNING)
        self._auth = auth
        self.region = region
        self._control = control
        self._max_workers = max_workers
        self._slots: asyncio.Semaphore | None = None
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="openship-aws-sdk",
        )
        self._clients: dict[tuple[str, str], object] = {}
        self._running: dict[str, concurrent.futures.Future] = {}
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._http_session = BoundedAwsHttpSession(
            control=control,
            transport_factory=transport_factory,
            request_limit=max_requests,
            max_response_bytes=max_response_bytes,
            timeout_seconds=timeout_seconds,
            request_budget=request_budget,
        )

    def client(self, service: str, *, region: str | None = None):
        selected_region = region or self.region
        if self._closed or not isinstance(service, str) or not re.fullmatch(r"[a-z0-9-]{1,64}", service):
            raise AwsSdkError("aws_configuration_error")
        if not _REGION.fullmatch(selected_region):
            raise AwsSdkError("aws_configuration_error")
        key = (service, selected_region)
        existing = self._clients.get(key)
        if existing is not None:
            return existing
        botocore_session = botocore.session.Session()
        config_store = botocore_session.get_component("config_store")
        config_store.set_config_provider("profile", ConstantProvider(value=None))
        config_store.set_config_provider("data_path", ConstantProvider(value=None))
        for name, value in {
            "config_file": os.devnull,
            "credentials_file": os.devnull,
            "region": selected_region,
        }.items():
            botocore_session.set_config_variable(name, value)
        endpoint_resolver = botocore_session.get_component("endpoint_resolver")
        endpoint_metadata = endpoint_resolver.construct_endpoint(
            service,
            selected_region,
            use_dualstack_endpoint=False,
            use_fips_endpoint=False,
        )
        if (
            not isinstance(endpoint_metadata, dict)
            or endpoint_metadata.get("partition") != "aws"
            or not isinstance(endpoint_metadata.get("hostname"), str)
        ):
            raise AwsSdkError("aws_operation_not_supported")
        credential_scope = endpoint_metadata.get("credentialScope", {})
        signing_region = (
            credential_scope.get("region", selected_region)
            if isinstance(credential_scope, dict)
            else selected_region
        )
        if not isinstance(signing_region, str) or not _REGION.fullmatch(signing_region):
            raise AwsSdkError("aws_operation_not_supported")
        endpoint = f"https://{endpoint_metadata['hostname']}"
        session = boto3.session.Session(
            aws_access_key_id=self._auth.access_key_id,
            aws_secret_access_key=self._auth.secret_access_key,
            aws_session_token=self._auth.session_token,
            region_name=signing_region,
            botocore_session=botocore_session,
        )
        options = {
            "region_name": signing_region,
            "signature_version": "v4",
            "proxies": {},
            "retries": {"mode": "standard", "total_max_attempts": 1},
            "connect_timeout": 30,
            "read_timeout": 30,
            "max_pool_connections": 1,
            "use_dualstack_endpoint": False,
            "use_fips_endpoint": False,
            "ignore_configured_endpoint_urls": True,
        }
        if service == "s3":
            options["s3"] = {"addressing_style": "path"}
        sdk_client = session.client(
            service,
            region_name=signing_region,
            endpoint_url=endpoint,
            verify=True,
            config=Config(**options),
        )
        # Botocore's Endpoint calls its synchronous HTTP session's ``send``.
        # Replace only that pinned SDK seam; serialization, signing and parsing
        # remain inside the SDK.
        sdk_client._endpoint.http_session.close()
        sdk_client._endpoint.http_session = self._http_session
        self._clients[key] = sdk_client
        return sdk_client

    async def run(self, method, **parameters):
        if (
            self._closed
            or not callable(method)
            or getattr(method, "__self__", None) not in self._clients.values()
        ):
            raise AwsSdkError("aws_operation_not_supported")
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise AwsSdkError("aws_configuration_error")
        self._loop = loop
        self._http_session.bind_loop(loop)
        if self._slots is None:
            self._slots = asyncio.Semaphore(self._max_workers)
        await self._acquire_slot()
        if self._closed:
            self._slots.release()
            raise AwsSdkError("aws_cancelled")

        call_id = str(uuid.uuid4())
        worker = self._executor.submit(self._invoke, call_id, method, parameters)
        self._running[call_id] = worker
        worker.add_done_callback(lambda _done: loop.call_soon_threadsafe(self._slots.release))
        wrapped = asyncio.wrap_future(worker, loop=loop)
        try:
            while not worker.done():
                self._raise_if_stopped()
                timeout = self._control.request_timeout_seconds(30.0)
                if timeout <= 0:
                    self._cancel_call(call_id, worker)
                    await self._wait_worker(wrapped)
                    raise AwsSdkError("aws_timeout")
                try:
                    await asyncio.wait_for(
                        asyncio.shield(wrapped),
                        timeout=min(_POLL_SECONDS, timeout),
                    )
                except TimeoutError:
                    continue
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            self._cancel_call(call_id, worker)
            await self._wait_worker(wrapped)
            raise
        except ProviderError:
            self._cancel_call(call_id, worker)
            await self._wait_worker(wrapped)
            raise AwsSdkError("aws_cancelled") from None
        except ClientError as exc:
            raise AwsSdkError(_safe_client_error_code(method, exc)) from None
        except AwsSdkError:
            raise
        except Exception:  # noqa: BLE001 - keep arbitrary SDK errors secret-safe
            raise AwsSdkError("aws_unavailable") from None
        finally:
            self._running.pop(call_id, None)

    async def _acquire_slot(self) -> None:
        assert self._slots is not None
        acquire = asyncio.create_task(self._slots.acquire())
        try:
            while not acquire.done():
                if self._closed:
                    acquire.cancel()
                    await asyncio.gather(acquire, return_exceptions=True)
                    raise AwsSdkError("aws_cancelled")
                self._raise_if_stopped()
                timeout = self._control.request_timeout_seconds(30.0)
                if timeout <= 0:
                    acquire.cancel()
                    await asyncio.gather(acquire, return_exceptions=True)
                    raise AwsSdkError("aws_timeout")
                await asyncio.wait((acquire,), timeout=min(_POLL_SECONDS, timeout))
            await acquire
            if self._closed:
                self._slots.release()
                raise AwsSdkError("aws_cancelled")
        except ProviderError:
            acquire.cancel()
            await asyncio.gather(acquire, return_exceptions=True)
            raise AwsSdkError("aws_cancelled") from None
        except asyncio.CancelledError:
            acquire.cancel()
            await asyncio.gather(acquire, return_exceptions=True)
            raise

    def _raise_if_stopped(self) -> None:
        try:
            self._control.raise_if_stopped()
        except ProviderError:
            raise ProviderError("cancelled") from None

    def _invoke(self, call_id: str, method, parameters: dict):
        self._http_session.bind_call(call_id)
        try:
            return method(**parameters)
        finally:
            self._http_session.clear_call()

    def _cancel_call(self, call_id: str, worker) -> None:
        self._http_session.cancel_call(call_id)
        worker.cancel()

    async def _wait_worker(self, wrapped: asyncio.Future) -> None:
        try:
            await asyncio.shield(wrapped)
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 - drain worker regardless of result
            return

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        running = tuple(self._running.items())
        for call_id, worker in running:
            self._cancel_call(call_id, worker)
        wrapped = [asyncio.wrap_future(worker) for _, worker in running]
        if wrapped:
            await asyncio.gather(*(self._wait_worker(item) for item in wrapped))
        for sdk_client in self._clients.values():
            sdk_client.close()
        self._clients.clear()
        # Every submitted worker is awaited above, so the pool has no active
        # SDK calls left to drain. Avoid blocking the event loop's default
        # executor while joining this dedicated pool during shutdown.
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        await self.aclose()


def _safe_client_error_code(method, error: ClientError) -> str:
    code = error.response.get("Error", {}).get("Code")
    response_metadata = error.response.get("ResponseMetadata", {})
    status = response_metadata.get("HTTPStatusCode") if isinstance(response_metadata, dict) else None
    if status == 403 or code in {
        "AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "Forbidden"
    }:
        service_name = getattr(getattr(method.__self__, "meta", None), "service_model", None)
        if (
            getattr(service_name, "service_name", None) == "sts"
            and getattr(method, "__name__", "") == "assume_role"
        ):
            return "aws_trust_denied"
        return "aws_access_denied"
    if status == 429:
        return "aws_throttled"
    if not isinstance(code, str):
        return "aws_unavailable"
    if code in {"ExpiredToken", "ExpiredTokenException"}:
        return "aws_credentials_expired"
    if code in {"InvalidClientTokenId", "UnrecognizedClientException", "SignatureDoesNotMatch"}:
        return "aws_credentials_invalid"
    if code in {
        "NoSuchBucket", "NoSuchKey", "NoSuchEntity", "NotFound",
        "ResourceNotFoundException", "DBInstanceNotFound", "InvalidInstanceID.NotFound",
    }:
        return "aws_not_found"
    if code in {
        "Throttling", "ThrottlingException", "RequestLimitExceeded", "TooManyRequestsException",
        "SlowDown",
    }:
        return "aws_throttled"
    if code in {
        "InvalidParameter", "InvalidParameterValue", "InvalidParameterCombination",
        "ValidationException", "MalformedQueryString", "SerializationException",
    }:
        return "aws_invalid_parameters"
    if code in {"UnknownOperationException", "UnsupportedOperation", "NotImplemented"}:
        return "aws_operation_not_supported"
    return "aws_unavailable"
