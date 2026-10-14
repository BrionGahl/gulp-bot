use poise::serenity_prelude::{CreateAllowedMentions, RoleId};
use poise::CreateReply;

use crate::ai::{access_for, gate, speaker_label, split_message, Question, DISCORD_MAX_CHARS};
use crate::checks::is_administrator;
use crate::types::bot::{Context, Error};

/// Ask the guild's AI assistant anything
///
/// Only for raiders. The assistant remembers the last few questions asked in the same channel,
/// and answers messages that say "Bot please" the same way. Reply to one of its answers to carry
/// the conversation on.
#[poise::command(slash_command, guild_only, category = "AI")]
pub async fn ask(
    ctx: Context<'_>,
    #[description = "What do you want to ask?"] question: String,
) -> Result<(), Error> {
    // `guild_only` has already established this is a guild.
    let Some(guild_id) = ctx.guild_id() else { return Ok(()) };
    let Some(ai) = ctx.data().ai.as_ref() else {
        return refuse(ctx, "The AI assistant isn't set up for this bot yet.").await;
    };

    // The same roles the other raider-only commands go by, looked up for this server only.
    // Administrators count as Officers, as they do for `/settings` and `/wcl`.
    let settings = ctx.data().guild_settings.get(guild_id);
    let member = ctx.author_member().await;
    let roles: &[RoleId] = member.as_ref().map(|member| member.roles.as_slice()).unwrap_or(&[]);
    let access = access_for(&settings, roles, is_administrator(ctx));
    if !access.is_allowed() {
        let reason = if settings.raider_role_id.is_none() && settings.mod_role_id.is_none() {
            "No raider role is registered for this server. An administrator or moderator can set one with `/settings`."
        } else {
            "This command is only available to raiders."
        };
        return refuse(ctx, reason).await;
    }
    // Off, or in maintenance: told privately, before the public "thinking..." of the defer.
    if let Err(e) = gate(&settings.ai, access) {
        return refuse(ctx, &e.to_string()).await;
    }

    // The model takes a few seconds, longer than Discord waits for a first response.
    ctx.defer().await?;

    let display_name = match &member {
        Some(member) => member.display_name().to_string(),
        None => ctx.author().display_name().to_string(),
    };
    let speaker = speaker_label(&display_name, &ctx.author().name);

    let question = Question { speaker: &speaker, text: &question, replying_to: None };
    let (reply, answered) = match ai.ask(ctx.channel_id().get(), ctx.author().id.get(), access, &settings.ai, &question).await {
        Ok(answer) => (answer, true),
        Err(e) => (e.to_string(), false),
    };

    // Nobody is pinged, whatever the answer says.
    for chunk in split_message(&reply, DISCORD_MAX_CHARS) {
        let sent = ctx.send(CreateReply::default().content(chunk).allowed_mentions(CreateAllowedMentions::new())).await?;
        // A reply to any part of the answer carries the conversation on, as with "Bot please".
        if answered {
            if let Ok(message) = sent.message().await {
                ai.remember_answer(message.id.get());
            }
        }
    }
    Ok(())
}

/// Tells only the member who ran the command why nothing was answered.
async fn refuse(ctx: Context<'_>, content: &str) -> Result<(), Error> {
    ctx.send(CreateReply::default().content(content).ephemeral(true)).await?;
    Ok(())
}
