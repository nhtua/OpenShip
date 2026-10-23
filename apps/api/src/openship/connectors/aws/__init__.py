"""AWS source validation, role assumption and invocation-scoped access."""
"""AWS credential brokerage and invocation-scoped clients."""

from .broker import AwsBrokerError, AwsRoleBroker
from .clients import AwsRoleAccess
from .contracts import AwsAssumedRole, AwsBootstrapAuth, AwsRoleCacheKey

__all__ = [
    "AwsAssumedRole",
    "AwsBootstrapAuth",
    "AwsBrokerError",
    "AwsRoleAccess",
    "AwsRoleBroker",
    "AwsRoleCacheKey",
]
