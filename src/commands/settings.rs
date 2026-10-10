use poise::serenity_prelude::{ChannelId, ChannelType, CreateAllowedMentions, GuildId, RoleId, UserId};
use poise::{ChoiceParameter, CreateReply};

use crate::guild_settings::Setting;
use crate::personal_officer_channels;
use crate::raid_notes;
use crate::schedules;
use crate::types::bot::{Context, Error};

const TEXT_CHANNEL_TYPES: &[ChannelType] = &[ChannelType::Text, ChannelType::News];

/// Cap on the `officer_channel_list` embed description, under Discord's 4096-character limit.
const MAX_LIST_CHARS: usize = 4000;

/// View or change what this server has registered with the bot. Administrators and moderators.
///
/// The check and `guild_only` are set here once: poise applies a parent command's checks to all
/// of its subcommands. There is deliberately no `default_member_permissions`: Discord would hide
/// the command from moderators who aren't administrators.
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
        "replay_user",
        "clips_add",
        "clips_remove",
        "bart_token",
        "wowutils",
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
    let user = |id: Option<UserId>| id.map_or("Not set".to_string(), |id| format!("<@{id}>"));

    let mut clips: Vec<ChannelId> = settings.clips_channel_ids.iter().copied().collect();
    clips.sort();
    let clips = if clips.is_empty() {
        "None".to_string()
    } else {
        clips.iter().map(|id| format!("<#{id}>")).collect::<Vec<_>>().join(", ")
    };

    let raid_notes_schedule = match &settings.raid_notes_cron {
        Some(schedule) => format!("`{}`", schedule.source()),
        None => format!("Default (`{}`)", raid_notes::DEFAULT_RAID_NOTES_CRON),
    };

    let embed = crate::helper::create_base_embed(&ctx)
        .title("Server Settings")
        .field(Setting::ModRole.name(), role(settings.mod_role_id), true)
        .field(Setting::RaiderRole.name(), role(settings.raider_role_id), true)
        .field(Setting::TrialRole.name(), role(settings.trial_role_id), true)
        .field(Setting::PersonalOfficerCategory.name(), channel(settings.personal_officer_category_id), true)
        .field(Setting::RaidNotesChannel.name(), channel(settings.raid_notes_channel_id), true)
        .field(ClearTarget::RaidNotesCron.name(), raid_notes_schedule, true)
        .field(Setting::ReplayUser.name(), user(settings.replay_user_id), true)
        .field("Clips channels", clips, false)
        // Credentials are never echoed back, only whether they're registered.
        .field(ClearTarget::BartToken.name(), if settings.bart_token.is_some() { "Set" } else { "Not set" }, true)
        .field(ClearTarget::WowUtils.name(), if settings.wowutils_token.is_some() { "Set" } else { "Not set" }, true);

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

/// Register the member whose self-deleted messages the bot reposts
#[poise::command(prefix_command, slash_command, ephemeral)]
async fn replay_user(ctx: Context<'_>, #[description = "Member to replay deleted messages for"] user: UserId) -> Result<(), Error> {
    let guild_id = require_guild(ctx)?;

    if guild_id.member(ctx, user).await.is_err() {
        return reply(ctx, format!("<@{user}> isn't a member of this server, so they can't be registered here.")).await;
    }

    ctx.data().guild_settings.set(guild_id, Setting::ReplayUser, Some(user.get())).await?;
    reply(ctx, format!("{} set to <@{user}>.", Setting::ReplayUser.name())).await
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
/// Slash-only, like `wowutils`: a prefix command is an ordinary chat message, which would leave
/// the token readable in the channel.
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
    #[name = "Replay user"]
    ReplayUser,
    #[name = "Bart token"]
    BartToken,
    #[name = "WowUtils credentials"]
    WowUtils,
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
        ClearTarget::ReplayUser => store.set(guild_id, Setting::ReplayUser, None).await?,
        ClearTarget::RaidNotesCron => store.set_raid_notes_cron(guild_id, None).await?,
        ClearTarget::BartToken => store.set_bart_token(guild_id, None).await?,
        ClearTarget::WowUtils => store.set_wowutils(guild_id, None).await?,
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
