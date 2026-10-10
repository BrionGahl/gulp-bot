use chrono::NaiveDate;
use chrono_tz::America::New_York;

use crate::raid_attendance::{self, Moment};
use crate::raid_notes;
use crate::types::bot::{Context, Error};

/// Immediately create the raid notes forum thread for a given week, without waiting for the schedule.
///
/// `week` is any date (`YYYY-MM-DD`) falling in the target week; the thread is titled for that
/// week's Tuesday–Monday span. Defaults to the current week (Eastern) when omitted.
#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "Debug",
    check = "crate::checks::check_is_moderator",
)]
pub async fn test_raid_notes(
    ctx: Context<'_>,
    #[description = "Any date (YYYY-MM-DD) in the target week; defaults to this week"] week: Option<String>,
) -> Result<(), Error> {
    // `check_is_moderator` has already established this is a guild.
    let Some(guild_id) = ctx.guild_id() else { return Ok(()) };
    let Some(channel_id) = ctx.data().guild_settings.get(guild_id).raid_notes_channel_id else {
        ctx.say("No raid notes channel is registered for this server; set one with `/settings raid_notes_channel`.").await?;
        return Ok(());
    };

    let date = match week {
        Some(s) => match NaiveDate::parse_from_str(s.trim(), "%Y-%m-%d") {
            Ok(date) => date,
            Err(_) => {
                ctx.say(format!("Couldn't read `{}` as a date. Use `YYYY-MM-DD`.", s)).await?;
                return Ok(());
            }
        },
        None => chrono::Utc::now().with_timezone(&New_York).date_naive(),
    };

    let post = raid_notes::week_post(ctx.serenity_context().http.as_ref(), &ctx.data().db, guild_id, channel_id, date).await?;

    if post.created {
        ctx.say(format!("Created raid notes thread \"{}\": <#{}>.", post.title, post.thread_id)).await?;
    } else {
        ctx.say(format!("That week already has a raid notes thread, \"{}\": <#{}>.", post.title, post.thread_id)).await?;
    }
    Ok(())
}

/// Record who is in the raid voice channel right now and post it to this week's raid notes thread.
#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "Debug",
    check = "crate::checks::check_is_moderator",
)]
pub async fn test_raid_attendance(ctx: Context<'_>) -> Result<(), Error> {
    // `check_is_moderator` has already established this is a guild.
    let Some(guild_id) = ctx.guild_id() else { return Ok(()) };
    let settings = ctx.data().guild_settings.get(guild_id);
    let Some(raid_voice) = settings.raid_voice else {
        ctx.say("Raid attendance isn't set up for this server; set it with `/settings raid_voice`.").await?;
        return Ok(());
    };
    let Some(raid_notes_channel_id) = settings.raid_notes_channel_id else {
        ctx.say("No raid notes channel is registered for this server; set one with `/settings raid_notes_channel`.").await?;
        return Ok(());
    };

    let serenity_ctx = ctx.serenity_context();
    let thread_id = raid_attendance::record(
        &serenity_ctx.http,
        &serenity_ctx.cache,
        &ctx.data().db,
        guild_id,
        raid_voice.channel_id,
        raid_notes_channel_id,
        Moment::Test,
        chrono::Utc::now().with_timezone(&New_York),
    )
    .await?;

    ctx.say(format!("Posted an attendance snapshot of <#{}> to <#{}>.", raid_voice.channel_id, thread_id)).await?;
    Ok(())
}

/// Post a sample delete-and-replay embed here, to preview how a reposted message looks.
///
/// Uses you as the stand-in author. Only exercises the embed rendering — it doesn't touch the
/// audit log check or delete anything.
#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "Debug",
    check = "crate::checks::check_is_moderator",
)]
pub async fn test_replay(
    ctx: Context<'_>,
    #[description = "Text to show in the replayed embed"] message: Option<String>,
) -> Result<(), Error> {
    let content = message.unwrap_or_else(|| "This is a sample deleted message.".to_string());

    crate::message_replay::render_test_replay(
        ctx.serenity_context(),
        ctx.channel_id(),
        ctx.author(),
        &content,
    )
    .await;

    ctx.say("Posted a sample replay embed.").await?;
    Ok(())
}
