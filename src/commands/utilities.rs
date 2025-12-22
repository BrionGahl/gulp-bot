use crate::types::bot::{Context, Error};

// Links to the bot GitHub repo
#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "Utilities",
    discard_spare_arguments
)]
pub async fn source(ctx: Context<'_>) -> Result<(), Error> {
    ctx.say("TODO")
        .await?;
    Ok(())
}

#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "Utilities",
    discard_spare_arguments
)]
pub async fn help(ctx: Context<'_>) -> Result<(), Error> {
    ctx.say("TODO")
        .await?;
    Ok(())
}

// Register slash commands in this guild or globally
#[poise::command(
    prefix_command,
    slash_command,
    category = "Utilities",
    hide_in_help,
)]
pub async fn register(ctx: Context<'_>) -> Result<(), Error> {
    poise::builtins::register_application_commands_buttons(ctx).await?;
    Ok(())
}