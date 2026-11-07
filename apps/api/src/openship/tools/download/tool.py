"""Prepare validated, bounded downloads for fenced artifact publication."""

from urllib.parse import unquote, urlsplit

from src.openship.config import settings
from src.openship.tools.download.contracts import DownloadObservation, validate_download
from src.openship.tools.download.transport import (
    DownloadError,
    download_bytes,
    download_stream,
)


def download_name(url: str) -> str:
    try:
        path = urlsplit(url).path
        name = unquote(path.rsplit("/", 1)[-1])
        if (not name or len(name.encode()) > 128 or name in (".", "..")
                or any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in name)):
            raise ValueError("invalid name")
        if not name.lower().endswith((".csv", ".json")):
            raise ValueError("unsupported file")
        return name
    except (ValueError, UnicodeError):
        raise DownloadError() from None


async def download_file(arguments, invocation, control) -> DownloadObservation:
    try:
        name = download_name(arguments.url)
        if invocation is None:
            content = await download_bytes(arguments.url, control)
            return DownloadObservation(content=content, name=name,
                                       format=validate_download(name, content))
        store = invocation.artifact_store
        artifact_id = invocation.reserved_artifact_id
        if store is None or artifact_id is None:
            raise DownloadError()
        authorization = invocation.authorization

        async def receive(write):
            await download_stream(arguments.url, control, write)

        staged = await store.stage_received(
            artifact_id, receive,
            media_type="application/json" if name.lower().endswith(".json") else "text/plain",
            max_bytes=settings.artifact_upload_max_bytes,
            project_id=authorization.project_id, conversation_id=authorization.conversation_id,
        )
        try:
            with store.open_staged_verified(staged) as source:
                content = source.read()
            file_format = validate_download(name, content)
            return DownloadObservation(content=content, name=name, format=file_format, staged=staged)
        except BaseException:
            store.remove_staging(artifact_id, authorization.project_id, authorization.conversation_id)
            raise
    except (ValueError, UnicodeError):
        raise DownloadError() from None
