from openai import OpenAI

from ..config import settings

client = OpenAI(
    api_key=settings.openai_api_key or None,
    base_url=settings.openai_base_url,
)


def chat(messages: list[dict], model: str | None = None):
    model = model or settings.openai_model
    return client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
    )
