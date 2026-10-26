"""The abilities that count as a healthstone or a health potion in the Warcraft Logs consumables overview."""

from __future__ import annotations

from ..repository.wcl_consumable_ability_repository import ConsumableAbilityRepository
from ..schema.wcl_settings_schema import ConsumableAbility, ConsumableKind, RegisterConsumableOutcome, RegisterConsumableResult

# The most abilities one server can register for a kind. A kind is a few IDs (a plain and a demonic
# healthstone, a potion and its stronger version), and each is a request to the API.
MAX_PER_KIND = 6


def ids_of_kind(registered: list[ConsumableAbility], kind: ConsumableKind) -> list[int]:
    """The IDs registered for one kind."""
    return [entry.ability_id for entry in registered if entry.kind is kind]


class ConsumableService:
    def __init__(self, repository: ConsumableAbilityRepository):
        self._repository = repository

    async def list_registered(self, guild_id: int) -> list[ConsumableAbility]:
        """Everything the guild has registered, healthstones first, each by ID."""
        return await self._repository.list_for_guild(guild_id)

    async def register(self, guild_id: int, kind: ConsumableKind, ability_id: int) -> RegisterConsumableResult:
        """Counts `ability_id` as a `kind`. An ability is one kind only."""
        registered = await self._repository.list_for_guild(guild_id)
        for entry in registered:
            if entry.ability_id == ability_id:
                if entry.kind is kind:
                    return RegisterConsumableResult(RegisterConsumableOutcome.ALREADY_REGISTERED)
                return RegisterConsumableResult(RegisterConsumableOutcome.OTHER_KIND, entry.kind)
        if len(ids_of_kind(registered, kind)) >= MAX_PER_KIND:
            return RegisterConsumableResult(RegisterConsumableOutcome.FULL)

        await self._repository.insert(guild_id, ConsumableAbility(kind, ability_id))
        return RegisterConsumableResult(RegisterConsumableOutcome.ADDED)

    async def unregister(self, guild_id: int, ability_id: int) -> ConsumableKind | None:
        """Stops counting `ability_id`, returning what kind it was, or `None` if it wasn't registered."""
        return await self._repository.delete(guild_id, ability_id)
