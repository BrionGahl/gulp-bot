mod commands;
mod types;
mod config;
mod checks;
mod helper;
mod twitter;

use poise::serenity_prelude::{self as serenity, GatewayIntents};
use tracing_subscriber::prelude::*;

use std::env;
use std::sync::Arc;
use std::time::Duration;
use log::info;
use crate::config::Config;
use crate::types::bot::{Error, Data, Context};

#[tokio::main]
async fn main() {
    let data = Data::new();

    tracing_subscriber::registry()
        .with(data.config.log_level)
        .with(tracing_stackdriver::layer())
        .init();
    let token = data.config.discord_token.clone();

    let poll_http = data.http_client.clone();
    let nitter_base_url = data.config.nitter_base_url.clone();
    let x_base_url = data.config.x_base_url.clone();
    let usernames = data.config.twitter_user_ids.clone();
    let channel_id = data.config.tweet_target_channel_id;
    let poll_time = data.config.tweet_poll_time;

    let intents = GatewayIntents::non_privileged()
        | GatewayIntents::MESSAGE_CONTENT
        | GatewayIntents::GUILD_MEMBERS
        | GatewayIntents::GUILDS;

    let prefix = poise::PrefixFrameworkOptions {
        prefix: Some("?".to_string()),
        additional_prefixes: vec![
            poise::Prefix::Regex(
                "(yo |hey )? kail, can you (please |pwease )?"
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
            commands::wow_guild::class_discords(),
            commands::utilities::source(),
            commands::utilities::help(),
            commands::utilities::register(),
            commands::gambling::roll(),
            commands::gambling::gamble(),
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

    let mut client = serenity::ClientBuilder::new(token, intents)
        .framework(framework)
        .await
        .unwrap();

    let serenity_http = Arc::clone(&client.http);
    tokio::spawn(twitter::poll_task(serenity_http, poll_http, nitter_base_url, x_base_url, usernames, channel_id, poll_time));

    client.start().await.unwrap()
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
