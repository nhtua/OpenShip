"""Data validation for one sandbox-declared output set.

Provider transport owns byte collection; this module only decides whether the
received bytes are the declared, well-formed data outputs they claim to be.
"""

import json

from .contracts import ExecutionRequest, ExecutionResult

_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_OUTPUT_BYTES = 8 * 1024 * 1024


class OutputValidationFailed(Exception):
    """The received bytes are not the declared data outputs."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def validate_outputs(request: ExecutionRequest, result: ExecutionResult) -> dict[str, bytes]:
    """Return exactly the declared output bytes after shape and content checks."""
    declared = {spec.alias: spec for spec in request.outputs}
    if set(result.outputs) != set(declared):
        raise OutputValidationFailed("outputs do not match the declared set")
    total = sum(len(data) for data in result.outputs.values())
    if total > _MAX_TOTAL_OUTPUT_BYTES:
        raise OutputValidationFailed("total output bytes exceeded the bound")
    validated: dict[str, bytes] = {}
    for alias, spec in declared.items():
        data = result.outputs[alias]
        if len(data) > _MAX_OUTPUT_BYTES:
            raise OutputValidationFailed(f"output {alias!r} exceeded its bound")
        if spec.media_type == "application/json":
            try:
                json.loads(data.decode("utf-8"))
            except (UnicodeError, ValueError, RecursionError) as failure:
                raise OutputValidationFailed(
                    f"output {alias!r} is not well-formed JSON"
                ) from failure
        else:
            try:
                data.decode("utf-8")
            except UnicodeError as failure:
                raise OutputValidationFailed(
                    f"output {alias!r} is not valid UTF-8 text"
                ) from failure
        validated[alias] = data
    return validated
