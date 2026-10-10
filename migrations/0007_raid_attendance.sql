-- Raid attendance: a guild's raid voice channel, plus the two quartz-style cron schedules (start
-- and end of raid) at which the bot records who is in it. Set and cleared together.
ALTER TABLE guild_settings
    ADD COLUMN raid_voice_channel_id BIGINT,
    ADD COLUMN raid_voice_start_cron TEXT,
    ADD COLUMN raid_voice_end_cron   TEXT;

-- The raid notes forum thread the bot created for each week, keyed by the Tuesday that week starts on. Lets
-- attendance be posted into the right week's thread, and stops a second thread being created
-- for a week that already has one.
CREATE TABLE raid_notes_posts (
    guild_id   BIGINT NOT NULL,
    week_start DATE NOT NULL,
    thread_id  BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, week_start)
);
