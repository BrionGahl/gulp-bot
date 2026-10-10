-- A guild can now have any number of delete-and-replay users, so they move out of the single
-- `guild_settings.replay_user_id` column into their own table. Existing values are carried over.
CREATE TABLE guild_replay_users (
    guild_id BIGINT NOT NULL,
    user_id  BIGINT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

INSERT INTO guild_replay_users (guild_id, user_id)
SELECT guild_id, replay_user_id FROM guild_settings WHERE replay_user_id IS NOT NULL;

ALTER TABLE guild_settings DROP COLUMN replay_user_id;
