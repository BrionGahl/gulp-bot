"""Who may use the AI assistant, who counts as an Officer and who as a Developer, and whether the
developers' switches let them through. No Discord in here; roles are their numeric IDs."""

from __future__ import annotations

from collections.abc import Sequence

from ..exception.ai_exception import Disabled, Maintenance
from ..schema.ai_schema import Access
from ..schema.guild_settings_schema import AiSettings, GuildSettings


def access_from_roles(
    member_roles: Sequence[int],
    raider_role: int | None,
    officer_role: int | None,
    developer_role: int | None,
    is_administrator: bool,
) -> Access:
    """The access of a member holding `member_roles`, given the roles the server registered with
    `/settings`.

    A Developer is a holder of the registered developer role, and nobody else: not even an administrator is
    one, so that developer powers are only ever given on purpose. An Officer is a holder of the registered
    moderator role or a server administrator, the same two the bot treats alike everywhere else
    (`/settings`, `/wcl`). A role that isn't registered grants nothing, so a server that hasn't set up its
    roles gets no assistant for its members rather than one open to everybody; administrators are still
    Officers there, so that somebody can always use it.
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


def access_for_member(settings: GuildSettings, roles: Sequence[int], is_administrator: bool) -> Access:
    """What the member holding `roles` may do with the assistant, by the roles this server registered: the
    raider role, the moderator role as the Officer role, and the developer role."""
    return access_from_roles(roles, settings.raider_role_id, settings.mod_role_id, settings.dev_role_id, is_administrator)


def ensure_switches_allow(settings: AiSettings, access: Access) -> None:
    """Whether the developers' switches let a member with `access` talk to the assistant: not while it is
    off, and while it is in maintenance only if the member is an Officer (a Developer is one). Raises the
    `AskError` that says why not."""
    if settings.disabled:
        raise Disabled()
    if settings.maintenance and not access.is_officer():
        raise Maintenance()
