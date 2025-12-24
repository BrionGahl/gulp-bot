use poise::serenity_prelude::RoleId;
use crate::types::bot::{Context, Error};

pub async fn check_is_moderator(ctx: Context<'_>) -> Result<bool, Error> {
    let mod_role_id = &ctx.data().config.mod_role_id;
    let user_is_moderator = is_some_role(ctx, mod_role_id);
    if !user_is_moderator {
        ctx.send(
            poise::CreateReply::default()
                .content("This command is only available to moderators.")
                .ephemeral(true),
        ).await?;
    }
    Ok(user_is_moderator)
}

pub async fn check_is_raider(ctx: Context<'_>) -> Result<bool, Error> {
    let raider_role_id = &ctx.data().config.raider_role_id;
    let user_is_raider = is_some_role(ctx, raider_role_id);
    if !user_is_raider {
        ctx.send(
            poise::CreateReply::default()
                .content("This command is only available to raiders.")
                .ephemeral(true),
        ).await?;
    }
    Ok(user_is_raider)
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