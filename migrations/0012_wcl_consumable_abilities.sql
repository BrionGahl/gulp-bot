-- Abilities that count as a healthstone or a health potion in the Warcraft Logs consumables
-- overview, see `wcl_consumables.rs`. A category can have several IDs (a plain and a demonic
-- healthstone, two potions), and an ability belongs to one category.
CREATE TABLE wcl_consumable_abilities (
    guild_id   BIGINT NOT NULL,
    ability_id BIGINT NOT NULL,
    -- 'healthstone' or 'health_potion'
    kind       TEXT NOT NULL,
    PRIMARY KEY (guild_id, ability_id)
);
