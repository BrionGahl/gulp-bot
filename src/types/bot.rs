use std::sync::Arc;

use crate::config::Config;
use crate::guild_settings::GuildSettingsStore;
use crate::message_replay::MessageReplay;
use crate::warcraftlogs::WarcraftLogsClients;

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
    /// Warcraft Logs API clients, one per guild that has registered its client credentials.
    pub warcraft_logs: WarcraftLogsClients,
}

impl Data {
    pub fn new(config: Config, db: sqlx::PgPool, guild_settings: Arc<GuildSettingsStore>) -> Self {
        let http_client = reqwest::Client::new();

        let warcraft_logs = WarcraftLogsClients::new(http_client.clone());

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
