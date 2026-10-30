"""The conversation into the request Ollama takes, and its answer into `ModelReply`."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from ..core.http_client import error_body
from ..schema.ai_schema import ModelReply, Turn

# Room for an answer of about the 2000 characters Discord allows in a message, with some to spare in case
# the model spends part of it thinking.
MAX_TOKENS = 1024

# The context window asked of the model, in tokens. The prompt, 8 turns of memory and a question fit
# in it comfortably; a bigger one takes more GPU memory and makes answers slower.
LOCAL_CONTEXT_TOKENS = 8192

# What a model that thinks may write: Ollama counts the thinking in `num_predict` too, and a model
# like Qwen easily thinks for more than `MAX_TOKENS` about a one-line question. With only `MAX_TOKENS`
# it runs out while still thinking and answers nothing. The context window grows by as much, so the
# prompt and the memory still fit next to it.
LOCAL_THINKING_TOKENS = 4096
LOCAL_THINKING_CONTEXT_TOKENS = LOCAL_CONTEXT_TOKENS + LOCAL_THINKING_TOKENS - MAX_TOKENS


def wire_messages(turns: Sequence[Turn]) -> list[dict[str, str]]:
    """The conversation as Ollama takes it."""
    return [{"role": turn.role.value, "content": turn.content} for turn in turns]


def ollama_request(
    model: str, system: str, turns: Sequence[Turn], think: bool | None, temperature: float | None = None, schema: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A request to Ollama's `/api/chat`: the system prompt first, as a message of its own, the whole
    answer in one response (`stream: false`), with the context window (`num_ctx`) and the most tokens to
    answer with (`num_predict`). Unless thinking is off (`think` false), the model may think first, so it
    gets room for that too. `temperature` is how much the model varies its wording, from 0 (always the
    likeliest) up; `None` leaves the model's own. With a JSON `schema`, the answer is JSON in that shape
    (Ollama's `format`)."""
    may_think = think is not False
    request: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *wire_messages(turns)],
        "stream": False,
        "options": {
            "num_ctx": LOCAL_THINKING_CONTEXT_TOKENS if may_think else LOCAL_CONTEXT_TOKENS,
            "num_predict": LOCAL_THINKING_TOKENS if may_think else MAX_TOKENS,
        },
    }
    if temperature is not None:
        request["options"]["temperature"] = temperature
    if schema is not None:
        request["format"] = schema
    if think is not None:
        request["think"] = think
    return request


def ollama_reply(body: Any) -> ModelReply:
    """An Ollama answer. Thinking that a model wrote into its answer (`<think>...</think>`) is taken out: it
    is not meant for the channel. Ollama says `length` when it ran out of tokens."""
    body = body if isinstance(body, dict) else {}
    input_tokens, output_tokens = body.get("prompt_eval_count"), body.get("eval_count")
    tokens = None if input_tokens is None and output_tokens is None else (int(input_tokens or 0), int(output_tokens or 0))
    message = body.get("message")
    content = message.get("content") or "" if isinstance(message, dict) else ""
    done_reason = body.get("done_reason")
    return ModelReply(text=strip_thinking(content), cut_short=done_reason == "length", stop_reason=done_reason, tokens=tokens)


def strip_thinking(text: str) -> str:
    """`text` without the thinking a model wrote into it (`<think>...</think>`), trimmed. Thinking that
    never ends (the model ran out of tokens while thinking) takes the rest of the text with it."""
    opening, closing = "<think>", "</think>"
    kept: list[str] = []
    rest = text
    while (start := rest.find(opening)) != -1:
        kept.append(rest[:start])
        end = rest.find(closing, start)
        if end == -1:
            rest = ""
            break
        rest = rest[end + len(closing) :]
    kept.append(rest)
    return "".join(kept).strip()


def api_error_message(body: str) -> str:
    """The message Ollama gave for an error (`{"error": "..."}`), or the start of the body if it isn't
    that JSON."""
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, str):
            return error
    return error_body(body)
