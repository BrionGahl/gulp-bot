use std::env;

use poise::serenity_prelude::{RoleId, UserId};

#[derive(Debug)]
pub struct Config {
    pub discord_token: String,
    pub wow_audit_token: String,
    pub bot_name: String,
    pub mod_role_id: RoleId,
    pub raider_role_id: RoleId,
    pub twitter_token: String,
    pub twitter_user_ids: Vec<String>,
    pub tweet_target_channel_id: u64,
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
            twitter_token: env::var("TWITTER_TOKEN")
                .expect("Missing `TWITTER_TOKEN` env variable."),
            twitter_user_ids: env::var("TWITTER_USER_IDS")
                .unwrap_or_default()
                .split(',')
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .collect(),
            tweet_target_channel_id: env::var("TWEET_CHANNEL_ID")
                .expect("Missing `TWEET_CHANNEL_ID` env variable.")
                .parse::<u64>()
                .expect("Failed to parse `TWEET_CHANNEL_ID env variable.")
        }
    }
}