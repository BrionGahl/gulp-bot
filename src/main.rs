mod commands;
mod types;
mod config;
mod checks;
mod helper;
mod clips;
mod health;
mod raid_notes;
mod personal_officer_channels;
mod announcements;
mod mentions;
mod message_replay;
mod guild_settings;
mod schedules;
mod raid_attendance;

use poise::serenity_prelude::{self as serenity, GatewayIntents};
use tracing_subscriber::prelude::*;

use std::sync::Arc;
use std::time::Duration;
use log::info;
use crate::config::Config;
use crate::guild_settings::GuildSettingsStore;
use crate::types::bot::{Error, Data};

#[tokio::main]
async fn main() {
    let config = Config::from_env();

    tracing_subscriber::registry()
        .with(config.log_level)
        .with(tracing_stackdriver::layer())
        .init();

    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(5)
        .connect(&config.database_url)
        .await
        .expect("Failed to connect to the database at `DATABASE_URL`.");
    sqlx::migrate!()
        .run(&pool)
        .await
        .expect("Failed to run database migrations.");
    let guild_settings = Arc::new(
        GuildSettingsStore::load(pool.clone())
            .await
            .expect("Failed to load guild settings from the database."),
    );

    let data = Data::new(config, pool.clone(), guild_settings.clone());
    let token = data.config.discord_token.clone();

    let intents = GatewayIntents::non_privileged()
        | GatewayIntents::MESSAGE_CONTENT
        | GatewayIntents::GUILD_MEMBERS
        | GatewayIntents::GUILDS;

    let prefix = poise::PrefixFrameworkOptions {
        prefix: Some("?".to_string()),
        edit_tracker: Some(Arc::new(poise::EditTracker::for_timespan(
            Duration::from_secs(60 * 5), // 5 minutes
        ))),
        ..Default::default()
    };

    let options = poise::FrameworkOptions {
        commands: vec![
            commands::wow_guild::get_timeline_reminders(),
            commands::wow_guild::class_discords(),
            commands::wow_utils::submit_droptimizer(),
            commands::utilities::source(),
            commands::utilities::help(),
            commands::utilities::register(),
            commands::mod_debug::test_raid_notes(),
            commands::mod_debug::test_raid_attendance(),
            commands::mod_debug::test_replay(),
            commands::settings::settings(),
            commands::announcement::announcement(),
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

    tokio::spawn(health::serve());
    tokio::spawn(raid_notes::schedule_weekly_posts(client.http.clone(), pool.clone(), guild_settings.clone()));
    tokio::spawn(raid_attendance::schedule_snapshots(client.http.clone(), client.cache.clone(), pool.clone(), guild_settings.clone()));
    tokio::spawn(announcements::schedule_announcements(client.http.clone(), pool, guild_settings));

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
        serenity::FullEvent::Message { new_message } => {
            clips::enforce_clips_channel(ctx, data, new_message).await;
            message_replay::remember_message(data, new_message).await;
        }
        serenity::FullEvent::MessageDelete { deleted_message_id, guild_id, .. } => {
            message_replay::handle_message_delete(ctx, data, *deleted_message_id, *guild_id).await;
        }
        serenity::FullEvent::MessageDeleteBulk { multiple_deleted_messages_ids, .. } => {
            message_replay::forget_messages(data, multiple_deleted_messages_ids).await;
        }
        serenity::FullEvent::GuildMemberUpdate { new, .. } => {
            personal_officer_channels::handle_role_update(ctx, data, new).await;
        }
        _ => {}
    }
    Ok(())
}
