use std::sync::Arc;

use log::warn;

use crate::config::Config;
use crate::guild_settings::GuildSettingsStore;
use crate::message_replay::MessageReplay;
use crate::warcraftlogs::WarcraftLogs;

#[derive(Debug)]
pub struct Data {
    pub config: Config,
    /// Per-guild role/channel/user IDs registered by server administrators. Shared with the
    /// scheduled jobs, which run outside the framework and so can't reach `Data`.
    pub guild_settings: Arc<GuildSettingsStore>,
    /// Database pool, for the tables that are queried directly rather than cached in memory.
    pub db: sqlx::PgPool,
    pub http_client: reqwest::Client,
    /// Buffers each guild's replay users' recent messages so a deleted one can be reposted.
    pub message_replay: MessageReplay,
    /// Warcraft Logs API client; `None` when the bot has no client credentials, which turns the
    /// `/wcl` commands off.
    pub warcraft_logs: Option<WarcraftLogs>,
}

impl Data {
    pub fn new(config: Config, db: sqlx::PgPool, guild_settings: Arc<GuildSettingsStore>) -> Self {
        let http_client = reqwest::Client::new();

        let warcraft_logs = match (&config.wcl_client_id, &config.wcl_client_secret) {
            (Some(client_id), Some(client_secret)) => Some(WarcraftLogs::new(http_client.clone(), client_id.clone(), client_secret.clone())),
            (None, None) => None,
            _ => {
                warn!("Only one of `WCL_CLIENT_ID` and `WCL_CLIENT_SECRET` is set; Warcraft Logs stays off until both are.");
                None
            }
        };

        Self {
            config,
            guild_settings,
            db,
            http_client,
            message_replay: MessageReplay::new(),
            warcraft_logs,
        }
    }
}

pub type Error = Box<dyn std::error::Error + Send + Sync>;
pub type Context<'a> = poise::Context<'a, Data, Error>;
