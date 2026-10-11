use poise::serenity_prelude::{ChannelId, ChannelType, CreateAllowedMentions, GuildId, RoleId, UserId};
use poise::{ChoiceParameter, CreateReply};

use crate::guild_settings::{RaidVoice, Setting, WclSource};
use crate::personal_officer_channels;
use crate::raid_notes;
use crate::schedules;
use crate::types::bot::{Context, Error};
use crate::wcl_consumables::{self, AddOutcome as ConsumableOutcome, Kind};
use crate::wcl_ignored_players::{self, AddOutcome as IgnoreOutcome};
use crate::wcl_mechanics::{self, AddOutcome};

const TEXT_CHANNEL_TYPES: &[ChannelType] = &[ChannelType::Text, ChannelType::News];

/// Cap on the `officer_channel_list` embed description, under Discord's 4096-character limit.
const MAX_LIST_CHARS: usize = 4000;

/// View or change what this server has registered with the bot. Administrators and moderators.
///
/// Everything here applies only to the server the command is used in. Setting the moderator role
/// is limited to administrators.
//
// The check and `guild_only` are set here once: poise applies a parent command's checks to all
// of its subcommands. There is deliberately no `default_member_permissions`: Discord would hide
// the command from moderators who aren't administrators. (Kept out of the doc comment, which
// poise shows to users in `/help settings`.)
#[poise::command(
    prefix_command,
    slash_command,
    guild_only,
    category = "Settings",
    check = "crate::checks::check_is_admin_or_moderator",
    subcommands(
        "show",
        "mod_role",
        "raider_role",
        "trial_role",
        "officer_category",
        "officer_channel_add",
        "officer_channel_remove",
        "officer_channel_list",
        "raid_notes_channel",
        "raid_notes_cron",
        "raid_voice",
        "replay_add",
        "replay_remove",
        "clips_add",
        "clips_remove",
        "bart_token",
        "wowutils",
        "wcl",
        "clear",
    ),
    subcommand_required,
)]
pub async fn settings(_ctx: Context<'_>) -> Result<(), Error> {
    Ok(())
}

/// Show everything registered for this server
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn show(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;
    let settings = ctx.data().guild_settings.get(guild_id);

    let role = |id: Option<RoleId>| id.map_or("Not set".to_string(), |id| format!("<@&{id}>"));
    let channel = |id: Option<ChannelId>| id.map_or("Not set".to_string(), |id| format!("<#{id}>"));

    let mut clips: Vec<ChannelId> = settings.clips_channel_ids.iter().copied().collect();
    clips.sort();
    let clips = if clips.is_empty() {
        "None".to_string()
    } else {
        clips.iter().map(|id| format!("<#{id}>")).collect::<Vec<_>>().join(", ")
    };

    let mut replay_users: Vec<UserId> = settings.replay_user_ids.iter().copied().collect();
    replay_users.sort();
    let replay_users = if replay_users.is_empty() {
        "None".to_string()
    } else {
        replay_users.iter().map(|id| format!("<@{id}>")).collect::<Vec<_>>().join(", ")
    };

    let raid_voice = match &settings.raid_voice {
        Some(raid_voice) => format!(
            "<#{}> — start `{}`, end `{}`",
            raid_voice.channel_id,
            raid_voice.start.source(),
            raid_voice.end.source(),
        ),
        None => "Not set".to_string(),
    };

    let raid_notes_schedule = match &settings.raid_notes_cron {
        Some(schedule) => format!("`{}`", schedule.source()),
        None => format!("Default (`{}`)", raid_notes::DEFAULT_RAID_NOTES_CRON),
    };

    let wcl_source = match &settings.wcl_source {
        Some(source) => format!("Guild {}, uploader {}", source.guild_id, source.uploader_id),
        None => "Not set".to_string(),
    };

    // Names are limited when they're added (`wcl_mechanics::MAX_NAME_CHARS`, `MAX_TRACKED`), so
    // the list always fits in one field.
    let wcl_abilities = wcl_mechanics::list(&ctx.data().db, guild_id).await?;
    let wcl_abilities = if wcl_abilities.is_empty() {
        "None".to_string()
    } else {
        wcl_abilities.iter().map(|ability| ability.name.as_str()).collect::<Vec<_>>().join(", ")
    };

    // Only how many: the names can add up to more than a field holds, and `/settings wcl ignore_list` has them.
    let wcl_ignored = match wcl_ignored_players::list(&ctx.data().db, guild_id).await?.len() {
        0 => "None".to_string(),
        1 => "1 player".to_string(),
        count => format!("{count} players"),
    };

    // At most `MAX_PER_KIND` IDs of each kind, so this is always short.
    let registered_consumables = wcl_consumables::list(&ctx.data().db, guild_id).await?;
    let wcl_consumables = Kind::ALL
        .into_iter()
        .filter_map(|kind| {
            let ids = wcl_consumables::ids(&registered_consumables, kind);
            (!ids.is_empty()).then(|| format!("{}: {}", kind.plural(), ids.iter().map(u32::to_string).collect::<Vec<_>>().join(", ")))
        })
        .collect::<Vec<_>>()
        .join("\n");
    let wcl_consumables = if wcl_consumables.is_empty() { "None".to_string() } else { wcl_consumables };

    let embed = crate::helper::create_base_embed(&ctx)
        .title("Server Settings")
        .field(Setting::ModRole.name(), role(settings.mod_role_id), true)
        .field(Setting::RaiderRole.name(), role(settings.raider_role_id), true)
        .field(Setting::TrialRole.name(), role(settings.trial_role_id), true)
        .field(Setting::PersonalOfficerCategory.name(), channel(settings.personal_officer_category_id), true)
        .field(Setting::RaidNotesChannel.name(), channel(settings.raid_notes_channel_id), true)
        .field(ClearTarget::RaidNotesCron.name(), raid_notes_schedule, true)
        .field(ClearTarget::RaidVoice.name(), raid_voice, false)
        .field("Clips channels", clips, false)
        .field("Replay users", replay_users, false)
        // Credentials are never echoed back, only whether they're registered.
        .field(ClearTarget::BartToken.name(), if settings.bart_token.is_some() { "Set" } else { "Not set" }, true)
        .field(ClearTarget::WowUtils.name(), if settings.wowutils_token.is_some() { "Set" } else { "Not set" }, true)
        .field(Setting::WclChannel.name(), channel(settings.wcl_channel_id), true)
        .field(ClearTarget::WclSource.name(), wcl_source, true)
        .field("Warcraft Logs abilities", wcl_abilities, false)
        .field("Warcraft Logs ignored players", wcl_ignored, true)
        .field("Warcraft Logs consumables", wcl_consumables, true);

    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}

/// Register the moderator (officer) role for this server. Administrators only.
#[poise::command(prefix_command, slash_command, ephemeral, check = "crate::checks::check_is_administrator")]
async fn mod_role(ctx: Context<'_>, #[description = "Role that grants moderator commands"] role: RoleId) -> Result<(), Error> {
    register_role(ctx, Setting::ModRole, role).await
}

/// Register the raider role for this server
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn raider_role(ctx: Context<'_>, #[description = "Role held by raiders"] role: RoleId) -> Result<(), Error> {
    register_role(ctx, Setting::RaiderRole, role).await
}

/// Register the trial role for this server
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn trial_role(ctx: Context<'_>, #[description = "Role held by trial raiders"] role: RoleId) -> Result<(), Error> {
    register_role(ctx, Setting::TrialRole, role).await
}

/// Register the category that personal officer channels are created under
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn officer_category(
    ctx: Context<'_>,
    #[description = "Category for personal officer channels"]
    #[channel_types("Category")]
    category: ChannelId,
) -> Result<(), Error> {
    register_channel(ctx, Setting::PersonalOfficerCategory, category, &[ChannelType::Category], "a category").await
}

/// Register the forum channel that weekly raid notes threads are posted in
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn raid_notes_channel(
    ctx: Context<'_>,
    #[description = "Forum channel for weekly raid notes"]
    #[channel_types("Forum")]
    channel: ChannelId,
) -> Result<(), Error> {
    register_channel(ctx, Setting::RaidNotesChannel, channel, &[ChannelType::Forum], "a forum channel").await
}

/// Set when the raid notes post is created, as a quartz-style cron expression (Eastern time)
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn raid_notes_cron(
    ctx: Context<'_>,
    #[description = "sec min hour day-of-month month day-of-week [year], e.g. 0 0 9 * * Fri *"]
    #[rest]
    expression: String,
) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    let schedule = match schedules::parse_schedule(expression.trim()) {
        Ok(schedule) => schedule,
        Err(problem) => return reply(ctx, problem).await,
    };
    // `parse_schedule` only accepts schedules with an upcoming fire time.
    let next = schedules::next_fire_time(&schedule).map_or("unknown".to_string(), |next| format!("<t:{}:F>", next.timestamp()));
    let source = schedule.source().to_string();

    ctx.data().guild_settings.set_raid_notes_cron(guild_id, Some(schedule)).await?;
    reply(ctx, format!("{} set to `{source}`. Next post: {next}.", ClearTarget::RaidNotesCron.name())).await
}

/// Record who is in the raid voice channel at the start and end of each raid (Eastern time)
///
/// Both schedules are quartz-style cron expressions. Through the prefix form, each one has to be
/// wrapped in quotes, since they contain spaces.
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn raid_voice(
    ctx: Context<'_>,
    #[description = "Voice channel the raid happens in"]
    #[channel_types("Voice", "Stage")]
    channel: ChannelId,
    #[description = "Raid start: sec min hour day-of-month month day-of-week [year], e.g. 0 0 20 * * Tue,Thu *"]
    start: String,
    #[description = "Raid end, same format, e.g. 0 0 23 * * Tue,Thu *"]
    end: String,
) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    let allowed = &[ChannelType::Voice, ChannelType::Stage];
    if let Some(problem) = channel_problem(ctx, guild_id, channel, allowed, "a voice channel").await? {
        return reply(ctx, problem).await;
    }
    let start = match schedules::parse_schedule(start.trim()) {
        Ok(schedule) => schedule,
        Err(problem) => return reply(ctx, format!("Start: {problem}")).await,
    };
    let end = match schedules::parse_schedule(end.trim()) {
        Ok(schedule) => schedule,
        Err(problem) => return reply(ctx, format!("End: {problem}")).await,
    };

    // `parse_schedule` only accepts schedules with an upcoming fire time.
    let next = |schedule: &cron::Schedule| {
        schedules::next_fire_time(schedule).map_or("unknown".to_string(), |next| format!("<t:{}:F>", next.timestamp()))
    };
    let mut message = format!(
        "{} set to <#{channel}>. Next start: {}. Next end: {}.",
        ClearTarget::RaidVoice.name(),
        next(&start),
        next(&end),
    );
    if ctx.data().guild_settings.get(guild_id).raid_notes_channel_id.is_none() {
        message.push_str("\nAttendance is posted in the week's raid notes thread, and no raid notes channel is registered yet; set one with `/settings raid_notes_channel`.");
    }

    ctx.data().guild_settings.set_raid_voice(guild_id, Some(RaidVoice { channel_id: channel, start, end })).await?;
    reply(ctx, message).await
}

/// Add a member whose self-deleted messages the bot reposts
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn replay_add(ctx: Context<'_>, #[description = "Member to replay deleted messages for"] user: UserId) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    if guild_id.member(ctx, user).await.is_err() {
        return reply(ctx, format!("<@{user}> isn't a member of this server, so they can't be registered here.")).await;
    }

    ctx.data().guild_settings.add_replay_user(guild_id, user).await?;
    reply(ctx, format!("<@{user}> is now a replay user.")).await
}

/// Stop reposting a member's self-deleted messages
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn replay_remove(ctx: Context<'_>, #[description = "Replay user to remove"] user: UserId) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    // No membership check: the member may have left the server since they were added.
    if ctx.data().guild_settings.remove_replay_user(guild_id, user).await? {
        reply(ctx, format!("<@{user}> is no longer a replay user.")).await
    } else {
        reply(ctx, format!("<@{user}> isn't a replay user in this server.")).await
    }
}

/// Restrict a channel to messages that contain a video
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn clips_add(
    ctx: Context<'_>,
    #[description = "Channel to restrict to video-only messages"]
    #[channel_types("Text", "News")]
    channel: ChannelId,
) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    if let Some(problem) = channel_problem(ctx, guild_id, channel, TEXT_CHANNEL_TYPES, "a text channel").await? {
        return reply(ctx, problem).await;
    }

    ctx.data().guild_settings.add_clips_channel(guild_id, channel).await?;
    reply(ctx, format!("<#{channel}> is now a clips channel.")).await
}

/// Stop restricting a channel to video-only messages
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn clips_remove(
    ctx: Context<'_>,
    #[description = "Clips channel to stop restricting"] channel: ChannelId,
) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    // No ownership check: this only ever removes a row registered under this guild, and the
    // channel may have been deleted since it was added.
    if ctx.data().guild_settings.remove_clips_channel(guild_id, channel).await? {
        reply(ctx, format!("<#{channel}> is no longer a clips channel.")).await
    } else {
        reply(ctx, format!("<#{channel}> isn't registered as a clips channel in this server.")).await
    }
}

/// Register a personal officer channel that already exists for a member
///
/// Once a member is registered the bot never creates a channel for them, so this is how channels
/// made by hand (or before the bot tracked them) are made known to it.
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn officer_channel_add(
    ctx: Context<'_>,
    #[description = "Member the channel belongs to"] user: UserId,
    #[description = "Their existing personal officer channel"]
    #[channel_types("Text")]
    channel: ChannelId,
) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    if guild_id.member(ctx, user).await.is_err() {
        return reply(ctx, format!("<@{user}> isn't a member of this server, so they can't be registered here.")).await;
    }
    if let Some(problem) = channel_problem(ctx, guild_id, channel, &[ChannelType::Text], "a text channel").await? {
        return reply(ctx, problem).await;
    }

    personal_officer_channels::register(&ctx.data().db, guild_id, user, channel).await?;
    reply(ctx, format!("<#{channel}> registered as <@{user}>'s personal officer channel. The bot won't create one for them.")).await
}

/// Forget a member's personal officer channel, so the bot will create one for them again
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn officer_channel_remove(
    ctx: Context<'_>,
    #[description = "Member to forget the channel for"] user: UserId,
) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    // No membership check: the member may have left the server since they were registered.
    if personal_officer_channels::unregister(&ctx.data().db, guild_id, user).await? {
        reply(ctx, format!("<@{user}> no longer has a registered personal officer channel. The channel itself was not deleted.")).await
    } else {
        reply(ctx, format!("<@{user}> has no registered personal officer channel in this server.")).await
    }
}

/// List the members who have a registered personal officer channel
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn officer_channel_list(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    let registered = personal_officer_channels::list(&ctx.data().db, guild_id).await?;
    if registered.is_empty() {
        return reply(ctx, "No personal officer channels are registered for this server.".to_string()).await;
    }

    let total = registered.len();
    let mut description = String::new();
    for (shown, (user_id, channel_id)) in registered.into_iter().enumerate() {
        let line = match channel_id {
            Some(channel_id) => format!("<@{user_id}> — <#{channel_id}>\n"),
            None => format!("<@{user_id}> — channel not recorded\n"),
        };
        // Stay inside Discord's embed description limit, leaving room for the "and N more" line.
        if description.len() + line.len() > MAX_LIST_CHARS {
            description.push_str(&format!("…and {} more.", total - shown));
            break;
        }
        description.push_str(&line);
    }

    let embed = crate::helper::create_base_embed(&ctx)
        .title(format!("Personal Officer Channels ({total})"))
        .description(description);

    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}

/// Register the Bart Timeline Reminders token shown to this server's raiders
///
/// Slash command only, so the token is never typed into a channel as a normal message.
#[poise::command(slash_command, ephemeral)]
async fn bart_token(ctx: Context<'_>, #[description = "Bart Timeline Reminders access token"] token: String) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    let token = token.trim();
    if token.is_empty() {
        return reply(ctx, "The token can't be empty.".to_string()).await;
    }

    ctx.data().guild_settings.set_bart_token(guild_id, Some(token)).await?;
    reply(ctx, format!("{} registered for this server.", ClearTarget::BartToken.name())).await
}

/// Register the WowUtils token and group that this server's droptimizers are submitted to
#[poise::command(slash_command, ephemeral)]
async fn wowutils(
    ctx: Context<'_>,
    #[description = "WowUtils API token"] token: String,
    #[description = "WowUtils group ID to submit droptimizer reports to"] group_id: String,
) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    let (token, group_id) = (token.trim(), group_id.trim());
    if token.is_empty() || group_id.is_empty() {
        return reply(ctx, "The token and group ID can't be empty.".to_string()).await;
    }

    ctx.data().guild_settings.set_wowutils(guild_id, Some((token, group_id))).await?;
    reply(ctx, format!("{} registered for this server.", ClearTarget::WowUtils.name())).await
}

/// Warcraft Logs settings: channel, report source, abilities, ignored players, consumables
//
// A group of its own because Discord allows a command at most 25 subcommands, and `/settings`
// had more than that with these directly under it.
#[poise::command(
    prefix_command,
    slash_command,
    subcommands(
        "wcl_channel",
        "wcl_source",
        "wcl_mechanic_add",
        "wcl_mechanic_remove",
        "wcl_mechanic_list",
        "wcl_ignore_add",
        "wcl_ignore_remove",
        "wcl_ignore_list",
        "wcl_consumable_add",
        "wcl_consumable_remove",
        "wcl_consumable_list",
    ),
    subcommand_required,
)]
async fn wcl(_ctx: Context<'_>) -> Result<(), Error> {
    Ok(())
}

/// Register the channel where Warcraft Logs results are posted, and the only one `/wcl` works in
#[poise::command(prefix_command, slash_command, ephemeral, rename = "channel")]
async fn wcl_channel(
    ctx: Context<'_>,
    #[description = "Officer channel for Warcraft Logs results"]
    #[channel_types("Text", "News")]
    channel: ChannelId,
) -> Result<(), Error> {
    register_channel(ctx, Setting::WclChannel, channel, TEXT_CHANNEL_TYPES, "a text channel").await
}

/// Register where this server's Warcraft Logs reports are found: the guild and the uploader
///
/// Both are the numeric IDs Warcraft Logs uses. Only reports from this uploader are picked when
/// `/wcl report` is run without a link.
#[poise::command(prefix_command, slash_command, ephemeral, rename = "source")]
async fn wcl_source(
    ctx: Context<'_>,
    #[description = "Warcraft Logs guild ID the reports are uploaded to"] guild: u32,
    #[description = "Warcraft Logs user ID of the account that uploads the logs"] uploader: u32,
) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    // The API's IDs are signed 32-bit integers, and zero is never one.
    let valid = |id: u32| (1..=i32::MAX as u32).contains(&id);
    if !valid(guild) || !valid(uploader) {
        return reply(ctx, "Those aren't valid Warcraft Logs IDs. Both are positive whole numbers.".to_string()).await;
    }

    ctx.data().guild_settings.set_wcl_source(guild_id, Some(WclSource { guild_id: guild, uploader_id: uploader })).await?;
    reply(ctx, format!("{} set to guild {guild}, uploader {uploader}.", ClearTarget::WclSource.name())).await
}

/// Track an ability in the Warcraft Logs overview, to see who took damage from it
///
/// Give a name to show and the ability's ID as Warcraft Logs logs its damage: click the ability in a
/// report's Damage Taken tab. It can differ from the ID on Wowhead, and one ability can be logged
/// under more than one ID. Adding an ability that is already tracked renames it.
#[poise::command(prefix_command, slash_command, ephemeral, rename = "mechanic_add")]
async fn wcl_mechanic_add(
    ctx: Context<'_>,
    #[description = "Name to show, e.g. Coalesced Venom"] name: String,
    #[description = "The game's ability (spell) ID"] ability_id: u32,
) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    let name = name.trim();
    if name.is_empty() || name.chars().count() > wcl_mechanics::MAX_NAME_CHARS {
        return reply(ctx, format!("The name has to be 1 to {} characters.", wcl_mechanics::MAX_NAME_CHARS)).await;
    }
    // The API's IDs are signed 32-bit integers, and zero is never one.
    if !(1..=i32::MAX as u32).contains(&ability_id) {
        return reply(ctx, "That isn't a valid ability ID. It's a positive whole number.".to_string()).await;
    }

    let message = match wcl_mechanics::add(&ctx.data().db, guild_id, ability_id, name).await? {
        AddOutcome::Added => format!("Tracking **{name}** (ability {ability_id})."),
        AddOutcome::Renamed => format!("Ability {ability_id} is now called **{name}**."),
        AddOutcome::NameTaken(other) => format!("Another ability (ID {other}) is already called **{name}**. Pick a different name."),
        AddOutcome::Full => format!(
            "This server already tracks {} abilities. Remove one with `/settings wcl mechanic_remove` first.",
            wcl_mechanics::MAX_TRACKED
        ),
    };
    reply(ctx, message).await
}

/// Stop tracking an ability in the Warcraft Logs overview
#[poise::command(prefix_command, slash_command, ephemeral, rename = "mechanic_remove")]
async fn wcl_mechanic_remove(
    ctx: Context<'_>,
    #[description = "Tracked ability to remove"]
    #[autocomplete = "crate::commands::wcl::autocomplete_ability"]
    name: String,
) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    if wcl_mechanics::remove(&ctx.data().db, guild_id, name.trim()).await? {
        reply(ctx, format!("No longer tracking **{}**.", name.trim())).await
    } else {
        reply(ctx, format!("No tracked ability is called **{}**. See `/settings wcl mechanic_list`.", name.trim())).await
    }
}

/// List the abilities tracked in the Warcraft Logs overview
#[poise::command(prefix_command, slash_command, ephemeral, rename = "mechanic_list")]
async fn wcl_mechanic_list(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    let tracked = wcl_mechanics::list(&ctx.data().db, guild_id).await?;
    if tracked.is_empty() {
        return reply(ctx, "No abilities are tracked. Add one with `/settings wcl mechanic_add`.".to_string()).await;
    }

    let lines: Vec<String> = tracked.iter().map(|ability| format!("**{}** — ability {}", ability.name, ability.ability_id)).collect();
    reply(ctx, format!("Tracked abilities ({} of {}):\n{}", tracked.len(), wcl_mechanics::MAX_TRACKED, lines.join("\n"))).await
}

/// Leave a player out of the Warcraft Logs overview
///
/// An ignored player appears in no ranking, and what they took doesn't count towards any total. Give
/// the character name; capitals don't matter. A server can ignore 50 players.
#[poise::command(prefix_command, slash_command, ephemeral, rename = "ignore_add")]
async fn wcl_ignore_add(ctx: Context<'_>, #[description = "Character name, e.g. Tritonian"] name: String) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    let name = name.trim();
    if name.is_empty() || name.chars().count() > wcl_ignored_players::MAX_NAME_CHARS {
        return reply(ctx, format!("The name has to be 1 to {} characters.", wcl_ignored_players::MAX_NAME_CHARS)).await;
    }

    let message = match wcl_ignored_players::add(&ctx.data().db, guild_id, name).await? {
        IgnoreOutcome::Added => format!("**{name}** is now left out of the Warcraft Logs overview."),
        IgnoreOutcome::AlreadyIgnored => format!("**{name}** is already ignored."),
        IgnoreOutcome::Full => format!(
            "This server already ignores {} players. Remove one with `/settings wcl ignore_remove` first.",
            wcl_ignored_players::MAX_IGNORED
        ),
    };
    reply(ctx, message).await
}

/// Show a player in the Warcraft Logs overview again
#[poise::command(prefix_command, slash_command, ephemeral, rename = "ignore_remove")]
async fn wcl_ignore_remove(
    ctx: Context<'_>,
    #[description = "Ignored character to show again"]
    #[autocomplete = "crate::commands::wcl::autocomplete_ignored_player"]
    name: String,
) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    if wcl_ignored_players::remove(&ctx.data().db, guild_id, name.trim()).await? {
        reply(ctx, format!("**{}** is no longer ignored.", name.trim())).await
    } else {
        reply(ctx, format!("**{}** isn't ignored. See `/settings wcl ignore_list`.", name.trim())).await
    }
}

/// List the players left out of the Warcraft Logs overview
#[poise::command(prefix_command, slash_command, ephemeral, rename = "ignore_list")]
async fn wcl_ignore_list(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    let ignored = wcl_ignored_players::list(&ctx.data().db, guild_id).await?;
    if ignored.is_empty() {
        return reply(ctx, "No players are ignored. Add one with `/settings wcl ignore_add`.".to_string()).await;
    }
    reply(ctx, format!("Ignored players ({} of {}): {}", ignored.len(), wcl_ignored_players::MAX_IGNORED, ignored.join(", "))).await
}

/// Count an ability as a healthstone or a health potion in the Warcraft Logs consumables overview
///
/// Give the ability's ID as Warcraft Logs logs it: click it in a report's Casts tab. A kind can have
/// several (a plain and a demonic healthstone, two potions), and only the IDs you add are counted.
#[poise::command(prefix_command, slash_command, ephemeral, rename = "consumable_add")]
async fn wcl_consumable_add(
    ctx: Context<'_>,
    #[description = "Healthstone or health potion"] kind: Kind,
    #[description = "The ability's ID as Warcraft Logs logs it"] ability_id: u32,
) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    // The API's IDs are signed 32-bit integers, and zero is never one.
    if !(1..=i32::MAX as u32).contains(&ability_id) {
        return reply(ctx, "That isn't a valid ability ID. It's a positive whole number.".to_string()).await;
    }

    let message = match wcl_consumables::add(&ctx.data().db, guild_id, kind, ability_id).await? {
        ConsumableOutcome::Added => format!("Ability {ability_id} now counts as a **{}**.", kind.name().to_lowercase()),
        ConsumableOutcome::AlreadyRegistered => format!("Ability {ability_id} already counts as a **{}**.", kind.name().to_lowercase()),
        ConsumableOutcome::OtherKind(other) => format!(
            "Ability {ability_id} is registered as a **{}**. Remove it with `/settings wcl consumable_remove` first.",
            other.name().to_lowercase()
        ),
        ConsumableOutcome::Full => format!(
            "This server already has {} abilities registered as a {}. Remove one with `/settings wcl consumable_remove` first.",
            wcl_consumables::MAX_PER_KIND,
            kind.name().to_lowercase()
        ),
    };
    reply(ctx, message).await
}

/// Stop counting an ability in the Warcraft Logs consumables overview
#[poise::command(prefix_command, slash_command, ephemeral, rename = "consumable_remove")]
async fn wcl_consumable_remove(ctx: Context<'_>, #[description = "The ability's ID, as in wcl consumable_list"] ability_id: u32) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    match wcl_consumables::remove(&ctx.data().db, guild_id, ability_id).await? {
        Some(kind) => reply(ctx, format!("Ability {ability_id} no longer counts as a **{}**.", kind.name().to_lowercase())).await,
        None => reply(ctx, format!("Ability {ability_id} isn't registered. See `/settings wcl consumable_list`.")).await,
    }
}

/// List the abilities counted as healthstones and health potions
#[poise::command(prefix_command, slash_command, ephemeral, rename = "consumable_list")]
async fn wcl_consumable_list(ctx: Context<'_>) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    let registered = wcl_consumables::list(&ctx.data().db, guild_id).await?;
    if registered.is_empty() {
        return reply(ctx, "No abilities are registered. Add one with `/settings wcl consumable_add`.".to_string()).await;
    }

    let lines: Vec<String> = Kind::ALL
        .into_iter()
        .map(|kind| {
            let ids = wcl_consumables::ids(&registered, kind);
            let ids = if ids.is_empty() { "none".to_string() } else { ids.iter().map(u32::to_string).collect::<Vec<_>>().join(", ") };
            format!("**{}**: {ids}", kind.plural())
        })
        .collect();
    reply(ctx, lines.join("\n")).await
}

/// Everything `/settings clear` can clear: each ID setting, plus the credentials.
#[derive(Debug, Clone, Copy, poise::ChoiceParameter)]
enum ClearTarget {
    #[name = "Moderator role"]
    ModRole,
    #[name = "Raider role"]
    RaiderRole,
    #[name = "Trial role"]
    TrialRole,
    #[name = "Personal officer category"]
    PersonalOfficerCategory,
    #[name = "Raid notes channel"]
    RaidNotesChannel,
    #[name = "Raid notes schedule"]
    RaidNotesCron,
    #[name = "Raid attendance"]
    RaidVoice,
    #[name = "Bart token"]
    BartToken,
    #[name = "WowUtils credentials"]
    WowUtils,
    #[name = "Warcraft Logs channel"]
    WclChannel,
    #[name = "Warcraft Logs source"]
    WclSource,
}

/// Clear one of this server's registered settings
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn clear(ctx: Context<'_>, #[description = "Setting to clear"] setting: ClearTarget) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;
    let store = &ctx.data().guild_settings;

    // Same restriction as `mod_role`: clearing the moderator role is changing it.
    if matches!(setting, ClearTarget::ModRole) && !crate::checks::check_is_administrator(ctx).await? {
        return Ok(());
    }

    match setting {
        ClearTarget::ModRole => store.set(guild_id, Setting::ModRole, None).await?,
        ClearTarget::RaiderRole => store.set(guild_id, Setting::RaiderRole, None).await?,
        ClearTarget::TrialRole => store.set(guild_id, Setting::TrialRole, None).await?,
        ClearTarget::PersonalOfficerCategory => store.set(guild_id, Setting::PersonalOfficerCategory, None).await?,
        ClearTarget::RaidNotesChannel => store.set(guild_id, Setting::RaidNotesChannel, None).await?,
        ClearTarget::RaidNotesCron => store.set_raid_notes_cron(guild_id, None).await?,
        ClearTarget::RaidVoice => store.set_raid_voice(guild_id, None).await?,
        ClearTarget::BartToken => store.set_bart_token(guild_id, None).await?,
        ClearTarget::WowUtils => store.set_wowutils(guild_id, None).await?,
        ClearTarget::WclChannel => store.set(guild_id, Setting::WclChannel, None).await?,
        ClearTarget::WclSource => store.set_wcl_source(guild_id, None).await?,
    }
    reply(ctx, format!("{} cleared.", setting.name())).await
}

/// Registers `role_id` for `setting` after confirming, against Discord, that the role belongs to
/// the server the command was used in.
async fn register_role(ctx: Context<'_>, setting: Setting, role_id: RoleId) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    if role_id == guild_id.everyone_role() {
        return reply(ctx, "`@everyone` can't be registered; pick a specific role.".to_string()).await;
    }
    if !guild_id.roles(ctx.http()).await?.contains_key(&role_id) {
        return reply(ctx, "That role doesn't belong to this server, so it can't be registered here.".to_string()).await;
    }

    ctx.data().guild_settings.set(guild_id, setting, Some(role_id.get())).await?;
    reply(ctx, format!("{} set to <@&{role_id}>.", setting.name())).await
}

/// Registers `channel_id` for `setting` after confirming, against Discord, that the channel
/// belongs to the server the command was used in and is one of the `allowed` types.
async fn register_channel(
    ctx: Context<'_>,
    setting: Setting,
    channel_id: ChannelId,
    allowed: &[ChannelType],
    expected: &str,
) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    if let Some(problem) = channel_problem(ctx, guild_id, channel_id, allowed, expected).await? {
        return reply(ctx, problem).await;
    }

    ctx.data().guild_settings.set(guild_id, setting, Some(channel_id.get())).await?;
    reply(ctx, format!("{} set to <#{channel_id}>.", setting.name())).await
}

/// Returns why `channel_id` can't be registered in `guild_id`, or `None` if it can. The channel
/// list is fetched from Discord rather than trusted from the command arguments, since a prefix
/// command can be handed any ID.
pub(super) async fn channel_problem(
    ctx: Context<'_>,
    guild_id: GuildId,
    channel_id: ChannelId,
    allowed: &[ChannelType],
    expected: &str,
) -> Result<Option<String>, Error> {
    let channels = guild_id.channels(ctx.http()).await?;

    Ok(match channels.get(&channel_id) {
        None => Some("That channel doesn't belong to this server, so it can't be registered here.".to_string()),
        Some(channel) if !allowed.contains(&channel.kind) => Some(format!("<#{channel_id}> must be {expected}.")),
        Some(_) => None,
    })
}

fn require_guild(ctx: Context<'_>) -> Result<GuildId, Error> {
    ctx.guild_id().ok_or_else(|| "Settings can only be changed in a server.".into())
}

/// Replies without pinging anyone: these messages mention the roles and users being registered,
/// and a prefix-command reply is a normal message that would otherwise notify them.
async fn reply(ctx: Context<'_>, content: String) -> Result<(), Error> {
    ctx.send(CreateReply::default().content(content).allowed_mentions(CreateAllowedMentions::new())).await?;
    Ok(())
}
