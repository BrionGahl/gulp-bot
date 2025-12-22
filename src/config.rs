use std::env;

#[derive(Debug)]
pub struct Config {
    pub discord_token: String,
    pub wow_audit_token: String,
    pub bot_name: String,
}

impl Config {
    pub fn from_env() -> Self {
        Self {
            discord_token: env::var("DISCORD_TOKEN").expect("Missing `DISCORD_TOKEN` env variable."),
            wow_audit_token: env::var("WOWAUDIT_TOKEN").expect("Missing `WOWAUDIT_TOKEN` env variable."),
            bot_name: env::var("BOT_NAME").unwrap_or(String::from("yuh-bot")),
        }
    }
}