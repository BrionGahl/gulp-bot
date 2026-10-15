//! "bot get them": an Officer replies to a message with these words, and the bot deletes the
//! message they replied to and barks.
//!
//! An Officer is who the assistant takes for one: a holder of the moderator role, a server
//! administrator, or a Developer (`ai::access_for`). It works whether or not the AI assistant is
//! set up or switched on: it is a command, not a question. The bot needs the Manage Messages
//! permission in the channel to delete someone else's message.

use log::{error, info};
use poise::serenity_prelude::{self as serenity, CreateAllowedMentions, CreateMessage, Message};

use crate::ai::{access_for, author_is_administrator, has_get_them};
use crate::message_replay;
use crate::types::bot::Data;

/// What the bot says when it has got them.
const BARK: &str = "arf! arf!";

/// What it says when it couldn't delete the message.
const MISSED: &str = "grr... can't reach that one. i need the Manage Messages permission here.";

/// Handles `msg` if it is "bot get them" in reply to another message: deletes that message and
/// barks, if the author is an Officer. Returns whether `msg` was the command, whoever sent it, so
/// that it isn't also taken as a question for the assistant. Anyone else gets no answer at all, as
/// with "Bot please" from someone the assistant doesn't talk to.
pub async fn handle(ctx: &serenity::Context, data: &Data, msg: &Message) -> bool {
    let Some(guild_id) = msg.guild_id else { return false };
    if msg.author.bot || !has_get_them(&msg.content) {
        return false;
    }
    // Without a reply there is nobody to get.
    let Some(target) = msg.referenced_message.as_deref() else { return false };

    let roles = msg.member.as_ref().map(|member| member.roles.as_slice()).unwrap_or(&[]);
    let access = access_for(&data.guild_settings.get(guild_id), roles, author_is_administrator(ctx, msg));
    if !access.is_officer() {
        return true;
    }

    // Out of the replay buffer first: a message the bot deleted on an Officer's say-so must not be
    // reposted as if its author had deleted it.
    message_replay::forget_messages(data, &[target.id]).await;

    if let Err(e) = target.delete(&ctx.http).await {
        error!("Failed to delete message {} in channel {} for \"bot get them\": {}", target.id, msg.channel_id, e);
        bark(ctx, msg, MISSED).await;
        return true;
    }

    info!(
        "audit: {} ({}) had \"bot get them\" delete message {} by {} ({}) in channel {} of guild {guild_id}",
        msg.author.id, msg.author.name, target.id, target.author.id, target.author.name, msg.channel_id
    );
    bark(ctx, msg, BARK).await;
    true
}

/// Says `text` in reply to the command, pinging nobody.
async fn bark(ctx: &serenity::Context, msg: &Message, text: &str) {
    let message = CreateMessage::new().content(text).reference_message(msg).allowed_mentions(CreateAllowedMentions::new());
    if let Err(e) = msg.channel_id.send_message(&ctx.http, message).await {
        error!("Failed to bark in channel {}: {}", msg.channel_id, e);
    }
}
