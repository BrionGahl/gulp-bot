"""Text helpers for the AI assistant. No Discord in here, so they can be tested without it."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

# Discord rejects messages longer than this many characters.
DISCORD_MAX_CHARS = 2000

# The most characters of a member's question that are sent to the model. Bounds the cost of one
# request: a long paste would otherwise be billed on every following turn too.
MAX_INPUT_CHARS = 1500

# The most characters of the message a member replied to that are sent along with their question.
MAX_QUOTE_CHARS = 500

# The longest name put in front of a member's message.
MAX_SPEAKER_CHARS = 40


def has_trigger(content: str) -> bool:
    """Whether the message calls the assistant: the words "bot please" in a row, in any case and with
    any punctuation between them ("Bot, please ...", "BOT PLEASE!"). "robot please" doesn't count."""
    return has_phrase(content, ["bot", "please"])


def has_get_them(content: str) -> bool:
    """Whether the message is the "bot get them" command: those words in a row, like `has_trigger`."""
    return has_phrase(content, ["bot", "get", "them"])


def _words(content: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    for char in content:
        if char.isalnum():
            current.append(char)
        elif current:
            words.append("".join(current).lower())
            current = []
    if current:
        words.append("".join(current).lower())
    return words


def has_phrase(content: str, phrase: Sequence[str]) -> bool:
    """Whether `content` has the lowercase words of `phrase` in a row, whole words only, in any case and
    with any punctuation or spacing between them."""
    if not phrase:
        return False
    words = _words(content)
    size = len(phrase)
    return any(words[start : start + size] == list(phrase) for start in range(len(words) - size + 1))


def truncate_chars(text: str, max_chars: int) -> str:
    """At most `max_chars` characters of `text`, trimmed."""
    text = text.strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def single_line(text: str) -> str:
    """`text` on one line: every run of whitespace, line breaks included, becomes one space. A member's
    question goes after their name and a colon, so line breaks are taken out; otherwise they could
    write lines of their own that look like another member speaking."""
    return " ".join(text.split())


def render_question(speaker: str, text: str, replying_to: tuple[str, str] | None) -> str:
    """A member's message as the model sees it: `speaker: text`, and, if they replied to a message, who
    wrote that one and what it said, as ` [replying to author: "quoted"]`. All of it on one line and
    cut to length, so that neither the question nor the quote can fake a line of its own or run up the
    cost. A quote with no text (an embed, an image) is left out."""
    line = f"{speaker}: {single_line(truncate_chars(text, MAX_INPUT_CHARS))}"
    if replying_to is not None:
        author, quoted = replying_to
        quoted = single_line(truncate_chars(quoted, MAX_QUOTE_CHARS))
        if quoted:
            line += f' [replying to {single_line(author)}: "{quoted}"]'
    return line


def strip_thinking(text: str) -> str:
    """`text` without the thinking a model wrote into it (`<think>...</think>`), trimmed. A model served
    locally may think aloud in its answer; that isn't meant for the channel. Thinking that never ends
    (the model ran out of tokens while thinking) takes the rest of the text with it."""
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


def split_message(text: str, max_chars: int) -> list[str]:
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


def speaker_label(display_name: str, username: str) -> str:
    """How a member is named in front of their message, so the model can tell speakers apart (and
    recognise the ones the system prompt talks about): their display name, with the account name after
    it if that differs. Line breaks are dropped so a name can't fake another line."""
    display_name = _clean_name(display_name)
    username = _clean_name(username)

    if not display_name:
        label = username
    elif not username or ascii_lower(display_name) == ascii_lower(username):
        label = display_name
    else:
        label = f"{display_name} ({username})"

    return truncate_chars(label, MAX_SPEAKER_CHARS) if label else "Someone"


def _clean_name(name: str) -> str:
    return "".join(" " if unicodedata.category(char) == "Cc" else char for char in name).strip()


def ascii_lower(text: str) -> str:
    """`text` with only its ASCII letters lowercased, for comparisons that ignore ASCII case alone."""
    return "".join(char.lower() if char.isascii() else char for char in text)
