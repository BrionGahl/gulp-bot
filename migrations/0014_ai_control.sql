-- The developer role (above the moderator role for the AI assistant; only administrators register
-- it), and how the guild's developers have switched the AI assistant with `/ai`.
-- ai_disabled: the assistant answers nobody. ai_maintenance: it answers only officers and
-- developers. ai_model / ai_effort: used instead of ANTHROPIC_MODEL / ANTHROPIC_EFFORT when set
-- ('off' as the effort means none is sent). The defaults are the assistant on and unchanged.
ALTER TABLE guild_settings
    ADD COLUMN dev_role_id      BIGINT,
    ADD COLUMN ai_disabled      BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN ai_maintenance   BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN ai_model         TEXT,
    ADD COLUMN ai_effort        TEXT;
