use std::env;
use std::fmt;

use tracing_subscriber::filter::LevelFilter;

/// Process-wide configuration from the environment. Per-server values (role, channel and user
/// IDs, the Bart / WowUtils / Warcraft Logs credentials, the raid notes schedule) are not here: administrators register those per
/// guild, see `guild_settings.rs`.
pub struct Config {
    pub discord_token: String,
    pub bot_name: String,
    /// Postgres connection URL for the database holding per-guild settings.
    pub database_url: String,
    pub log_level: LevelFilter,
    /// Anthropic API key for the AI assistant (`/ask`, "Bot please"). Without it the assistant is off.
    pub anthropic_api_key: Option<String>,
    /// The Claude model that answers. Default: `claude-sonnet-5-5`.
    pub anthropic_model: String,
    /// How much effort the model puts into an answer (`output_config.effort`): `low`, `medium`, `high`...
    /// Default `low`, which is quick and cheap for chat; `off` doesn't send the setting at all.
    pub anthropic_effort: Option<String>,
    /// The most questions the assistant answers in 24 hours, from everybody together: what keeps the
    /// bill from running away. Default 100.
    pub anthropic_daily_limit: usize,
}

// By hand, so that the tokens and the database password in the URL can't end up in a log.
impl fmt::Debug for Config {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Config")
            .field("bot_name", &self.bot_name)
            .field("log_level", &self.log_level)
            .field("anthropic_model", &self.anthropic_model)
            .field("anthropic_effort", &self.anthropic_effort)
            .field("anthropic_daily_limit", &self.anthropic_daily_limit)
            .finish_non_exhaustive()
    }
}

impl Config {
    pub fn from_env() -> Self {
        Self {
            discord_token: env::var("DISCORD_TOKEN")
                .expect("Missing `DISCORD_TOKEN` env variable."),
            bot_name: env::var("BOT_NAME")
                .unwrap_or("gulp-bot".to_string()),
            database_url: env::var("DATABASE_URL")
                .expect("Missing `DATABASE_URL` env variable."),
            log_level: env::var("LOG_LEVEL")
                .unwrap_or_else(|_| "INFO".to_string())
                .parse::<LevelFilter>()
                .expect("Failed to parse `LOG_LEVEL` env variable. Valid values: TRACE, DEBUG, INFO, WARN, ERROR"),
            anthropic_api_key: optional_env("ANTHROPIC_API_KEY"),
            anthropic_model: optional_env("ANTHROPIC_MODEL").unwrap_or_else(|| "claude-sonnet-5-5".to_string()),
            anthropic_effort: match optional_env("ANTHROPIC_EFFORT") {
                None => Some("low".to_string()),
                Some(value) if value.eq_ignore_ascii_case("off") => None,
                Some(value) => Some(value.to_lowercase()),
            },
            anthropic_daily_limit: optional_env("ANTHROPIC_DAILY_LIMIT")
                .map(|value| value.parse().expect("Failed to parse `ANTHROPIC_DAILY_LIMIT` env variable. It must be a whole number."))
                .unwrap_or(100),
        }
    }
}

/// An environment variable that may be missing or empty (docker compose passes unset ones on as
/// empty), either of which means "not set".
fn optional_env(name: &str) -> Option<String> {
    env::var(name).ok().map(|value| value.trim().to_string()).filter(|value| !value.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn debug_output_never_holds_a_secret() {
        let config = Config {
            discord_token: "discord-secret-token".to_string(),
            bot_name: "gulp-bot".to_string(),
            database_url: "postgres://gulp:db-secret-password@db:5432/gulp".to_string(),
            log_level: LevelFilter::INFO,
            anthropic_api_key: Some("anthropic-secret-key".to_string()),
            anthropic_model: "claude-sonnet-5-5".to_string(),
            anthropic_effort: Some("low".to_string()),
            anthropic_daily_limit: 100,
        };

        let shown = format!("{config:?}");
        assert!(shown.contains("gulp-bot"));
        assert!(shown.contains("claude-sonnet-5-5"));
        for secret in ["discord-secret-token", "db-secret-password", "anthropic-secret-key"] {
            assert!(!shown.contains(secret), "{secret} leaked into {shown}");
        }
    }
}
