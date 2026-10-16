"""Role mentions in text the bot posts."""

from __future__ import annotations


def replace_role_mentions(text: str, replacements: list[tuple[str, int]]) -> str:
    """Replaces `@RoleName` placeholders in `text` with each role's real Discord mention syntax
    (`<@&ROLE_ID>`), so the role actually gets pinged instead of showing as plain text."""
    for placeholder, role_id in replacements:
        text = text.replace(placeholder, f"<@&{role_id}>")
    return text
