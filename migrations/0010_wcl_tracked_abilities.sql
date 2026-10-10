-- Abilities (mechanics) a guild's officers track in the Warcraft Logs damage taken overview, see
-- `wcl_mechanics.rs`. The name is what the overview shows; the ID is the game's ability ID.
CREATE TABLE wcl_tracked_abilities (
    guild_id   BIGINT NOT NULL,
    ability_id BIGINT NOT NULL,
    name       TEXT NOT NULL,
    PRIMARY KEY (guild_id, ability_id)
);

-- Names are unique within a guild, ignoring case, because officers pick abilities by name.
CREATE UNIQUE INDEX wcl_tracked_abilities_name_idx ON wcl_tracked_abilities (guild_id, lower(name));
