-- Enemies a guild's officers track in `/wcl damage_window`, by the name Warcraft Logs gives them. Every
-- enemy of a report with that name counts as one: the adds of a pull share a name.
CREATE TABLE wcl_tracked_enemies (
    guild_id BIGINT NOT NULL,
    name     TEXT NOT NULL,
    PRIMARY KEY (guild_id, name)
);

-- Names are unique within a guild, ignoring case, because they are matched to a report's enemies that way.
CREATE UNIQUE INDEX wcl_tracked_enemies_name_idx ON wcl_tracked_enemies (guild_id, lower(name));
