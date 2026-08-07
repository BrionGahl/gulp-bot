use crate::config::Config;

#[derive(Debug)]
pub struct Data {
    pub config: Config,
    pub http_client: reqwest::Client,
}

impl Data {
    pub fn new() -> Self {
        let config = Config::from_env();
        let http_client = reqwest::Client::new();

        Self {
            config,
            http_client,
        }
    }
}

pub type Error = Box<dyn std::error::Error + Send + Sync>;
pub type Context<'a> = poise::Context<'a, Data, Error>;
