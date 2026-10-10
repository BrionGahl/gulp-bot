-- Warcraft Logs, see `warcraftlogs.rs`. NULL means not registered.
-- The channel where the officers' Warcraft Logs results are posted.
ALTER TABLE guild_settings ADD COLUMN wcl_channel_id BIGINT;
-- Where the guild's reports are found: the Warcraft Logs guild they are uploaded to, and the user ID of
-- the account that uploads them. Registered together, so in practice both are set or neither is.
ALTER TABLE guild_settings ADD COLUMN wcl_guild_id BIGINT;
ALTER TABLE guild_settings ADD COLUMN wcl_uploader_id BIGINT;
