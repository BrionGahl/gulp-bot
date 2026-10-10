-- One row per member who has (or is being given) a personal officer channel in a guild. The
-- primary key is what guarantees a member never gets a second channel: the bot inserts the row
-- before creating the channel, and only creates it if the insert actually added a row.
-- `channel_id` is NULL only between that insert and the channel being created.
CREATE TABLE personal_officer_channels (
    guild_id   BIGINT NOT NULL,
    user_id    BIGINT NOT NULL,
    channel_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, user_id)
);
