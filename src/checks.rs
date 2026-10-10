use poise::serenity_prelude::{GuildId, RoleId};
use crate::types::bot::{Context, Error};

pub async fn check_is_moderator(ctx: Context<'_>) -> Result<bool, Error> {
    let Some(guild_id) = ctx.guild_id() else {
        reply_denied(ctx, "This command is only available in a server.").await?;
        return Ok(false);
    };
    // Looked up by the guild the command was used in, so a role registered for another server
    // never grants access here.
    let Some(mod_role_id) = ctx.data().guild_settings.get(guild_id).mod_role_id else {
        reply_denied(ctx, "No moderator role is registered for this server. An administrator can set one with `/settings mod_role`.").await?;
        return Ok(false);
    };

    let user_is_moderator = is_some_role(ctx, &mod_role_id);
    if !user_is_moderator {
        reply_denied(ctx, "This command is only available to moderators.").await?;
    }
    Ok(user_is_moderator)
}

pub async fn check_is_raider(ctx: Context<'_>) -> Result<bool, Error> {
    let Some(guild_id) = ctx.guild_id() else {
        reply_denied(ctx, "This command is only available in a server.").await?;
        return Ok(false);
    };
    let settings = ctx.data().guild_settings.get(guild_id);
    let allowed_roles = [settings.raider_role_id, settings.mod_role_id, settings.trial_role_id];
    if allowed_roles.iter().all(Option::is_none) {
        reply_denied(ctx, "No raider roles are registered for this server. An administrator or moderator can set them with `/settings`.").await?;
        return Ok(false);
    }

    let user_is_raider = allowed_roles.iter().flatten().any(|role_id| is_some_role(ctx, role_id));
    if !user_is_raider {
        reply_denied(ctx, "This command is only available to raiders.").await?;
    }
    Ok(user_is_raider)
}

/// Gate for the `/settings` commands: server administrators, plus holders of the registered
/// moderator role. Administrators are always allowed so that a server with no moderator role
/// registered yet (or one registered wrongly) can still be configured.
pub async fn check_is_admin_or_moderator(ctx: Context<'_>) -> Result<bool, Error> {
    let Some(guild_id) = ctx.guild_id() else {
        reply_denied(ctx, "This command is only available in a server.").await?;
        return Ok(false);
    };

    let allowed = is_admin_or_moderator(ctx, guild_id);
    if !allowed {
        reply_denied(ctx, "This command is only available to administrators and moderators.").await?;
    }
    Ok(allowed)
}

/// Whether the invoking member is an administrator or holds `guild_id`'s registered moderator role.
/// Unlike the checks above this never replies, so it can gate things a check can't, such as a
/// command's autocomplete (poise doesn't run a command's checks for those).
pub fn is_admin_or_moderator(ctx: Context<'_>, guild_id: GuildId) -> bool {
    let mod_role_id = ctx.data().guild_settings.get(guild_id).mod_role_id;
    is_administrator(ctx) || mod_role_id.is_some_and(|role_id| is_some_role(ctx, &role_id))
}

/// Gate for changing which role is the moderator role. Moderators can use the rest of
/// `/settings`, but if they could also reassign the moderator role they could hand `/settings`
/// access to anyone, or lock the other moderators out.
pub async fn check_is_administrator(ctx: Context<'_>) -> Result<bool, Error> {
    let allowed = is_administrator(ctx);
    if !allowed {
        reply_denied(ctx, "Only administrators can change the moderator role.").await?;
    }
    Ok(allowed)
}

/// Whether the invoking member has the Administrator permission in the guild the command was
/// used in (the guild owner always does).
pub fn is_administrator(ctx: Context<'_>) -> bool {
    match ctx {
        // Discord sends the member's resolved permissions with every interaction.
        Context::Application(app_context) => app_context
            .interaction
            .member
            .as_ref()
            .and_then(|member| member.permissions)
            .is_some_and(|permissions| permissions.administrator()),
        // A message carries only the member's role IDs, so resolve them against the cached guild.
        // If the guild isn't cached this is `false`, which errs on the side of denying.
        Context::Prefix(msg_context) => {
            let Some(guild) = msg_context.msg.guild(&msg_context.serenity_context().cache) else {
                return false;
            };
            if guild.owner_id == msg_context.msg.author.id {
                return true;
            }
            let Some(member) = &msg_context.msg.member else {
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
    }
}

async fn reply_denied(ctx: Context<'_>, content: &str) -> Result<(), Error> {
    ctx.send(
        poise::CreateReply::default()
            .content(content)
            .ephemeral(true),
    ).await?;
    Ok(())
}

fn is_some_role(ctx: Context<'_>, role_id: &RoleId) -> bool {
    match ctx {
        Context::Application(app_context) => {
            // Triggered outside guild, i.e if its not a member, it is outside guild and cannot be a moderator
            let Some(member) = &app_context.interaction.member else {
                return false;
            };
            member.roles.contains(role_id)
        }
        Context::Prefix(msg_context) => {
            // Triggered outside MessageCreateEvent
            let Some(member) = &msg_context.msg.member else {
                return false;
            };
            member.roles.contains(role_id)
        }
    }
}