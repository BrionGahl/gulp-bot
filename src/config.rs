use std::env;

use poise::serenity_prelude::{RoleId, UserId};
use tracing_subscriber::filter::LevelFilter;

#[derive(Debug)]
pub struct Config {
    pub discord_token: String,
    pub wow_audit_token: String,
    pub bot_name: String,
    pub mod_role_id: RoleId,
    pub raider_role_id: RoleId,
    pub bart_token: String,
    pub nitter_base_url: String,
    pub x_base_url: String,
    pub twitter_user_ids: Vec<String>,
    pub tweet_target_channel_id: u64,
    pub tweet_poll_time: u64,
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
            nitter_base_url: env::var("NITTER_BASE_URL")
                .unwrap_or_else(|_| "https://nitter.net".to_owned()),
            x_base_url: env::var("X_BASE_URL")
                .unwrap_or_else(|_| "https://x.com".to_string()),
            twitter_user_ids: env::var("TWITTER_USER_IDS")
                .unwrap_or_default()
                .split(',')
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .collect(),
            tweet_target_channel_id: env::var("TWEET_CHANNEL_ID")
                .expect("Missing `TWEET_CHANNEL_ID` env variable.")
                .parse::<u64>()
                .expect("Failed to parse `TWEET_CHANNEL_ID env variable."),
            tweet_poll_time: env::var("TWEET_POLL_TIME")
                .expect("Missing `TWEET_POLL_TIME` env variable.")
                .parse::<u64>()
                .expect("Failed to parse `TWEET_POLL_TIME env variable."),
            log_level: env::var("LOG_LEVEL")
                .unwrap_or_else(|_| "INFO".to_string())
                .parse::<LevelFilter>()
                .expect("Failed to parse `LOG_LEVEL` env variable. Valid values: TRACE, DEBUG, INFO, WARN, ERROR"),
        }
    }
}