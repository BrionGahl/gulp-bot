use log::error;
use poise::CreateReply;

use crate::guild_settings::WclSource;
use crate::types::bot::{Context, Error};
use crate::warcraftlogs;

/// Warcraft Logs overviews of raid nights. Officers only, and only in the Warcraft Logs channel.
///
/// Results are posted in the channel registered with `/settings wcl_channel`; the commands refuse
/// to run anywhere else.
//
// The check and `guild_only` are set here once: poise applies a parent command's checks to all of
// its subcommands. (Kept out of the doc comment, which poise shows to users in `/help wcl`.)
#[poise::command(
    slash_command,
    guild_only,
    category = "Warcraft Logs",
    check = "crate::checks::check_is_moderator",
    subcommands("report"),
    subcommand_required,
)]
pub async fn wcl(_ctx: Context<'_>) -> Result<(), Error> {
    Ok(())
}

/// Post the overview of a raid night's report: pulls, who died the most, who died first
///
/// Only the first two deaths in each pull are counted, since the rest is the raid wiping. Only
/// Mythic boss pulls are included. Without a report, the newest report from the uploader
/// registered with `/settings wcl_source` is used.
#[poise::command(slash_command)]
async fn report(
    ctx: Context<'_>,
    #[description = "Report link or code; defaults to the newest report from the registered uploader"] report: Option<String>,
) -> Result<(), Error> {
    // `check_is_moderator` has already established this is a guild.
    let Some(guild_id) = ctx.guild_id() else { return Ok(()) };
    let settings = ctx.data().guild_settings.get(guild_id);

    // Everything that can be checked without the network is checked before deferring, so that a
    // refusal is private to the officer who ran the command instead of a public follow-up.
    let Some(channel_id) = settings.wcl_channel_id else {
        return refuse(ctx, "No Warcraft Logs channel is registered for this server. An administrator or moderator can set one with `/settings wcl_channel`.".to_string()).await;
    };
    if ctx.channel_id() != channel_id {
        return refuse(ctx, format!("Warcraft Logs results are only posted in <#{channel_id}>. Use the command there.")).await;
    }
    let Some(warcraft_logs) = ctx.data().warcraft_logs.as_ref() else {
        return refuse(ctx, "Warcraft Logs isn't set up for this bot: `WCL_CLIENT_ID` and `WCL_CLIENT_SECRET` haven't been provided.".to_string()).await;
    };

    let target = match report {
        Some(input) => match warcraftlogs::parse_report_code(&input) {
            Some(code) => Target::Code(code),
            None => return refuse(ctx, format!("Couldn't find a report code in `{}`. Give a report link or its code.", input.trim())).await,
        },
        None => match settings.wcl_source {
            Some(source) => Target::Newest(source),
            None => return refuse(ctx, "Give a report link or code, or register the uploader with `/settings wcl_source` to use the newest report.".to_string()).await,
        },
    };

    // The API takes a few seconds, longer than Discord waits for a first response.
    ctx.defer().await?;

    let code = match target {
        Target::Code(code) => code,
        Target::Newest(source) => match warcraft_logs.latest_report_code(&source).await {
            Ok(Some(code)) => code,
            Ok(None) => {
                ctx.say("None of the guild's newest reports are from the registered uploader. Give a report link or code instead.").await?;
                return Ok(());
            }
            Err(e) => return failed(ctx, &e).await,
        },
    };

    match warcraft_logs.overview(&code).await {
        Ok(overview) => {
            ctx.send(CreateReply::default().embed(warcraftlogs::overview_embed(&overview))).await?;
            Ok(())
        }
        Err(e) => failed(ctx, &e).await,
    }
}

/// Which report `/wcl report` was asked for.
enum Target {
    Code(String),
    /// The newest report from the registered uploader.
    Newest(WclSource),
}

/// Tells only the officer who ran the command why nothing was posted.
async fn refuse(ctx: Context<'_>, content: String) -> Result<(), Error> {
    ctx.send(CreateReply::default().content(content).ephemeral(true)).await?;
    Ok(())
}

/// Reports an API failure in the channel, since the command has already been deferred publicly.
/// The message is safe to show: it only ever holds the API's own error text.
async fn failed(ctx: Context<'_>, error: &Error) -> Result<(), Error> {
    error!("Warcraft Logs request failed: {error}");
    ctx.say(format!("Couldn't get that from Warcraft Logs. {error}")).await?;
    Ok(())
}
