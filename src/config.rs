use std::collections::HashSet;
use std::env;

use poise::serenity_prelude::{ChannelId, RoleId};
use tracing_subscriber::filter::LevelFilter;

#[derive(Debug)]
pub struct Config {
    pub discord_token: String,
    pub bot_name: String,
    pub mod_role_id: RoleId,
    pub raider_role_id: RoleId,
    pub trial_role_id: RoleId,
    pub personal_officer_category_id: ChannelId,
    pub bart_token: String,
    pub wowutils_token: String,
    pub wowutils_group_id: String,
    pub clips_channel_ids: HashSet<ChannelId>,
    pub raid_notes_channel_id: ChannelId,
    pub log_level: LevelFilter,
}

impl Config {
    pub fn from_env() -> Self {
        Self {
            discord_token: env::var("DISCORD_TOKEN")
                .expect("Missing `DISCORD_TOKEN` env variable."),
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
            trial_role_id: RoleId::from(env::var("TRIAL_ROLE_ID")
                .expect("Missing `TRIAL_ROLE_ID` env variable.")
                .parse::<u64>()
                .expect("Failed to parse `TRIAL_ROLE_ID env variable.")),
            personal_officer_category_id: ChannelId::from(env::var("PERSONAL_OFFICER_CATEGORY_ID")
                .expect("Missing `PERSONAL_OFFICER_CATEGORY_ID` env variable.")
                .parse::<u64>()
                .expect("Failed to parse `PERSONAL_OFFICER_CATEGORY_ID` env variable.")),
            bart_token: env::var("BART_TOKEN")
                .unwrap_or("".to_string()),
            wowutils_token: env::var("WOWUTILS_TOKEN")
                .expect("Missing `WOWUTILS_TOKEN` env variable."),
            wowutils_group_id: env::var("WOWUTILS_GROUP_ID")
                .expect("Missing `WOWUTILS_GROUP_ID` env variable."),
            clips_channel_ids: env::var("CLIPS_CHANNEL_IDS")
                .unwrap_or_default()
                .split(',')
                .filter(|s| !s.is_empty())
                .map(|s| ChannelId::from(s.trim().parse::<u64>()
                    .expect("Failed to parse `CLIPS_CHANNEL_IDS` env variable, expected a comma-separated list of channel IDs.")))
                .collect(),
            raid_notes_channel_id: ChannelId::from(env::var("RAID_NOTES_CHANNEL_ID")
                .expect("Missing `RAID_NOTES_CHANNEL_ID` env variable.")
                .parse::<u64>()
                .expect("Failed to parse `RAID_NOTES_CHANNEL_ID` env variable.")),
            log_level: env::var("LOG_LEVEL")
                .unwrap_or_else(|_| "INFO".to_string())
                .parse::<LevelFilter>()
                .expect("Failed to parse `LOG_LEVEL` env variable. Valid values: TRACE, DEBUG, INFO, WARN, ERROR"),
        }
    }
}