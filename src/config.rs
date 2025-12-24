use std::env;

use poise::serenity_prelude::{RoleId};

#[derive(Debug)]
pub struct Config {
    pub discord_token: String,
    pub wow_audit_token: String,
    pub bot_name: String,
    pub mod_role_id: RoleId,
    pub bart_token: String,
}

impl Config {
    pub fn from_env() -> Self {
        Self {
            discord_token: env::var("DISCORD_TOKEN")
                .expect("Missing `DISCORD_TOKEN` env variable."),
            wow_audit_token: env::var("WOWAUDIT_TOKEN")
                .expect("Missing `WOWAUDIT_TOKEN` env variable."),
            bot_name: env::var("BOT_NAME")
                .unwrap_or(String::from("yuh-bot")),
            mod_role_id: RoleId::from(env::var("MOD_ROLE_ID")
                .expect("Missing `MOD_ROLE_ID` env variable.")
                .parse::<u64>()
                .expect("Failed to parse `MOD_ROLE_ID env variable")),
            bart_token: env::var("BART_TOKEN")
                .unwrap_or(String::from("")),
        }
    }
}