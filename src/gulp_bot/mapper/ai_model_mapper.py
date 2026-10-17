"""The conversation into the requests the model APIs take, and their answers into `ModelReply`."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from ..core.text import truncate_chars
from ..schema.ai_schema import ModelReply, Turn

# Room for an answer of about the 2000 characters Discord allows in a message, with some to spare in case
# the model spends part of it thinking.
MAX_TOKENS = 1024

# The context window asked of a local model, in tokens. The prompt, 8 turns of memory and a question fit
# in it comfortably; a bigger one takes more GPU memory and makes answers slower.
LOCAL_CONTEXT_TOKENS = 8192


def wire_messages(turns: Sequence[Turn]) -> list[dict[str, str]]:
    """The conversation as both APIs take it."""
    return [{"role": turn.role.value, "content": turn.content} for turn in turns]


def anthropic_request(model: str, system: str, turns: Sequence[Turn], effort: str | None, max_tokens: int = MAX_TOKENS) -> dict[str, Any]:
    """A request to the Anthropic Messages API."""
    request: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system, "messages": wire_messages(turns)}
    if effort is not None:
        request["output_config"] = {"effort": effort}
    return request


def ollama_request(model: str, system: str, turns: Sequence[Turn], think: bool | None) -> dict[str, Any]:
    """A request to Ollama's `/api/chat`: the system prompt first, as a message of its own, the whole
    answer in one response (`stream: false`), with the context window (`num_ctx`) and the most tokens to
    answer with (`num_predict`)."""
    request: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *wire_messages(turns)],
        "stream": False,
        "options": {"num_ctx": LOCAL_CONTEXT_TOKENS, "num_predict": MAX_TOKENS},
    }
    if think is not None:
        request["think"] = think
    return request


def anthropic_reply(body: Any) -> ModelReply:
    """An Anthropic API answer: the text of its text blocks, joined."""
    body = body if isinstance(body, dict) else {}
    text = "".join(block.get("text") or "" for block in body.get("content") or [] if isinstance(block, dict) and block.get("type") == "text").strip()
    stop_reason = body.get("stop_reason")
    usage = body.get("usage")
    tokens = (int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)) if isinstance(usage, dict) else None
    return ModelReply(text=text, cut_short=stop_reason == "max_tokens", stop_reason=stop_reason, tokens=tokens)


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
    """The message an API gave for an error, or the start of the body if it isn't the usual JSON. The
    Anthropic API says `{"error": {"message": "..."}}`; Ollama says `{"error": "..."}`."""
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(error, str):
            return error
    return truncate_chars(body, 300)
