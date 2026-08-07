use std::collections::HashSet;
use std::env;

use poise::serenity_prelude::{ChannelId, RoleId, UserId};
use tracing_subscriber::filter::LevelFilter;

#[derive(Debug)]
pub struct Config {
    pub discord_token: String,
    pub wow_audit_token: String,
    pub bot_name: String,
    pub mod_role_id: RoleId,
    pub raider_role_id: RoleId,
    pub bart_token: String,
    pub clips_channel_ids: HashSet<ChannelId>,
    pub log_level: LevelFilter,
}

impl Config {
    pub fn from_env() -> Self {
        Self {
            discord_token: env::var("DISCORD_TOKEN")
                .expect("Missing `DISCORD_TOKEN` env variable."),
            wow_audit_token: env::var("WOWAUDIT_TOKEN")
                .expect("Missing `WOWAUDIT_TOKEN` env variable."),
            bot_name: env::var("BOT_NAME")
                .unwrap_or("gulp-bot".to_string()),
            mod_role_id: RoleId::from(env::var("MOD_ROLE_ID")
                .expect("Missing `MOD_ROLE_ID` env variable.")
                .parse::<u64>()
                .expect("Failed to parse `MOD_ROLE_ID env variable")),
            raider_role_id: RoleId::from(env::var("RAIDER_ROLE_ID")
                .expect("Missing `RAIDER_ROLE_ID` env variable.")
                .parse::<u64>()
                .expect("Failed to parse `RAIDER_ROLE_ID env variable.")),
            bart_token: env::var("BART_TOKEN")
                .unwrap_or("".to_string()),
            clips_channel_ids: env::var("CLIPS_CHANNEL_IDS")
                .unwrap_or_default()
                .split(',')
                .filter(|s| !s.is_empty())
                .map(|s| ChannelId::from(s.trim().parse::<u64>()
                    .expect("Failed to parse `CLIPS_CHANNEL_IDS` env variable, expected a comma-separated list of channel IDs.")))
                .collect(),
            log_level: env::var("LOG_LEVEL")
                .unwrap_or_else(|_| "INFO".to_string())
                .parse::<LevelFilter>()
                .expect("Failed to parse `LOG_LEVEL` env variable. Valid values: TRACE, DEBUG, INFO, WARN, ERROR"),
        }
    }
}