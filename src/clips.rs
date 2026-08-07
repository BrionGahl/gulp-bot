use std::time::Duration;

use log::{error, info, warn};
use poise::serenity_prelude::{ChannelId, Context, CreateMessage, Message};

use crate::types::bot::Data;

// Discord unfurls link embeds (YouTube, Streamable, Twitch clips, etc.) asynchronously
// after the message is sent, so a message with no video yet isn't necessarily invalid.
const EMBED_GRACE_PERIOD: Duration = Duration::from_secs(6);

/// Deletes messages posted in a configured clips channel that don't contain a video,
/// giving Discord a moment to unfurl link embeds before making a final decision.
pub async fn enforce_clips_channel(ctx: &Context, data: &Data, message: &Message) {
    if message.author.bot {
        return;
    }
    if !data.config.clips_channel_ids.contains(&message.channel_id) {
        return;
    }
    if has_video(message) {
        return;
    }

    let ctx = ctx.clone();
    let channel_id = message.channel_id;
    let message_id = message.id;

    tokio::spawn(async move {
        tokio::time::sleep(EMBED_GRACE_PERIOD).await;

        let message = match channel_id.message(&ctx.http, message_id).await {
            Ok(message) => message,
            Err(_) => return, // Already deleted, or we lost access - nothing to do.
        };

        if has_video(&message) {
            return;
        }

        match message.delete(&ctx.http).await {
            Ok(()) => {
                info!(
                    "Deleted non-video message from {} in clips channel {}",
                    message.author.name, channel_id
                );
                notify_author(&ctx, &message, channel_id).await;
            }
            Err(e) => error!(
                "Failed to delete non-video message {} in clips channel {}: {}",
                message_id, channel_id, e
            ),
        }
    });
}

/// DMs the author to let them know why their message was removed.
async fn notify_author(ctx: &Context, message: &Message, channel_id: ChannelId) {
    let content = format!(
        "Your message in <#{channel_id}> was removed because it didn't include a video or clip. \
        That channel only allows messages with a video attachment or a video link (e.g. YouTube, Streamable, Twitch clip)."
    );

    if let Err(e) = message.author.dm(&ctx.http, CreateMessage::new().content(content)).await {
        warn!(
            "Failed to DM {} about their deleted message in {}: {}",
            message.author.name, channel_id, e
        );
    }
}

fn has_video(message: &Message) -> bool {
    message.attachments.iter().any(|attachment| {
        attachment.content_type.as_deref().is_some_and(|content_type| content_type.starts_with("video/"))
    }) || message.embeds.iter().any(|embed| embed.video.is_some())
}
