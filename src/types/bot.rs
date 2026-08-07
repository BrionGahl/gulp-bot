use crate::config::Config;

#[derive(Debug)]
pub struct Data {
    pub config: Config,
}

impl Data {
    pub fn new() -> Self {
        let config = Config::from_env();

        Self {
            config,
        }
    }
}

pub type Error = Box<dyn std::error::Error + Send + Sync>;
pub type Context<'a> = poise::Context<'a, Data, Error>;
