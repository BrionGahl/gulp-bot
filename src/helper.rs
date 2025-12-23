use poise::serenity_prelude::{Colour, CreateEmbed, CreateEmbedAuthor, CreateEmbedFooter};
use crate::types::bot::Context;

pub fn create_base_embed(ctx: &Context<'_>) -> CreateEmbed {
    CreateEmbed::default()
        .author(CreateEmbedAuthor::new(&ctx.data().config.bot_name))
        .footer(CreateEmbedFooter::new("All rights reserved to Kail"))
        .colour(Colour::from(0xAF69EE))
}