"""Text helpers shared by every layer. No Discord in here."""

from __future__ import annotations

# Discord rejects messages longer than this many characters.
DISCORD_MAX_CHARS = 2000


def utf8_length(text: str) -> int:
    """The length of `text` in UTF-8 bytes: the measure the Rust bot used for its size budgets, which is
    never less than the characters Discord counts."""
    return len(text.encode("utf-8"))


def truncate_chars(text: str, max_chars: int) -> str:
    """At most `max_chars` characters of `text`, trimmed."""
    text = text.strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def truncate_with_ellipsis(text: str, max_chars: int) -> str:
    """`text`, or its first `max_chars - 1` characters and `…` if it is longer than `max_chars`."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def single_line(text: str) -> str:
    """`text` on one line: every run of whitespace, line breaks included, becomes one space."""
    return " ".join(text.split())


def ascii_lower(text: str) -> str:
    """`text` with only its ASCII letters lowercased, for comparisons that ignore ASCII case alone."""
    return "".join(char.lower() if char.isascii() else char for char in text)


def plural(count: int, noun: str) -> str:
    """ "1 pull", "34 pulls"."""
    return f"1 {noun}" if count == 1 else f"{count} {noun}s"


def pull_time(ms: float) -> str:
    """A time into a pull, given in milliseconds, as `m:ss`: `1:05`."""
    seconds = max(int(ms // 1000), 0)
    return f"{seconds // 60}:{seconds % 60:02}"


def split_message(text: str, max_chars: int = DISCORD_MAX_CHARS) -> list[str]:
    """Splits `text` into messages of at most `max_chars` characters, cutting at a line break if it can,
    else at a space, else in the middle of a word. Nothing is lost except the whitespace at the cuts.
    Returns nothing for a blank text."""
    max_chars = max(max_chars, 1)
    chunks: list[str] = []
    rest = text.strip()

    while rest:
        if len(rest) <= max_chars:
            chunks.append(rest)
            break
        window = rest[:max_chars]
        cut = window.rfind("\n")
        if cut <= 0:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = max_chars
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()

    return chunks


def limited_lines(lines: list[str], max_bytes: int, overflow_note: str = "…and {left} more.") -> str:
    """`lines` joined together, cut short once they would pass `max_bytes` (UTF-8): the text then ends with
    `overflow_note`, where `{left}` is how many lines were left off. Each line carries its own separator."""
    text = ""
    for shown, line in enumerate(lines):
        if utf8_length(text) + utf8_length(line) > max_bytes:
            return text + overflow_note.format(left=len(lines) - shown)
        text += line
    return text
