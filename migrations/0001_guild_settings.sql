-- Per-guild IDs registered by server administrators through the `/settings` commands.
-- Discord snowflakes are unsigned 64-bit but never use the top bit, so they fit in BIGINT.
CREATE TABLE guild_settings (
    guild_id                     BIGINT PRIMARY KEY,
    mod_role_id                  BIGINT,
    raider_role_id               BIGINT,
    trial_role_id                BIGINT,
    personal_officer_category_id BIGINT,
    raid_notes_channel_id        BIGINT,
    announcement_channel_id      BIGINT,
    replay_user_id               BIGINT,
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE guild_clips_channels (
    guild_id   BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);
