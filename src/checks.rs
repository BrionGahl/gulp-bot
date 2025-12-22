use poise::serenity_prelude::RoleId;
use crate::types::bot::{Context, Error};

pub fn is_moderator(ctx: Context<'_>) -> bool {
    let mod_role_id = &ctx.data().config.mod_role_id;
    match ctx {
        Context::Application(app_context) => {
            // Triggered outside guild, i.e if its not a member, it is outside guild and cannot be a moderator
            let Some(member) = &app_context.interaction.member else {
                return false;
            };
            member.roles.contains(mod_role_id)
        }
        Context::Prefix(msg_context) => {
            // Triggered outside MessageCreateEvent
            let Some(member) = &msg_context.msg.member else {
                return false;
            };
            member.roles.contains(mod_role_id)
        }
    }
}

pub async fn check_is_moderator(ctx: Context<'_>) -> Result<bool, Error> {
    let user_is_moderator = is_moderator(ctx);
    if !user_is_moderator {
        ctx.send(
            poise::CreateReply::default()
                .content("This command is only available to moderators.")
                .ephemeral(true),
        ).await?;
    }
    Ok(user_is_moderator)
}