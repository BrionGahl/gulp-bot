-- Absences members report for raid nights, with `/absence` or in their personal officer channel. A single
-- day has start_date = end_date. kind is 'absent' or 'tentative' (late, or not sure). source_message_id
-- is the message in a personal officer channel it was read from, NULL for one added with `/absence add`.
-- Absences that ended before the week the raid notes post is for are deleted when it is made.
CREATE TABLE raid_absences (
    id                BIGSERIAL PRIMARY KEY,
    guild_id          BIGINT NOT NULL,
    user_id           BIGINT NOT NULL,
    start_date        DATE NOT NULL,
    end_date          DATE NOT NULL,
    kind              TEXT NOT NULL,
    note              TEXT,
    source_message_id BIGINT,
    created_by        BIGINT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (start_date <= end_date)
);
CREATE INDEX raid_absences_guild_end ON raid_absences (guild_id, end_date);
CREATE INDEX raid_absences_source ON raid_absences (source_message_id);

-- What else a member raised in their personal officer channel, sorted by the model for the officers:
-- category is 'role_change', 'loot_roster' or 'feedback'. Shown in the officer digest of the week it was
-- written in, and deleted when the next week's raid notes post is made.
CREATE TABLE officer_channel_notes (
    id                BIGSERIAL PRIMARY KEY,
    guild_id          BIGINT NOT NULL,
    user_id           BIGINT NOT NULL,
    channel_id        BIGINT NOT NULL,
    source_message_id BIGINT NOT NULL,
    category          TEXT NOT NULL,
    summary           TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX officer_channel_notes_guild_created ON officer_channel_notes (guild_id, created_at);
CREATE INDEX officer_channel_notes_source ON officer_channel_notes (source_message_id);

-- The officer digest (absences and personal channel notes) the bot posted in each week's thread, edited as
-- they change. NULL until it is posted.
ALTER TABLE raid_notes_posts ADD COLUMN digest_message_id BIGINT;
