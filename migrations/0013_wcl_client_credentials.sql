-- Per-guild Warcraft Logs API client (warcraftlogs.com, Manage Your Clients), replacing the bot-wide
-- WCL_CLIENT_ID / WCL_CLIENT_SECRET environment variables. Registered together, so in practice both
-- are set or neither is. NULL means not registered, which leaves `/wcl` off for that server.
ALTER TABLE guild_settings
    ADD COLUMN wcl_client_id     TEXT,
    ADD COLUMN wcl_client_secret TEXT;
