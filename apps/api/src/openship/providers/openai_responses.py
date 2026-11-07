"""Bounded OpenAI Responses transport: stateless, private, and pinned."""

import json

from pydantic import ValidationError

from src.openship.providers.configuration import destination_label
from src.openship.providers.contracts import (
    ProviderEvent,
    ResponsesContinuation,
    TokenUsage,
    ToolCall,
    validate_request,
)
from src.openship.providers.errors import ProviderError
from src.openship.providers.openai import _SDK_ERRORS, OpenAIAdapter, _sdk_failure
from src.openship.providers.openai_schemas import (
    normalize_arguments,
    unique_json_object,
    wire_schema,
)


class OpenAIResponsesAdapter(OpenAIAdapter):
    """Translate Responses events without provider-stored state."""

    async def stream(self, request, config, control):
        validate_request(request, config)
        inputs = []
        for message in request.messages:
            if message.role == "assistant" and message.continuation:
                state = message.continuation
                if (state.profile_revision != config.profile_revision or state.model != config.model
                        or state.destination != destination_label(config.endpoint)):
                    raise ProviderError("unsupported_capability", safe_message="Saved model continuation belongs to another profile.")
                inputs.extend(state.items)
            elif message.role == "tool":
                inputs.append({"type": "function_call_output", "call_id": message.tool_call_id,
                               "output": message.content})
            elif message.role == "assistant" and message.tool_calls:
                raise ProviderError("unsupported_capability", safe_message="Saved model continuation is unavailable.")
            else:
                inputs.append({"role": message.role, "content": message.content})
        client = self._client(config)
        tool_definitions = []
        for tool in request.tools:
            parameters, strict = wire_schema(tool.parameters)
            tool_definitions.append({"type": "function", "name": tool.name,
                                     "description": tool.description, "parameters": parameters,
                                     "strict": strict})
        kwargs = {
            "model": config.model,
            "input": inputs,
            "max_output_tokens": request.max_output_tokens,
            "reasoning": {"effort": config.adapter_options.reasoning_effort},
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "parallel_tool_calls": False,
            "stream": True,
            "timeout": control.request_timeout_seconds(config.limits.request_timeout_seconds),
        }
        if tool_definitions:
            kwargs["tools"] = tool_definitions
            kwargs["tool_choice"] = request.tool_choice
        try:
            stream = await client.responses.create(**kwargs)
        except _SDK_ERRORS as error:
            raise _sdk_failure(error) from error
        terminal = None
        terminal_kind = None
        argument_bytes = 0
        argument_fragments = {}
        try:
            async for event in stream:
                control.raise_if_stopped()
                if terminal is not None:
                    raise ProviderError("invalid_response")
                if event.type == "response.function_call_arguments.delta":
                    if not isinstance(event.delta, str):
                        raise ProviderError("invalid_response")
                    argument_bytes += len(event.delta.encode("utf-8"))
                    if argument_bytes > 64 * 1024:
                        raise ProviderError("invalid_response")
                    item_id = getattr(event, "item_id", None)
                    argument_fragments[item_id] = argument_fragments.get(item_id, "") + event.delta
                if event.type in ("response.completed", "response.failed", "response.incomplete"):
                    if terminal is not None:
                        raise ProviderError("invalid_response")
                    terminal = event.response
                    terminal_kind = event.type
                elif event.type in {
                    "response.output_text.delta",
                    "response.reasoning_summary_text.delta",
                    "response.function_call_arguments.delta",
                } or (
                    event.type == "response.output_item.added"
                    and getattr(getattr(event, "item", None), "type", None)
                    in {"function_call", "reasoning", "message"}
                ):
                    yield ProviderEvent(type="activity")
                elif event.type in ("response.completed", "response.failed", "response.incomplete"):
                    if terminal is not None:
                        raise ProviderError("invalid_response")
                    terminal = event.response
                    terminal_kind = event.type
            if terminal is None:
                raise ProviderError("invalid_response")
            if terminal_kind == "response.failed":
                raise ProviderError("invalid_response")
            if terminal_kind == "response.incomplete":
                if (terminal.status != "incomplete"
                        or getattr(getattr(terminal, "incomplete_details", None), "reason", None)
                        != "max_output_tokens"):
                    raise ProviderError("invalid_response")
                yield ProviderEvent(type="stream_finished", reason="length", actual_model=terminal.model)
                return
            if terminal_kind != "response.completed" or terminal.status != "completed":
                raise ProviderError("invalid_response")
            if terminal.model != config.model and terminal.model not in config.qualified_model_aliases:
                raise ProviderError("unexpected_model")
            usage = getattr(terminal, "usage", None)
            if usage is not None:
                yield ProviderEvent(type="usage_update", usage=TokenUsage(
                    input_tokens=getattr(usage, "input_tokens", None),
                    output_tokens=getattr(usage, "output_tokens", None),
                    total_tokens=getattr(usage, "total_tokens", None),
                    cached_input_tokens=getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None),
                    reasoning_tokens=getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", None),
                    source="provider",
                ))
            items = tuple(
                item if isinstance(item, dict) else item.model_dump(mode="json", exclude_none=True)
                for item in terminal.output
            )
            if any(part.get("type") == "refusal" for item in items if item.get("type") == "message"
                   for part in item.get("content", []) if isinstance(part, dict)):
                yield ProviderEvent(type="stream_finished", reason="refusal", actual_model=terminal.model)
                return
            item_ids = [item.get("id") for item in items]
            call_ids = [item["call_id"] for item in items if item.get("type") == "function_call"]
            if len(item_ids) != len(set(item_ids)) or len(call_ids) != len(set(call_ids)):
                raise ProviderError("invalid_response")
            try:
                continuation = ResponsesContinuation(
                    version=1, protocol="openai.responses", profile_revision=config.profile_revision,
                    model=config.model, destination=destination_label(config.endpoint), items=items,
                )
            except ValidationError as error:
                raise ProviderError("invalid_response", cause=error) from error
            calls = [item for item in items if item["type"] == "function_call"]
            if len(calls) > 1 or (calls and request.tool_choice == "none"):
                raise ProviderError("invalid_response")
            terminal_arguments = {item["id"]: item["arguments"] for item in calls}
            if argument_fragments and argument_fragments != terminal_arguments:
                raise ProviderError("invalid_response")
            final_text = [part["text"] for item in items
                          if item["type"] == "message" and item.get("phase") != "commentary"
                          for part in item["content"] if part["type"] == "output_text" and part["text"]]
            if not calls and not final_text:
                raise ProviderError("invalid_response")
            if calls:
                call = calls[0]
                definition = next((tool for tool in request.tools if tool.name == call["name"]), None)
                if definition is None or len(call["arguments"].encode()) > 64 * 1024:
                    raise ProviderError("invalid_response")
                try:
                    arguments = json.loads(call["arguments"], object_pairs_hook=unique_json_object,
                                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    if not isinstance(arguments, dict):
                        raise TypeError("arguments must be an object")
                    _, strict = wire_schema(definition.parameters)
                    arguments = normalize_arguments(definition.parameters, arguments, strict=strict)
                    ready = ToolCall(provider_call_id=call["call_id"], name=call["name"], arguments=arguments)
                except (TypeError, ValueError) as error:
                    raise ProviderError("invalid_response", cause=error) from error
                yield ProviderEvent(type="tool_call_ready", tool_call=ready)
            for text in final_text:
                yield ProviderEvent(type="text_delta", text=text)
            yield ProviderEvent(type="continuation", continuation=continuation)
            yield ProviderEvent(type="stream_finished", reason="tool_calls" if calls else "stop", actual_model=terminal.model)
        except _SDK_ERRORS as error:
            raise _sdk_failure(error) from error
        finally:
            await stream.aclose()
