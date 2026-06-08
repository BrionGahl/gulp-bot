use std::sync::Arc;
use poise::serenity_prelude::{ChannelId, Http, CreateEmbed, CreateMessage};
use crate::twitter::feed::Tweet;

pub async fn post_tweet(
    http: &Arc<Http>,
    channel_id: u64,
    tweet: &Tweet,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let mut embed = CreateEmbed::new()
        .title(format!("@{} tweeted!", tweet.author))
        .description(&tweet.content)
        .url(&tweet.url);

    if let Some(img) = &tweet.image_url {
        embed = embed.image(img);
    }

    ChannelId::new(channel_id)
        .send_message(http.as_ref(), CreateMessage::new().embed(embed))
        .await?;

    Ok(())
}
