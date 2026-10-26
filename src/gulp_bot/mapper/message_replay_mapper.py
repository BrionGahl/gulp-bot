"""A deleted message into the embed it is reposted as."""

from __future__ import annotations

from typing import Any

from ..core.embed import MAX_DESCRIPTION_CHARS, Embed, new_embed
from ..core.text import truncate_with_ellipsis
from ..schema.message_replay_schema import StoredMessage


def replay_embed(stored: StoredMessage) -> Embed:
    """The embed a deleted message is reposted as. One image attachment is shown inline; everything else
    (non-images, and any extra images) is listed as links under the text."""
    inline_image = next((index for index, attachment in enumerate(stored.attachments) if attachment.is_image), None)
    links = [attachment.url for index, attachment in enumerate(stored.attachments) if index != inline_image]

    description = stored.content
    if links:
        if description:
            description += "\n\n"
        description += "\n".join(links)
    description = truncate_with_ellipsis(description, MAX_DESCRIPTION_CHARS)

    author: dict[str, Any] = {"name": f"{stored.author_name} deleted a message"}
    if stored.author_avatar_url is not None:
        author["icon_url"] = stored.author_avatar_url

    embed = new_embed(description=description or None, timestamp=stored.timestamp.isoformat())
    embed["author"] = author
    if inline_image is not None:
        embed["image"] = {"url": stored.attachments[inline_image].url}
    return embed
