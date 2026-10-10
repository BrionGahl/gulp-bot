-- Announcements scheduled by a guild's officers. Each one carries its own channel, schedule
-- (quartz-style cron, evaluated in America/New_York) and message, so the single per-guild
-- announcement channel is no longer needed.
CREATE TABLE announcements (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    cron       TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX announcements_guild_id_idx ON announcements (guild_id);

ALTER TABLE guild_settings DROP COLUMN announcement_channel_id;
