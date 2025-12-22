use poise::CreateReply;
use poise::serenity_prelude::{Colour, CreateEmbed, CreateEmbedAuthor, CreateEmbedFooter};
use rand::Rng;
use crate::types::bot::{Context, Error};

#[poise::command(
    prefix_command,
    slash_command,
    category = "Gambling",
    discard_spare_arguments
)]
pub async fn roll(ctx: Context<'_>, #[description = "Max number that can be rolled"] #[min = 2] max_roll: Option<u32>) -> Result<(), Error> {
    let max_roll = max_roll.unwrap_or(100);

    let roll = {
        // Never store a ThreadRng across a function that lives to an await
        let mut rng = rand::rng();
        rng.random_range(1..max_roll)
    };

    let embed = CreateEmbed::default()
        .author(CreateEmbedAuthor::new(&ctx.data().config.bot_name))
        .title(format!("{} has rolled!", &ctx.author().name))
        .footer(CreateEmbedFooter::new(
            "All rights reserved to Kail-Area52",
        ))
        .colour(Colour::from_rgb(255, 0, 255))
        .field("", format!("{}", roll), false);
    ctx.send(CreateReply::default().embed(embed)).await?;
    ctx.
    Ok(())
}

#[poise::command(
    prefix_command,
    slash_command,
    category = "Gambling",
    discard_spare_arguments
)]
pub async fn start_death_roll(ctx: Context<'_>, #[description = "Max number that can be rolled"] #[min = 2] max_roll: Option<u32>) -> Result<(), Error> {
    let max_roll = max_roll.unwrap_or(100);

    // TODO: Need to find a way to implement this.

    let embed = CreateEmbed::default()
        .author(CreateEmbedAuthor::new(&ctx.data().config.bot_name))
        .title(format!("{} has started a new death roll!", ctx.author().name))
        .footer(CreateEmbedFooter::new(
            "All rights reserved to Kail-Area52",
        ))
        .colour(Colour::from_rgb(255, 0, 255))
        .field(format!("Starting roll is {}", max_roll), "React below to enter!", true);
    ctx.send(CreateReply::default().embed(embed)).await?;

    Ok(())
}