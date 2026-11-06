"""Only fully decoded, bounded raster outputs may be retained for preview."""

import base64
import hashlib
import struct
import uuid
import zlib

import pytest

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.config import settings
from src.openship.sandboxes.contracts import (
    ExecutionRequest,
    ExecutionResult,
    OutputSpec,
    RuntimeRequirements,
)
from src.openship.sandboxes.outputs import (
    OutputValidationFailed,
    validate_image,
    validate_outputs,
)
from src.openship.sandboxes.python_tool import PythonArguments
from src.openship.sandboxes.tool import runtime_requirements
from tests.sandboxes.test_tool import enable_sandbox

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAYAAAC09K7GAAAAFUlEQVR4nGP8z8DwnwEJMDGg"
    "AQwBAGFrAgRCl+p5AAAAAElFTkSuQmCC"
)
_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwh"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAAR"
    "CAAGAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAA"
    "AgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK"
    "FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWG"
    "h4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl"
    "5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREA"
    "AgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYk"
    "NOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE"
    "hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk"
    "5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwCOiiivkD83P//Z"
)
_BMP = base64.b64decode(
    "Qk06AAAAAAAAADYAAAAoAAAAAQAAAAEAAAABABgAAAAAAAQAAADEDgAAxA4AAAAAAAAAAAAAAwIBAA=="
)


def _solid_png(width, height):
    """Build a PNG of declared size without materializing its decoded pixels."""
    row = b"\x00" + b"\x01\x02\x03" * width
    compressor = zlib.compressobj()
    compressed = compressor.compress(row)
    stream = bytearray(compressed * height)
    stream += compressor.flush()

    def chunk(tag, body):
        return (
            struct.pack(">I", len(body))
            + tag
            + body
            + struct.pack(">I", zlib.crc32(tag + body))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", bytes(stream))
        + chunk(b"IEND", b"")
    )


def _execution(**outputs):
    specs = tuple(
        OutputSpec(alias=alias, media_type="image/png", kind="data") for alias in outputs
    )
    request = ExecutionRequest(
        root_run_id=uuid.uuid4(),
        operation_id=uuid.uuid4(),
        requirements=RuntimeRequirements(
            provider_id="opensandbox:test", profile_id="python-stdlib",
            image="python:3.11-slim@sha256:" + "0" * 64,
            dependency_identity="stdlib-only",
        ),
        source=ArtifactRef(
            id=uuid.uuid4(), sha256=hashlib.sha256(b"x").hexdigest(),
            size_bytes=1, media_type="text/plain",
        ),
        inputs=(),
        parameters={},
        outputs=specs,
    )
    result = ExecutionResult(exit_code=0, stdout="", stderr="", outputs=outputs)
    return request, result


def test_valid_png_accepted():
    validate_image(_PNG, "image/png")


def test_valid_jpeg_accepted():
    validate_image(_JPEG, "image/jpeg")


@pytest.mark.parametrize("data", [_PNG, _JPEG])
def test_truncated_image_rejected(data):
    with pytest.raises(OutputValidationFailed):
        validate_image(data[: len(data) // 2], "image/png" if data is _PNG else "image/jpeg")


@pytest.mark.parametrize("data, media", ((_PNG, "image/jpeg"), (_JPEG, "image/png")))
def test_declared_media_mismatch_rejected(data, media):
    with pytest.raises(OutputValidationFailed):
        validate_image(data, media)


@pytest.mark.parametrize("width, height", ((4097, 1), (4001, 4000)))
def test_4097_dimension_or_16000001_pixels_rejected(width, height):
    with pytest.raises(OutputValidationFailed):
        validate_image(_solid_png(width, height), "image/png")


def test_decompression_bomb_rejected():
    data = _solid_png(4001, 4001)
    assert len(data) < 8 * 1024
    with pytest.raises(OutputValidationFailed):
        validate_image(data, "image/png")


@pytest.mark.parametrize("media", ["image/bmp", "image/svg+xml", "image/gif"])
def test_non_raster_media_rejected(media):
    with pytest.raises(OutputValidationFailed):
        validate_image(_PNG, media)


def test_bmp_bytes_rejected():
    with pytest.raises(OutputValidationFailed):
        validate_image(_BMP, "image/png")


def test_output_media_admits_images_rejects_bmp():
    PythonArguments(
        source="print(1)\n", input_ids={}, parameters={}, output_media={"chart.png": "image/png", "photo.jpg": "image/jpeg"}
    )
    with pytest.raises(ValueError):
        PythonArguments(
            source="print(1)\n", input_ids={}, parameters={}, output_media={"chart.bmp": "image/bmp"}
        )


def _noise_png(width, height):
    """A PNG whose rows are incompressible so bytes survive to the bounds."""
    compressor = zlib.compressobj(0)
    stream = bytearray()
    counter = 0
    for _ in range(height):
        row = bytearray(b"\x00")
        while len(row) < 1 + width * 3:
            counter += 1
            row += hashlib.sha256(counter.to_bytes(8, "big")).digest()
        stream += compressor.compress(bytes(row[: 1 + width * 3]))
    stream += compressor.flush()
    return _chunked_png(width, height, bytes(stream))


def _chunked_png(width, height, idat):
    def chunk(tag, body):
        return (
            struct.pack(">I", len(body))
            + tag
            + body
            + struct.pack(">I", zlib.crc32(tag + body))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def test_oversized_images_exceed_byte_and_aggregate_bounds():
    noisy = _noise_png(1024, 1024)
    assert len(noisy) > 3 * 1024 * 1024
    request, result = _execution(first=noisy, second=noisy, third=noisy)
    with pytest.raises(OutputValidationFailed):
        validate_outputs(request, result)
    single_request, single_result = _execution(first=noisy)
    assert len(noisy) < 4 * 1024 * 1024
    validate_outputs(single_request, single_result)


def test_runtime_identity_changes_with_plotting_dependencies(monkeypatch, tmp_path):
    enable_sandbox(monkeypatch, tmp_path)
    base = runtime_requirements()
    monkeypatch.setattr(settings, "sandbox_image", "openship-python-plot:local")
    monkeypatch.setattr(
        settings, "sandbox_dependency_identity", "matplotlib-pinned-3-9-2"
    )
    plotting = runtime_requirements()
    assert base != plotting
    assert base.key != plotting.key
