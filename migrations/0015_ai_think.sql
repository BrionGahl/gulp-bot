-- Whether a local model thinks before it answers, as the guild's developers set it with `/ai think`:
-- 'on', 'off', or 'default' (nothing is sent and the model decides). Used instead of LOCAL_MODEL_THINK
-- when set; the default is the configured one.
ALTER TABLE guild_settings
    ADD COLUMN ai_think TEXT;
