"""Invocation-scoped AWS SDK clients with connector rechecks and renewal."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from ...capabilities.contracts import ConnectorRef
from ...connectors.contracts import ConnectorConfiguration
from ...execution.aws_sdk import AwsSdkContext
from ...execution.aws_transport import AwsRequestBudget, AwsSdkError
from ...providers.control import InvocationControl
from .contracts import AwsAssumedRole

if TYPE_CHECKING:
    from .broker import AwsRoleBroker

_ALLOWED_READ_OPERATIONS = {
    "cloudwatch": frozenset({"get_metric_data"}),
    "s3": frozenset({"get_bucket_location"}),
    "sts": frozenset({"get_caller_identity"}),
}


class AwsRoleAccess:
    """Invocation-scoped client proxies that recheck authority before each call."""

    def __init__(
        self,
        *,
        broker: AwsRoleBroker,
        project_id: uuid.UUID,
        reference: ConnectorRef,
        run_id: uuid.UUID,
        control: InvocationControl,
        request_budget: AwsRequestBudget,
        configuration: ConnectorConfiguration,
        session: AwsAssumedRole,
        sdk_context: AwsSdkContext,
    ):
        self._broker = broker
        self._project_id = project_id
        self._reference = reference
        self._run_id = run_id
        self._control = control
        self._request_budget = request_budget
        self._configuration = configuration
        self._session = session
        self._sdk_context = sdk_context
        self._contexts = {sdk_context}
        self._active: dict[AwsSdkContext, int] = {sdk_context: 0}
        self._retired: set[AwsSdkContext] = set()
        self._run_lock = asyncio.Lock()
        self._closed = False

    def client(self, service: str) -> _AwsServiceClient:
        if service not in {"sts", "cloudwatch", "s3"}:
            raise AwsSdkError("aws_operation_not_supported")
        return _AwsServiceClient(self, service)

    @property
    def configuration(self) -> ConnectorConfiguration:
        return self._configuration

    @property
    def request_budget(self) -> AwsRequestBudget:
        return self._request_budget

    async def run(self, operation: _AwsSdkMethod, **parameters):
        if (
            not isinstance(operation, _AwsSdkMethod)
            or operation.access is not self
            or operation.name
            not in _ALLOWED_READ_OPERATIONS.get(operation.service, frozenset())
        ):
            raise AwsSdkError("aws_operation_not_supported")
        async with self._run_lock:
            if self._closed:
                raise AwsSdkError("aws_cancelled")
            self._broker._validate_before_dispatch(
                project_id=self._project_id,
                reference=self._reference,
            )
            if not self._broker._usable(self._session, self._broker._now()):
                replacement = await self._broker.acquire(
                    project_id=self._project_id,
                    reference=self._reference,
                    run_id=self._run_id,
                    control=self._control,
                    request_budget=self._request_budget,
                )
                old_context = self._sdk_context
                self._sdk_context = replacement._sdk_context
                self._contexts.add(self._sdk_context)
                self._active[self._sdk_context] = 0
                self._session = replacement._session
                replacement._closed = True
                self._retired.add(old_context)
                if self._active.get(old_context, 0) == 0:
                    self._contexts.discard(old_context)
                    self._active.pop(old_context, None)
                    self._retired.discard(old_context)
                    await old_context.aclose()
            context = self._sdk_context
            sdk_client = context.client(operation.service)
            api_name = sdk_client.meta.method_to_api_mapping.get(operation.name)
            if api_name not in sdk_client.meta.service_model.operation_names:
                raise AwsSdkError("aws_operation_not_supported")
            method = getattr(sdk_client, operation.name)
            self._active[context] = self._active.get(context, 0) + 1
        try:
            return await context.run(method, **parameters)
        finally:
            async with self._run_lock:
                self._active[context] = max(0, self._active.get(context, 1) - 1)
                if context in self._retired and self._active[context] == 0:
                    self._contexts.discard(context)
                    self._active.pop(context, None)
                    self._retired.discard(context)
                    await context.aclose()

    async def aclose(self) -> None:
        async with self._run_lock:
            if self._closed:
                return
            self._closed = True
            contexts = tuple(self._contexts)
            self._contexts.clear()
            self._active.clear()
            self._retired.clear()
        for context in contexts:
            await context.aclose()

    def __repr__(self) -> str:
        return "AwsRoleAccess(project-scoped, runtime-only)"


class _AwsServiceClient:
    __slots__ = ("_access", "_service")

    def __init__(self, access: AwsRoleAccess, service: str):
        self._access = access
        self._service = service

    def __getattr__(self, name: str) -> _AwsSdkMethod:
        if name.startswith("_"):
            raise AttributeError(name)
        return _AwsSdkMethod(self._access, self._service, name)

    def __repr__(self) -> str:
        return f"AwsServiceClient(service={self._service!r})"


class _AwsSdkMethod:
    __slots__ = ("access", "name", "service")

    def __init__(self, access: AwsRoleAccess, service: str, name: str):
        self.access = access
        self.service = service
        self.name = name

    def __repr__(self) -> str:
        return f"AwsSdkMethod(service={self.service!r}, name={self.name!r})"


__all__ = ["AwsRoleAccess"]
