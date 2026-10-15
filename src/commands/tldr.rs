use chrono::{DateTime, Utc};
use chrono_tz::America::Chicago;
use log::{error, info};
use poise::serenity_prelude::{CreateAllowedMentions, GetMessages, MessageId, RoleId};
use poise::CreateReply;

use crate::ai::{
    access_for, conversation, describe_span, gate, speaker_label, split_message, transcript, Access, Ai, Cooldown, Line,
    DISCORD_MAX_CHARS, WINDOW_SECS,
};
use crate::checks::is_administrator;
use crate::guild_settings::GuildSettings;
use crate::types::bot::{Context, Error};

/// Messages are read back 100 at a time (Discord's most), at most this many times: 500 messages.
const MAX_PAGES: usize = 5;
const PAGE_SIZE: u8 = 100;

/// The most characters of conversation sent to the model. Leaves room for the prompt in a local
/// model's context, and keeps a summary's cost down; the oldest messages are left out first.
const MAX_TRANSCRIPT_CHARS: usize = 12_000;

/// Sum up this channel's recent chat, with jokes
///
/// Reads back up to 4 hours, stopping at the first pause of an hour or more. One per channel per
/// hour: until then, it points to the last one. Only for raiders.
#[poise::command(slash_command, guild_only, category = "AI")]
pub async fn tldr(ctx: Context<'_>) -> Result<(), Error> {
    // `guild_only` has already established this is a guild.
    let Some(guild_id) = ctx.guild_id() else { return Ok(()) };
    let Some(ai) = ctx.data().ai.as_ref() else {
        return refuse(ctx, "The AI assistant isn't set up for this bot yet.").await;
    };

    // The same access as `/ask`: raiders, Officers and Developers.
    let settings = ctx.data().guild_settings.get(guild_id);
    let member = ctx.author_member().await;
    let roles: &[RoleId] = member.as_ref().map(|member| member.roles.as_slice()).unwrap_or(&[]);
    let access = access_for(&settings, roles, is_administrator(ctx));
    if !access.is_allowed() {
        return refuse(ctx, "This command is only available to raiders.").await;
    }
    if let Err(e) = gate(&settings.ai, access) {
        return refuse(ctx, &e.to_string()).await;
    }

    let channel = ctx.channel_id().get();
    match ai.tldr_start(channel) {
        Cooldown::Ready => {}
        Cooldown::Busy => return refuse(ctx, "Already writing one for this channel, hang on.").await,
        Cooldown::Wait { link, posted_unix, remaining } => {
            let next = Utc::now().timestamp() + remaining.as_secs() as i64;
            return refuse(ctx, &format!("Already did one <t:{posted_unix}:R>: {link}\nNext one <t:{next}:R>.")).await;
        }
    }

    // The channel is held from here: unless a summary gets posted, let it go again.
    let posted = post_summary(ctx, ai, &settings, access).await;
    if !matches!(posted, Ok(true)) {
        ai.tldr_cancel(channel);
    }
    posted.map(|_| ())
}

/// Reads the channel back, has the conversation summed up and posts it. Returns whether a summary
/// was posted.
async fn post_summary(ctx: Context<'_>, ai: &Ai, settings: &GuildSettings, access: Access) -> Result<bool, Error> {
    // Reading the channel and the model both take longer than Discord waits for a first response.
    ctx.defer().await?;

    let now = Utc::now().timestamp();
    let newest_first = match read_back(ctx, now).await {
        Ok(lines) => lines,
        Err(e) => {
            error!("Failed to read the history of channel {} for /tldr: {}", ctx.channel_id(), e);
            say(ctx, "Can't read this channel's history. I need the Read Message History permission here.").await?;
            return Ok(false);
        }
    };
    let lines = conversation(newest_first, now);
    let Some(first) = lines.first() else {
        say(ctx, "Nothing to sum up: nobody has said anything here in the last 4 hours.").await?;
        return Ok(false);
    };
    let span = describe_span(now - first.at);

    let (text, omitted) = transcript(&lines, MAX_TRANSCRIPT_CHARS);
    let summary = match ai.summarize(access, &settings.ai, &text, omitted).await {
        Ok(summary) => summary,
        Err(e) => {
            say(ctx, &e.to_string()).await?;
            return Ok(false);
        }
    };

    let guild_id = ctx.guild_id().map_or(0, |guild_id| guild_id.get());
    let channel_id = ctx.channel_id();
    let mut link = format!("https://discord.com/channels/{guild_id}/{channel_id}");
    let full = format!("**tl;dr** of the last {span} ({} messages)\n{summary}", lines.len());
    for (index, chunk) in split_message(&full, DISCORD_MAX_CHARS).into_iter().enumerate() {
        let sent = ctx.send(reply(&chunk)).await?;
        if let Ok(message) = sent.message().await {
            // A reply to the summary carries on with the assistant, as for its other answers.
            ai.remember_answer(message.id.get());
            if index == 0 {
                link = format!("https://discord.com/channels/{guild_id}/{channel_id}/{}", message.id);
            }
        }
    }

    ai.tldr_finish(channel_id.get(), link, now);
    info!("{} ({}) had {} messages of channel {channel_id} summed up", ctx.author().id, ctx.author().name, lines.len());
    Ok(true)
}

/// The channel's messages of about the last `WINDOW_SECS`, the newest first, as transcript lines.
/// Bots' messages are left out, and so are messages with neither text nor a file.
async fn read_back(ctx: Context<'_>, now: i64) -> Result<Vec<Line>, Error> {
    let serenity_ctx = ctx.serenity_context();
    let mut lines = Vec::new();
    let mut before: Option<MessageId> = None;

    for _ in 0..MAX_PAGES {
        let mut request = GetMessages::new().limit(PAGE_SIZE);
        if let Some(before) = before {
            request = request.before(before);
        }
        let page = ctx.channel_id().messages(&serenity_ctx.http, request).await?;
        let Some(oldest) = page.last() else { break };
        before = Some(oldest.id);
        // A short page is the start of the channel; one reaching past the window is enough.
        let done = page.len() < usize::from(PAGE_SIZE) || now - oldest.timestamp.unix_timestamp() > WINDOW_SECS;

        for message in &page {
            if message.author.bot {
                continue;
            }
            // Mentions as names, and no live @everyone, in what the model sees.
            let mut text = message.content_safe(&serenity_ctx.cache);
            if text.trim().is_empty() {
                if message.attachments.is_empty() {
                    continue;
                }
                text = "[attachment]".to_string();
            }
            let at = message.timestamp.unix_timestamp();
            lines.push(Line {
                at,
                time: central_time(at),
                author: speaker_label(message.author.display_name(), &message.author.name),
                text,
            });
        }

        if done {
            break;
        }
    }
    Ok(lines)
}

/// `at` (Unix seconds) as a time of day in the guild's Central Time, e.g. `21:04`.
fn central_time(at: i64) -> String {
    DateTime::from_timestamp(at, 0).map(|time| time.with_timezone(&Chicago).format("%H:%M").to_string()).unwrap_or_default()
}

/// A message in the channel that pings nobody.
fn reply(content: &str) -> CreateReply {
    CreateReply::default().content(content).allowed_mentions(CreateAllowedMentions::new())
}

/// Says `content` after the command was deferred.
async fn say(ctx: Context<'_>, content: &str) -> Result<(), Error> {
    ctx.send(reply(content)).await?;
    Ok(())
}

/// Tells only the member who ran the command why nothing was done.
async fn refuse(ctx: Context<'_>, content: &str) -> Result<(), Error> {
    ctx.send(CreateReply::default().content(content).ephemeral(true).allowed_mentions(CreateAllowedMentions::new())).await?;
    Ok(())
}
