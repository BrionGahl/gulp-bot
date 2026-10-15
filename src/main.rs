mod ai;
mod commands;
mod get_them;
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
mod warcraftlogs;
mod wcl_mechanics;
mod wcl_ignored_players;
mod wcl_consumables;
mod wcl_raid_report;

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
    let warcraft_logs = data.warcraft_logs.clone();

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
            commands::wcl::wcl(),
            commands::ask::ask(),
            commands::ai::ai(),
            commands::tldr::tldr(),
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
    tokio::spawn(raid_attendance::schedule_snapshots(client.http.clone(), client.cache.clone(), pool.clone(), guild_settings.clone(), warcraft_logs));
    tokio::spawn(announcements::schedule_announcements(client.http.clone(), pool, guild_settings));

    // The bot is PID 1 in its container, and the kernel only delivers a signal to PID 1 if the
    // process handles it: without this, `docker stop` waits out its timeout and then kills the
    // bot (exit code 137) instead of letting it close its Discord connection.
    let shard_manager = client.shard_manager.clone();
    tokio::select! {
        result = client.start() => result.unwrap(),
        () = shutdown_signal() => {
            info!("Shutdown signal received, closing the Discord connection.");
            shard_manager.shutdown_all().await;
        }
    }
}

/// Resolves when the process is asked to stop: SIGTERM (`docker stop`) or SIGINT (Ctrl-C).
#[cfg(unix)]
async fn shutdown_signal() {
    let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).expect("Failed to listen for SIGTERM.");
    tokio::select! {
        _ = terminate.recv() => {}
        _ = tokio::signal::ctrl_c() => {}
    }
}

/// Resolves when the process is asked to stop. There is no SIGTERM outside Unix (the bot runs on
/// Linux in Docker), so Ctrl-C is the only signal here; this lets it compile and run on Windows.
#[cfg(not(unix))]
async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
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
            // "bot get them" is a command, not a question for the assistant.
            if !get_them::handle(ctx, data, new_message).await {
                ai::answer_trigger(ctx, data, new_message).await;
            }
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
