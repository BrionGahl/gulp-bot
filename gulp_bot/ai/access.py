"""Who may use the assistant, who counts as an Officer and who as a Developer. No Discord in here, so
it can be tested without it; roles are their numeric IDs."""

from __future__ import annotations

import enum
from collections.abc import Sequence


class Access(enum.Enum):
    """What a member may do with the assistant."""

    # Not a raider: the assistant doesn't answer.
    DENIED = 0
    # A raider: may talk to the assistant.
    RAIDER = 1
    # An Officer: may talk to it too, and may ask it to change things.
    OFFICER = 2
    # A Developer: everything an Officer may, and the only one who may run the `/ai` commands that
    # switch the assistant off, change its model, reset limits and so on.
    DEVELOPER = 3

    def is_allowed(self) -> bool:
        return self is not Access.DENIED

    def is_officer(self) -> bool:
        """Whether the member has the standing of an Officer, which a Developer has too."""
        return self in (Access.OFFICER, Access.DEVELOPER)

    def level(self) -> int:
        """Higher is more trusted: what is said to a member is shown only to members of this level or
        higher (see `history.History`)."""
        return self.value


def access(
    member_roles: Sequence[int],
    raider_role: int | None,
    officer_role: int | None,
    developer_role: int | None,
    is_administrator: bool,
) -> Access:
    """The access of a member holding `member_roles`, given the roles the server registered with
    `/settings`.

    A Developer is a holder of the registered developer role, and nobody else: not even an
    administrator is one, so that developer powers are only ever given on purpose. An Officer is a
    holder of the registered moderator role or a server administrator, the same two the bot treats
    alike everywhere else (`/settings`, `/wcl`). A role that isn't registered grants nothing, so a
    server that hasn't set up its roles gets no assistant for its members rather than one open to
    everybody; administrators are still Officers there, so that somebody can always use it.
    """

    def holds(role: int | None) -> bool:
        return role is not None and role in member_roles

    if holds(developer_role):
        return Access.DEVELOPER
    if is_administrator or holds(officer_role):
        return Access.OFFICER
    if holds(raider_role):
        return Access.RAIDER
    return Access.DENIED
