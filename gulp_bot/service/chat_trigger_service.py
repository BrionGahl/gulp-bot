"""The phrases members use to call on the bot in a channel: "Bot please" for the AI assistant, and "bot get
them" to have a message deleted. No Discord in here."""

from __future__ import annotations

from collections.abc import Sequence


def mentions_bot_please(content: str) -> bool:
    """Whether the message calls the assistant: the words "bot please" in a row, in any case and with any
    punctuation between them ("Bot, please ...", "BOT PLEASE!"). "robot please" doesn't count."""
    return has_phrase(content, ["bot", "please"])


def mentions_bot_get_them(content: str) -> bool:
    """Whether the message is the "bot get them" command: those words in a row, like `mentions_bot_please`."""
    return has_phrase(content, ["bot", "get", "them"])


def has_phrase(content: str, phrase: Sequence[str]) -> bool:
    """Whether `content` has the lowercase words of `phrase` in a row, whole words only, in any case and
    with any punctuation or spacing between them."""
    if not phrase:
        return False
    words = _words(content)
    size = len(phrase)
    return any(words[start : start + size] == list(phrase) for start in range(len(words) - size + 1))


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
