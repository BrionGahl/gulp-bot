-- Players a guild's officers leave out of the Warcraft Logs overview, see `wcl_ignored_players.rs`.
-- The name is the character name as typed; names are compared ignoring case by the bot, which
-- rejects a name that is already there in another case.
CREATE TABLE wcl_ignored_players (
    guild_id BIGINT NOT NULL,
    name     TEXT NOT NULL,
    PRIMARY KEY (guild_id, name)
);
