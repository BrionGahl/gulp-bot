-- The most recent raid-start attendance snapshot per guild, kept until the matching raid-end
-- snapshot so the two can be compared (who left early, who arrived late). In the database rather
-- than in memory so a bot restart mid-raid doesn't lose it.
CREATE TABLE raid_attendance_starts (
    guild_id         BIGINT PRIMARY KEY,
    voice_channel_id BIGINT NOT NULL,
    taken_at         TIMESTAMPTZ NOT NULL,
    user_ids         BIGINT[] NOT NULL
);
