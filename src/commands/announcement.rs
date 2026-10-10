use poise::serenity_prelude::{ChannelId, ChannelType, CreateAllowedMentions};
use poise::{CreateReply, Modal};

use crate::announcements;
use crate::schedules;
use crate::types::bot::{Data, Error};

/// These commands are slash-only: the message is collected in a modal (the only way to enter
/// multiple lines), and a modal can only be opened in response to an interaction.
type ApplicationContext<'a> = poise::ApplicationContext<'a, Data, Error>;

/// Most announcements one server can have scheduled, to keep the list readable and bound how
/// much one server can make the bot post.
const MAX_ANNOUNCEMENTS_PER_GUILD: i64 = 25;

/// How much of each message `/announcement list` shows.
const PREVIEW_CHARS: usize = 80;

/// Cap on the `/announcement list` embed description, under Discord's 4096-character limit.
const MAX_LIST_CHARS: usize = 4000;

#[derive(Debug, Modal)]
#[name = "New announcement"]
struct AnnouncementModal {
    #[name = "Message"]
    #[placeholder = "@Raider and @Trial are replaced with pings of those roles."]
    #[paragraph]
    #[min_length = 1]
    // Discord's limit on a message's content.
    #[max_length = 2000]
    message: String,
}

/// Schedule, list and delete this server's recurring announcements. Officers only.
///
/// The moderator check and `guild_only` are set here once: poise applies a parent command's
/// checks to all of its subcommands.
#[poise::command(
    slash_command,
    guild_only,
    category = "Announcements",
    check = "crate::checks::check_is_moderator",
    subcommands("add", "list", "delete", "post"),
    subcommand_required,
)]
pub async fn announcement(_ctx: ApplicationContext<'_>) -> Result<(), Error> {
    Ok(())
}

/// Schedule a recurring announcement; you'll be asked for the message next
#[poise::command(slash_command, ephemeral)]
async fn add(
    ctx: ApplicationContext<'_>,
    #[description = "Channel to post in"]
    #[channel_types("Text", "News")]
    channel: ChannelId,
    #[description = "When to post (Eastern): sec min hour day-of-month month day-of-week [year], e.g. 0 0 12 * * Tue *"]
    cron: String,
) -> Result<(), Error> {
    let db = &ctx.data().db;
    // `check_is_moderator` has already established this is a guild.
    let Some(guild_id) = ctx.guild_id() else { return Ok(()) };

    // Everything that can be rejected is checked before the modal opens, so nobody writes out a
    // message only to be told the schedule was wrong.
    let schedule = match schedules::parse_schedule(cron.trim()) {
        Ok(schedule) => schedule,
        Err(problem) => return reply(ctx, problem).await,
    };
    let allowed = &[ChannelType::Text, ChannelType::News];
    if let Some(problem) = super::settings::channel_problem(ctx.into(), guild_id, channel, allowed, "a text channel").await? {
        return reply(ctx, problem).await;
    }
    if announcements::count(db, guild_id).await? >= MAX_ANNOUNCEMENTS_PER_GUILD {
        return reply(ctx, format!("This server already has {MAX_ANNOUNCEMENTS_PER_GUILD} announcements, the most allowed. Delete one first.")).await;
    }

    // `None` means the modal was dismissed or timed out; there's nothing to reply to.
    let Some(modal) = AnnouncementModal::execute(ctx).await? else { return Ok(()) };

    let id = announcements::create(db, guild_id, channel, &schedule, &modal.message, ctx.author().id).await?;

    // `parse_schedule` only accepts schedules with an upcoming fire time.
    let next = schedules::next_fire_time(&schedule).map_or("unknown".to_string(), |next| format!("<t:{}:F>", next.timestamp()));
    reply(ctx, format!("Announcement **#{id}** scheduled for <#{channel}> on `{}`. Next post: {next}.", schedule.source())).await
}

/// List this server's scheduled announcements
#[poise::command(slash_command, ephemeral)]
async fn list(ctx: ApplicationContext<'_>) -> Result<(), Error> {
    let Some(guild_id) = ctx.guild_id() else { return Ok(()) };

    let scheduled = announcements::list(&ctx.data().db, guild_id).await?;
    if scheduled.is_empty() {
        return reply(ctx, "No announcements are scheduled for this server. Add one with `/announcement add`.".to_string()).await;
    }

    let total = scheduled.len();
    let mut description = String::new();
    for (shown, announcement) in scheduled.iter().enumerate() {
        let next = schedules::next_fire_time(&announcement.schedule)
            .map_or("never".to_string(), |next| format!("<t:{}:R>", next.timestamp()));
        let entry = format!(
            "**#{}** — <#{}> — `{}` — next {}\nby <@{}>: {}\n\n",
            announcement.id,
            announcement.channel_id,
            announcement.schedule.source(),
            next,
            announcement.created_by,
            preview(&announcement.message),
        );
        // Stay inside Discord's embed description limit, leaving room for the "and N more" line.
        if description.len() + entry.len() > MAX_LIST_CHARS {
            description.push_str(&format!("…and {} more.", total - shown));
            break;
        }
        description.push_str(&entry);
    }

    let embed = crate::helper::create_base_embed(&ctx.into())
        .title(format!("Scheduled Announcements ({total})"))
        .description(description);

    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}

/// Delete one of this server's scheduled announcements
#[poise::command(slash_command, ephemeral)]
async fn delete(
    ctx: ApplicationContext<'_>,
    #[description = "Announcement number, from /announcement list"] id: i64,
) -> Result<(), Error> {
    let Some(guild_id) = ctx.guild_id() else { return Ok(()) };

    if announcements::delete(&ctx.data().db, guild_id, id).await? {
        reply(ctx, format!("Announcement **#{id}** deleted.")).await
    } else {
        reply(ctx, format!("This server has no announcement **#{id}**.")).await
    }
}

/// Post one of this server's announcements right now, without waiting for its schedule
#[poise::command(slash_command, ephemeral)]
async fn post(
    ctx: ApplicationContext<'_>,
    #[description = "Announcement number, from /announcement list"] id: i64,
) -> Result<(), Error> {
    let Some(guild_id) = ctx.guild_id() else { return Ok(()) };

    let Some(announcement) = announcements::get(&ctx.data().db, guild_id, id).await? else {
        return reply(ctx, format!("This server has no announcement **#{id}**.")).await;
    };

    let settings = ctx.data().guild_settings.get(guild_id);
    announcements::post(ctx.serenity_context().http.as_ref(), &announcement, &settings).await?;

    reply(ctx, format!("Posted announcement **#{id}** to <#{}>.", announcement.channel_id)).await
}

/// The start of `message` on one line, with formatting neutralised so it reads as a quote of the
/// text rather than rendering (or pinging) inside the list.
fn preview(message: &str) -> String {
    let flattened = message.split_whitespace().collect::<Vec<_>>().join(" ").replace('`', "'");
    let mut preview: String = flattened.chars().take(PREVIEW_CHARS).collect();
    if flattened.chars().count() > PREVIEW_CHARS {
        preview.push('…');
    }
    format!("`{preview}`")
}

/// Replies without pinging anyone, since these messages mention the channels and users involved.
async fn reply(ctx: ApplicationContext<'_>, content: String) -> Result<(), Error> {
    ctx.send(CreateReply::default().content(content).allowed_mentions(CreateAllowedMentions::new())).await?;
    Ok(())
}
