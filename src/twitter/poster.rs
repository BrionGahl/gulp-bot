use std::sync::Arc;
use poise::serenity_prelude::{ChannelId, Http, CreateMessage};
use crate::twitter::feed::Tweet;

pub async fn post_tweet(
    http: &Arc<Http>,
    channel_id: u64,
    tweet: &Tweet,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    ChannelId::new(channel_id)
        .send_message(http.as_ref(), CreateMessage::new().content(&tweet.url))
        .await?;

    Ok(())
}
