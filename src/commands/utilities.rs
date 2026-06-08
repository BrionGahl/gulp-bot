use crate::types::bot::{Context, Error};

/// Links to the bot GitHub repo
#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "Utilities",
)]
pub async fn source(ctx: Context<'_>) -> Result<(), Error> {
    ctx.say("https://github.com/BrionGahl/gulp-bot")
        .await?;
    Ok(())
}

/// View all commands
#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "Utilities",
)]
pub async fn help(ctx: Context<'_>) -> Result<(), Error> {
    ctx.say("TODO")
        .await?;
    Ok(())
}

/// Register slash commands in this guild or globally
#[poise::command(
    prefix_command,
    slash_command,
    category = "Utilities",
    hide_in_help,
    ephemeral,
    check = "crate::checks::check_is_moderator"
)]
pub async fn register(ctx: Context<'_>) -> Result<(), Error> {
    poise::builtins::register_application_commands_buttons(ctx).await?;
    Ok(())
}