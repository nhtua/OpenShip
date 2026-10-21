"""Deterministic display filenames for retained search results."""

from slugify import slugify

_MAX_DISPLAY_NAME_BYTES = 255
_EXTENSION = ".json"
_FALLBACK_STEM = "search-results"


def search_artifact_filename(query: str) -> str:
    """Convert a search query to a bounded, ASCII snake-case JSON filename."""
    stem = slugify(query, separator="_", lowercase=True, allow_unicode=False)
    max_stem_length = _MAX_DISPLAY_NAME_BYTES - len(_EXTENSION)
    stem = stem[:max_stem_length].rstrip("_")
    return f"{stem or _FALLBACK_STEM}{_EXTENSION}"
