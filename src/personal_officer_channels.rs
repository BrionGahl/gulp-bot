use log::{error, info};
use poise::serenity_prelude::{
    ChannelType, Context, CreateChannel, Member, Permissions, PermissionOverwrite,
    PermissionOverwriteType, UserId,
};

use crate::types::bot::Data;

const CHANNEL_TOPIC_PREFIX: &str = "Personal officer channel for";

/// If `new` just gained the trial or raider role (relative to `old_if_available`), creates a
/// private text channel for them under the personal officer channels category, visible only to
/// them and the officer role. No-ops if they already have one.
pub async fn handle_role_update(ctx: &Context, data: &Data, old_if_available: &Option<Member>, new: &Option<Member>) {
    let Some(new_member) = new else { return };

    let had_role_before = old_if_available
        .as_ref()
        .is_some_and(|old| has_trial_or_raider_role(data, old));
    let has_role_now = has_trial_or_raider_role(data, new_member);

    if had_role_before || !has_role_now {
        return;
    }

    let category_id = data.config.personal_officer_category_id;
    let user_id = new_member.user.id;

    let channels = match new_member.guild_id.channels(&ctx.http).await {
        Ok(channels) => channels,
        Err(e) => {
            error!("Failed to list guild channels while checking for an existing personal officer channel for {}: {}", new_member.user.name, e);
            return;
        }
    };

    let topic_marker = topic_marker_for(user_id);
    let already_has_channel = channels
        .values()
        .any(|c| c.parent_id == Some(category_id) && c.topic.as_deref().is_some_and(|t| t.contains(&topic_marker)));

    if already_has_channel {
        return;
    }

    let everyone_role = new_member.guild_id.everyone_role();
    let officer_role = data.config.mod_role_id;

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
