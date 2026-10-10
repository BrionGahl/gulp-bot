-- Per-guild schedule for the raid notes post, as a quartz-style cron expression. NULL means the
-- bot's built-in default.
ALTER TABLE guild_settings ADD COLUMN raid_notes_cron TEXT;
