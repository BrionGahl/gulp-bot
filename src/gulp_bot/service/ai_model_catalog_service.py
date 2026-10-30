"""What counts as a model name. No Discord in here."""

from __future__ import annotations

from ..exception.ai_exception import ModelNameProblem


def normalize_model_name(name: str) -> str | None:
    """What a developer typed as a model: `None` for "default" (the model the bot was configured with), or
    the name, if it looks like one; otherwise raises `ModelNameProblem` saying what is wrong. Whether the
    server has it is checked separately."""
    name = name.strip()
    if name.lower() == "default":
        return None
    if not name or len(name) > 64:
        raise ModelNameProblem("A model name is 1 to 64 characters long.")
    # `:` for a model's tag (`qwen3.5:9b`), `/` for one pulled from a registry (`hf.co/...`).
    if not all((char.isascii() and char.isalnum()) or char in "-._:/" for char in name):
        raise ModelNameProblem("A model name has only letters, digits, `-`, `.`, `_`, `:` and `/`.")
    return name
