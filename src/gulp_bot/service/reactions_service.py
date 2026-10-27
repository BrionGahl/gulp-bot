"""The bot's spontaneous reactions to messages: in-jokes of the guild, each a `Reaction` in `REACTIONS`.

A reaction is set off by who sent a message (`author`) or by a word in it (`word`), and can be held back by
chance (`chance`), by a cooldown after it last went off in the server (`cooldown_secs`), or by the author
having spoken in the server too recently (`away_secs`). When it goes off, the bot says one of its
`responses`, picked at random, in the same channel: as a plain message, or as a reply (`reply`). Either way
it pings nobody.

Only messages in the channels of `REACTION_CHANNELS` (or in their threads) reach this service, and not chat
with the AI assistant or messages in a clips channel (`message_event_resource`). Anything else sets nothing
off, counts towards no cooldown, and doesn't count as the author having spoken. Two limits hold over all of them:
- at most one reaction to a message: the first in `REACTIONS` that goes off wins;
- at most one reaction every `MIN_GAP_SECS` in a server, whatever set it off, except for a reaction with
  `ignores_gap`, which goes off regardless and doesn't start the gap.

No Discord in here; times are `time.monotonic()` seconds. Everything is kept in memory only: after a
restart every clock starts afresh, so the first match is answered (and someone the bot hasn't seen yet counts
as away).
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

HOUR = 60 * 60

# The least time between two reactions in one server: 10 minutes.
MIN_GAP_SECS = 10 * 60

# The only channels the bot reacts in, by name. A thread counts as the channel it is in.
REACTION_CHANNELS = frozenset({"guild-chat", "mythic-chat"})


def reacts_in(channel_name: str | None) -> bool:
    """Whether the bot reacts to messages in the channel named `channel_name`; never in one with no name."""
    return channel_name is not None and channel_name.lower() in REACTION_CHANNELS


@dataclass(frozen=True)
class Reaction:
    # A short name, for the logs.
    name: str
    responses: tuple[str, ...]
    # Set off by a member with this as their username, global name or server nickname, in any case.
    author: str | None = None
    # With `author`: only the account's Discord username counts, not its global name or server nickname.
    username_only: bool = False
    # Set off by a message with this in it, anywhere and in any case.
    word: str | None = None
    # The chance it goes off when it is set off.
    chance: float = 1.0
    # How long after it went off in a server it stays quiet there.
    cooldown_secs: float = 0.0
    # Only when the author has said nothing in the server for this long, or nothing the bot has seen.
    away_secs: float | None = None
    # Whether the bot replies to the message rather than just saying something in the channel.
    reply: bool = False
    # Whether it goes off even within `MIN_GAP_SECS` of another reaction, and without starting that gap.
    ignores_gap: bool = False


REACTIONS = [
    Reaction(
        name="tritonian_welcome_back",
        author="Tritonian",
        away_secs=8 * HOUR,
        ignores_gap=True,
        responses=(
            "Welcome back, hairless one! Missed you :3",
            "Heeeeeeeello my favorite baldie",
            "Trit!!!! How was the pushing yesterday, furless one?",
            "Haaaaaaaaai Trit! if it ain't the chrome dome himself!",
            "*Holds the giggle* hii Trit :)",
            "Welcome back Trit! Your head is still the best mirror in Azeroth",
            "Trit!!!! How many depleted keys did we bring back today?",
            "*Shields eyes* oh it's just Trit, thought the sun came in",
            "Hiiii Trit :) still the most aerodynamic tank in the guild",
            "Heeeello Trit! Wind resistance: zero. Key timer: crushed",
            "*Polishes the welcome mat and Trit's head* hiii!",
            "Heeey Trit! Shampoo budget still at zero I see",
            "Trit! Every guild needs a north star, ours just happens to be your head",
            "*Adjusts sunglasses* sup Trit",
            "*Plays dramatic entrance music* the Trit has arrived",
            "Trit is here! Who needs a world buff when we have the dome",
            "Trit returns! The guild's MVP: Most Visible Pate",
            "*Puts on welding goggles* okay, hi Trit",
        ),
    ),
    Reaction(
        name="onlydam",
        word="Onlydam",
        cooldown_secs=24 * HOUR,
        responses=("I miss him :(", "I miss the jaguar warrior"),
    ),
    Reaction(
        name="yuki",
        word="Yuki",
        cooldown_secs=24 * HOUR,
        responses=("Bring him back!!!!",),
    ),
    Reaction(
        name="nartana",
        word="Nartana",
        cooldown_secs=24 * HOUR,
        responses=("*Giggles*",),
    ),
    Reaction(
        name="sono",
        word="Sono",
        cooldown_secs=24 * HOUR,
        reply=True,
        responses=("the goat",),
    ),
    Reaction(
        name="getcha",
        word="Getcha",
        chance=0.25,
        reply=True,
        responses=("Did you mean Bitcha?",),
    ),
    Reaction(
        name="horsefly",
        author="Kailanton",
        username_only=True,
        chance=0.02,
        responses=("thats right horsefly",),
    ),
]


@dataclass(frozen=True)
class Response:
    """What the bot says to a message, and whether as a reply to it."""

    reaction: str
    text: str
    reply: bool


def _same_name(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def sets_off(reaction: Reaction, names: Sequence[str | None], content: str) -> bool:
    """Whether a message by a member with `names` (username, then global name and nickname) and `content`
    sets `reaction` off, before chance, cooldowns and limits."""
    if reaction.author is not None:
        candidates = names[:1] if reaction.username_only else names
        if not any(name is not None and _same_name(name, reaction.author) for name in candidates):
            return False
    return reaction.word is None or reaction.word.lower() in content.lower()


class ReactionsService:
    def __init__(self, reactions: Sequence[Reaction] = REACTIONS, min_gap_secs: float = MIN_GAP_SECS, rng: random.Random | None = None):
        self._reactions = list(reactions)
        self._min_gap_secs = min_gap_secs
        self._rng = rng or random.Random()
        # When the bot last reacted, per server.
        self._last_reaction: dict[int, float] = {}
        # When each reaction last went off, per server and reaction.
        self._last_fired: dict[tuple[int, str], float] = {}
        # When a member last spoke, per server, member and reaction that cares (`away_secs`).
        self._last_seen: dict[tuple[int, int, str], float] = {}

    def response_for(self, guild_id: int, author_id: int, names: Iterable[str | None], content: str, now: float) -> Response | None:
        """What the bot says to a message in `guild_id` by `author_id`, who goes by `names`, with `content`,
        sent at `now`: the first reaction that goes off, if the server hasn't had one in `min_gap_secs` (or the
        reaction ignores that gap); else nothing."""
        names = list(names)
        set_off = [reaction for reaction in self._reactions if sets_off(reaction, names, content)]

        # Who has been away is noted on every message of theirs, whether or not the bot reacts to it.
        back = {reaction.name for reaction in set_off if reaction.away_secs is not None and self._note_seen(guild_id, author_id, reaction, now)}

        last = self._last_reaction.get(guild_id)
        in_gap = last is not None and now - last < self._min_gap_secs
        for reaction in set_off:
            if in_gap and not reaction.ignores_gap:
                continue
            if reaction.away_secs is not None and reaction.name not in back:
                continue
            fired = self._last_fired.get((guild_id, reaction.name))
            if fired is not None and now - fired < reaction.cooldown_secs:
                continue
            if self._rng.random() >= reaction.chance:
                continue
            if not reaction.ignores_gap:
                self._last_reaction[guild_id] = now
            self._last_fired[(guild_id, reaction.name)] = now
            return Response(reaction.name, self._rng.choice(reaction.responses), reaction.reply)
        return None

    def _note_seen(self, guild_id: int, author_id: int, reaction: Reaction, now: float) -> bool:
        """Notes that the author spoke at `now`; returns whether they had been away for `away_secs` (or the
        bot hadn't seen them yet)."""
        key = (guild_id, author_id, reaction.name)
        last = self._last_seen.get(key)
        self._last_seen[key] = now
        return last is None or reaction.away_secs is None or now - last >= reaction.away_secs


# --- For COMMANDS.md ---


def _duration(seconds: float) -> str:
    if seconds % HOUR == 0:
        hours = int(seconds // HOUR)
        return "1 hour" if hours == 1 else f"{hours} hours"
    minutes = int(seconds // 60)
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def describe(reaction: Reaction) -> tuple[str, str, str]:
    """A reaction as a row of the table of things that happen without a command: the message, what the bot
    does, and who sets it off."""
    if reaction.author is not None:
        message = f"A message from **{reaction.author}**"
        if reaction.word is not None:
            message += f' with **"{reaction.word}"** in it (any case)'
        if reaction.away_secs is not None:
            message += f" after {_duration(reaction.away_secs)} without one in those channels"
        who = f"{reaction.author} ({'Discord username only' if reaction.username_only else 'username, global name or nickname'})"
    else:
        message = f'A message with **"{reaction.word}"** in it (any case)'
        who = "Everyone"

    if len(reaction.responses) <= 2:
        said = " or ".join(f'"{response}"' for response in reaction.responses)
    else:
        said = f"one of {len(reaction.responses)} responses"
    if len(reaction.responses) > 1:
        said += ", at random"
    how = "a reply" if reaction.reply else "a message in the same channel"
    what = f"{how[0].upper()}{how[1:]}: {said}" if reaction.chance >= 1 else f"A {reaction.chance:.0%} chance of {how}: {said}"
    if reaction.cooldown_secs:
        what += f". At most once every {_duration(reaction.cooldown_secs)} per server"
    if reaction.ignores_gap:
        what += ". Not held back by, and doesn't count towards, the 10-minute limit below"
    return message, what, who


def limits_row(min_gap_secs: float = MIN_GAP_SECS) -> tuple[str, str, str]:
    """The row saying how the reactions are held back together."""
    return (
        "Any of the reactions above",
        f"At most one reaction to a message (the first in this table wins), and at most one every {_duration(min_gap_secs)} per server "
        "(except those that say otherwise). "
        f"Only in {' and '.join(f'#{name}' for name in sorted(REACTION_CHANNELS))} and their threads. "
        'Chat with the AI assistant ("Bot please", "hey bot" and replies to its answers) and messages in clips channels set nothing off and count towards no cooldown. '
        "Nobody is pinged, not even by a reply. Kept in memory only: after the bot restarts, the first match is answered",
        "As the reaction",
    )


def reaction_rows(reactions: Sequence[Reaction] = REACTIONS) -> str:
    """The rows of the reactions and their limits, for the table in COMMANDS.md."""
    rows = [describe(reaction) for reaction in reactions] + [limits_row()]
    return "".join(f"| {message} | {what} | {who} |\n" for message, what, who in rows)
