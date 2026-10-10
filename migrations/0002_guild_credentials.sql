-- Per-guild third-party credentials, so a server only ever uses (and is shown) its own.
ALTER TABLE guild_settings
    ADD COLUMN bart_token        TEXT,
    ADD COLUMN wowutils_token    TEXT,
    ADD COLUMN wowutils_group_id TEXT;
