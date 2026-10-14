//! Answers messages that say "Bot please".

use log::error;
use poise::serenity_prelude::{self as serenity, CreateAllowedMentions, CreateMessage, Message};

use super::access_for;
use super::text::{has_trigger, speaker_label, split_message, DISCORD_MAX_CHARS};
use crate::types::bot::Data;

/// Answers `msg` in its channel if it says "Bot please". Does nothing if the assistant isn't set
/// up, for a bot's message (this also keeps the bot from answering itself), outside a server, or
/// for a member who isn't a raider: they get no answer at all, not even a refusal, so that the
/// channel isn't filled with replies to people the assistant doesn't talk to.
pub async fn answer_trigger(ctx: &serenity::Context, data: &Data, msg: &Message) {
    let Some(ai) = data.ai.as_ref() else { return };
    let Some(guild_id) = msg.guild_id else { return };
    if msg.author.bot || !has_trigger(&msg.content) {
        return;
    }

    let roles = msg.member.as_ref().map(|member| member.roles.as_slice()).unwrap_or(&[]);
    let access = access_for(&data.guild_settings.get(guild_id), roles, is_administrator(ctx, msg));
    if !access.is_allowed() {
        return;
    }

    // Mentions as names, and no live @everyone, in what the model sees.
    let question = msg.content_safe(&ctx.cache);
    let display_name = msg
        .member
        .as_ref()
        .and_then(|member| member.nick.as_deref())
        .unwrap_or_else(|| msg.author.display_name());
    let speaker = speaker_label(display_name, &msg.author.name);

    // Shown until it is dropped, after the answer has been sent.
    let _typing = msg.channel_id.start_typing(&ctx.http);

    let reply = match ai.ask(msg.channel_id.get(), msg.author.id.get(), &speaker, access, &question).await {
        Ok(answer) => answer,
        Err(e) => e.to_string(),
    };

    for (index, chunk) in split_message(&reply, DISCORD_MAX_CHARS).into_iter().enumerate() {
        // The answer is shown as a reply to the question; nobody is pinged, whatever it says.
        let mut message = CreateMessage::new().content(chunk).allowed_mentions(CreateAllowedMentions::new());
        if index == 0 {
            message = message.reference_message(msg);
        }
        if let Err(e) = msg.channel_id.send_message(&ctx.http, message).await {
            error!("Failed to send the AI answer in channel {}: {}", msg.channel_id, e);
            return;
        }
    }
}

/// Whether the author of `msg` is an administrator of its server: its owner, or holding a role with
/// the Administrator permission. A message carries only the member's role IDs, so they are resolved
/// against the cached server, as `checks::is_administrator` does for prefix commands; if the server
/// isn't cached this is `false`, which errs on the side of denying.
fn is_administrator(ctx: &serenity::Context, msg: &Message) -> bool {
    let Some(guild) = msg.guild(&ctx.cache) else {
        return false;
    };
    if guild.owner_id == msg.author.id {
        return true;
    }
    let Some(member) = &msg.member else {
        return false;
    };
    let everyone_role = guild.id.everyone_role();
    member
        .roles
        .iter()
        .chain(std::iter::once(&everyone_role))
        .filter_map(|role_id| guild.roles.get(role_id))
        .any(|role| role.permissions.administrator())
}
