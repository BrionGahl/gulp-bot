mod commands;
mod types;
mod config;
mod checks;
mod helper;

use poise::serenity_prelude::{self as serenity, GatewayIntents, GuildId, RoleId};

use std::env;
use std::sync::Arc;
use std::time::Duration;
use log::info;
use crate::config::Config;
use crate::types::bot::{Error, Data, Context};

#[tokio::main]
async fn main() {
    // Logging tracer
    tracing_subscriber::fmt::init();

    let data = Data::new();
    let token = data.config.discord_token.clone();

    let intents = GatewayIntents::non_privileged()
        | GatewayIntents::MESSAGE_CONTENT
        | GatewayIntents::GUILD_MEMBERS
        | GatewayIntents::GUILDS;

    let prefix = poise::PrefixFrameworkOptions {
        prefix: Some(String::from("?")),
        additional_prefixes: vec![
            poise::Prefix::Regex(
                "(yo |hey )?(mommy|kail),? can you (please |pwease )?"
                    .parse()
                    .unwrap(),
            )
        ],
        edit_tracker: Some(Arc::new(poise::EditTracker::for_timespan(
            Duration::from_secs(60 * 5), // 5 minutes
        ))),
        ..Default::default()
    };

    let options = poise::FrameworkOptions {
        commands: vec![
            commands::wow_guild::get_upcoming_raids(),
            commands::wow_guild::get_upcoming_absences(),
            commands::wow_guild::get_liquid_info(),
            commands::wow_guild::get_better_resources(),
            commands::wow_guild::class_discords(),
            commands::utilities::source(),
            commands::utilities::help(),
            commands::utilities::register(),
            commands::gambling::roll(),
            commands::gambling::start_gambling_game(),
        ],
        // Call to the event handler
        event_handler: |ctx, event, framework, data| {
            Box::pin(event_handler(ctx, event, framework, data))
        },
        // Config for the prefix
        prefix_options: prefix,
        // This code is run before every command
        pre_command: |ctx| {
            Box::pin(async move {
                let channel_name = &ctx
                    .channel_id()
                    .name(&ctx)
                    .await
                    .unwrap_or_else(|_| "<unknown>".to_owned());
                let author = &ctx.author().name;
                info!("{} in {} used slash command '{}'", author, channel_name, &ctx.invoked_command_name());
            })
        },
        // This code is run after a command if it was successful (returned Ok)
        post_command: |ctx| {
            Box::pin(async move {
                info!("Executed command {}!", ctx.command().qualified_name);
            })
        },
        // Every command invocation must pass this check to continue execution
        command_check: Some(|_ctx| Box::pin(async move { Ok(true) })),
        skip_checks_for_owners: false,
        ..Default::default()
    };

    let framework = poise::Framework::builder()
        .setup(move |ctx, _ready, framework| {
            Box::pin(async move {
                info!("Logged in as {}", _ready.user.name);
                poise::builtins::register_globally(ctx, &framework.options().commands).await?;
                Ok(data)
            })
        })
        .options(options)
        .build();

    let client = serenity::ClientBuilder::new(token, intents)
        .framework(framework)
        .await;

    client.unwrap().start().await.unwrap()
}

async fn event_handler(
    ctx: &serenity::Context,
    event: &serenity::FullEvent,
    _framework: poise::FrameworkContext<'_, Data, Error>,
    data: &Data,
) -> Result<(), Error> {
    // List of all events we will handle
    match event {
        serenity::FullEvent::Ready { data_about_bot, .. } => {
        }
        _ => {}
    }
    Ok(())
}
