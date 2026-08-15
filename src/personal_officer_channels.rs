use log::{error, info};
use poise::serenity_prelude::{
    ChannelType, Context, CreateChannel, Member, Permissions, PermissionOverwrite,
    PermissionOverwriteType, UserId,
};

use crate::types::bot::Data;

const CHANNEL_TOPIC_PREFIX: &str = "Personal officer channel for";

/// If `new` currently has the trial or raider role (and isn't themselves an officer), creates a
/// private text channel for them under the personal officer channels category, visible only to
/// them and the officer role. No-ops if they already have one.
///
/// Deliberately ignores `old_if_available` rather than gating on a before/after role diff:
/// serenity's cache only has "old" member state when the member was already cached, which isn't
/// reliable for a guild this size (no member chunk request on startup), so that diff produced
/// false positives on unrelated member updates (nickname, avatar, etc.) after every restart.
/// Idempotency comes entirely from the already-has-channel check below.
pub async fn handle_role_update(ctx: &Context, data: &Data, new: &Option<Member>) {
    let Some(new_member) = new else { return };

    if !has_trial_or_raider_role(data, new_member) {
        return;
    }

    let category_id = data.config.personal_officer_category_id;
    let user_id = new_member.user.id;
    let officer_role = data.config.mod_role_id;

    if new_member.roles.contains(&officer_role) {
        return;
    }

    // Holds for the whole check-then-create sequence below so that two events for the same
    // member firing close together can't both see "no channel yet" and both create one.
    let _lock = data.personal_officer_channel_lock.lock().await;

    let channels = match new_member.guild_id.channels(&ctx.http).await {
        Ok(channels) => channels,
        Err(e) => {
            error!("Failed to list guild channels while checking for an existing personal officer channel for {}: {}", new_member.user.name, e);
            return;
        }
    };

    // Matches on permission overwrites rather than the topic marker so that channels created
    // manually (before this automation existed, or by an officer directly) are still recognized
    // as this member's existing channel — as long as they're set up the same way a personal
    // officer channel should be: under the category, with both the officer role and the member
    // individually overwritten in.
    let already_has_channel = channels.values().any(|c| {
        c.parent_id == Some(category_id)
            && c.permission_overwrites.iter().any(|o| o.kind == PermissionOverwriteType::Member(user_id))
            && c.permission_overwrites.iter().any(|o| o.kind == PermissionOverwriteType::Role(officer_role))
    });

    if already_has_channel {
        return;
    }

    let everyone_role = new_member.guild_id.everyone_role();
    let topic_marker = topic_marker_for(user_id);

    let permissions = vec![
        PermissionOverwrite {
            allow: Permissions::empty(),
            deny: Permissions::VIEW_CHANNEL,
            kind: PermissionOverwriteType::Role(everyone_role),
        },
        PermissionOverwrite {
            allow: Permissions::VIEW_CHANNEL | Permissions::SEND_MESSAGES | Permissions::READ_MESSAGE_HISTORY,
            deny: Permissions::empty(),
            kind: PermissionOverwriteType::Role(officer_role),
        },
        PermissionOverwrite {
            allow: Permissions::VIEW_CHANNEL | Permissions::SEND_MESSAGES | Permissions::READ_MESSAGE_HISTORY,
            deny: Permissions::empty(),
            kind: PermissionOverwriteType::Member(user_id),
        },
    ];

    let builder = CreateChannel::new(channel_name_for(new_member))
        .kind(ChannelType::Text)
        .category(category_id)
        .topic(format!("{topic_marker} — visible only to them and officers."))
        .permissions(permissions);

    match new_member.guild_id.create_channel(&ctx.http, builder).await {
        Ok(channel) => info!("Created personal officer channel #{} for {}", channel.name, new_member.user.name),
        Err(e) => error!("Failed to create personal officer channel for {}: {}", new_member.user.name, e),
    }
}

fn has_trial_or_raider_role(data: &Data, member: &Member) -> bool {
    member.roles.contains(&data.config.trial_role_id) || member.roles.contains(&data.config.raider_role_id)
}

fn topic_marker_for(user_id: UserId) -> String {
    format!("{CHANNEL_TOPIC_PREFIX} <@{user_id}>")
}

fn channel_name_for(member: &Member) -> String {
    let base = member.nick.as_deref().unwrap_or(&member.user.name);
    let sanitized: String = base
        .to_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
        .collect();
    let trimmed = sanitized.trim_matches('-');
    let trimmed = if trimmed.is_empty() { "member" } else { trimmed };

    format!("{trimmed}")
}
