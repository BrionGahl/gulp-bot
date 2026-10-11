use std::env;

use tracing_subscriber::filter::LevelFilter;

/// Process-wide configuration from the environment. Per-server values (role, channel and user
/// IDs, the Bart / WowUtils / Warcraft Logs credentials, the raid notes schedule) are not here: administrators register those per
/// guild, see `guild_settings.rs`.
#[derive(Debug)]
pub struct Config {
    pub discord_token: String,
    pub bot_name: String,
    /// Postgres connection URL for the database holding per-guild settings.
    pub database_url: String,
    pub log_level: LevelFilter,
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
        }
    }
}