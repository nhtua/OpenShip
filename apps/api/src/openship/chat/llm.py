from openai import OpenAI

from ..config import settings

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = settings.openai_api_key or None
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not configured. "
                "Set the OPENAI_API_KEY environment variable."
            )
        _client = OpenAI(api_key=api_key, base_url=settings.openai_base_url)
    return _client


def chat(messages: list[dict], model: str | None = None, max_tokens: int | None = None):
    client = _get_client()
    model = model or settings.openai_model
    kwargs = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return client.chat.completions.create(**kwargs)
