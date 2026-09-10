mod commands;
mod types;
mod config;
mod checks;
mod helper;
mod clips;
mod health;
mod raid_notes;
#[allow(dead_code)] // feature disabled in the event handler; kept for easy re-enable
mod personal_officer_channels;
mod announcements;
mod mentions;
mod message_replay;

use poise::serenity_prelude::{self as serenity, GatewayIntents};
use tracing_subscriber::prelude::*;

use std::sync::Arc;
use std::time::Duration;
use log::info;
use crate::types::bot::{Error, Data};

#[tokio::main]
async fn main() {
    let data = Data::new();
    let raid_notes_channel_id = data.config.raid_notes_channel_id;
    let raid_notes_cron = data.config.raid_notes_cron.clone();
    let announcement_channel_id = data.config.announcement_channel_id;
    let raider_role_id = data.config.raider_role_id;
    let trial_role_id = data.config.trial_role_id;

    tracing_subscriber::registry()
        .with(data.config.log_level)
        .with(tracing_stackdriver::layer())
        .init();
    let token = data.config.discord_token.clone();

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
            commands::wow_guild::get_liquid_info(),
            commands::wow_guild::class_discords(),
            commands::wow_utils::submit_droptimizer(),
            commands::utilities::source(),
            commands::utilities::help(),
            commands::utilities::register(),
            commands::gambling::roll(),
            commands::gambling::gamble(),
            commands::mod_debug::test_announcement(),
            commands::mod_debug::test_raid_notes(),
            commands::mod_debug::test_replay(),
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
    tokio::spawn(raid_notes::schedule_weekly_posts(client.http.clone(), raid_notes_channel_id, raid_notes_cron));

    if let Some(channel_id) = announcement_channel_id {
        tokio::spawn(announcements::schedule_weekly_announcement(
            client.http.clone(),
            channel_id,
            raider_role_id,
            trial_role_id,
        ));
    } else {
        info!("ANNOUNCEMENT_CHANNEL_ID not set; skipping weekly Tuesday noon announcement.");
    }

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
            // Personal officer channel creation is disabled.
            // personal_officer_channels::handle_role_update(ctx, data, new).await;
            let _ = new;
        }
        _ => {}
    }
    Ok(())
}
