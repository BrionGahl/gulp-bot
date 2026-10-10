use log::error;
use poise::CreateReply;

use crate::guild_settings::{GuildSettings, WclSource};
use crate::types::bot::{Context, Error};
use crate::warcraftlogs::{self, WarcraftLogs};
use crate::wcl_ignored_players;
use crate::wcl_mechanics::{self, TrackedAbility};

/// Warcraft Logs overviews of raid nights, for administrators and moderators
///
/// Only in the Warcraft Logs channel, and nothing is posted unless one of them runs a command.
/// Results go in the channel registered with `/settings wcl_channel`; the commands refuse to run
/// anywhere else.
//
// The check and `guild_only` are set here once: poise applies a parent command's checks to all of
// its subcommands. (Kept out of the doc comment, which poise shows to users in `/help wcl`.)
#[poise::command(
    slash_command,
    guild_only,
    category = "Warcraft Logs",
    check = "crate::checks::check_is_admin_or_moderator",
    subcommands("report", "damage_taken"),
    subcommand_required,
)]
pub async fn wcl(_ctx: Context<'_>) -> Result<(), Error> {
    Ok(())
}

/// Post the overview of a raid night's report: pulls, deaths and damage from tracked abilities
///
/// Only the first two deaths in each pull are counted, since the rest is the raid wiping, and damage
/// taken is counted up to the same moment. Only Mythic boss pulls are included. The abilities shown
/// are the ones registered with `/settings wcl_mechanic_add`. Without a report, the newest report
/// from the uploader registered with `/settings wcl_source` is used.
#[poise::command(slash_command)]
async fn report(
    ctx: Context<'_>,
    #[description = "Report link or code; defaults to the newest report from the registered uploader"] report: Option<String>,
) -> Result<(), Error> {
    let Some(ready) = ready(ctx).await? else { return Ok(()) };
    let target = match target(report, &ready.settings) {
        Ok(target) => target,
        Err(problem) => return refuse(ctx, problem).await,
    };
    let tracked = wcl_mechanics::list(&ctx.data().db, ready.guild_id).await?;
    let ignored = wcl_ignored_players::list(&ctx.data().db, ready.guild_id).await?;

    // The API takes a few seconds, longer than Discord waits for a first response.
    ctx.defer().await?;
    let Some(code) = resolve_code(ctx, ready.warcraft_logs, target).await? else { return Ok(()) };

    match ready.warcraft_logs.overview(&code, &tracked, &ignored).await {
        Ok(overview) => {
            ctx.send(CreateReply::default().embed(warcraftlogs::overview_embed(&overview))).await?;
            Ok(())
        }
        Err(e) => failed(ctx, &e).await,
    }
}

/// Post who took damage from one ability in a raid night's report
///
/// The ability is one registered with `/settings wcl_mechanic_add`, or any spell ID. Damage is
/// counted up to the second death in each pull, since the rest is the raid wiping. Only Mythic boss
/// pulls are included. Without a report, the newest report from the uploader registered with
/// `/settings wcl_source` is used.
#[poise::command(slash_command)]
async fn damage_taken(
    ctx: Context<'_>,
    #[description = "A tracked ability, or a spell ID"]
    #[autocomplete = "autocomplete_ability"]
    ability: String,
    #[description = "Only the pulls of the boss whose name contains this"] boss: Option<String>,
    #[description = "Report link or code; defaults to the newest report from the registered uploader"] report: Option<String>,
) -> Result<(), Error> {
    let Some(ready) = ready(ctx).await? else { return Ok(()) };
    let target = match target(report, &ready.settings) {
        Ok(target) => target,
        Err(problem) => return refuse(ctx, problem).await,
    };
    let tracked = wcl_mechanics::list(&ctx.data().db, ready.guild_id).await?;
    let ignored = wcl_ignored_players::list(&ctx.data().db, ready.guild_id).await?;
    let Some(ability) = resolve_ability(&tracked, &ability) else {
        return refuse(ctx, format!("`{}` isn't a tracked ability or a spell ID. Track one with `/settings wcl_mechanic_add`, or give its spell ID.", ability.trim())).await;
    };

    ctx.defer().await?;
    let Some(code) = resolve_code(ctx, ready.warcraft_logs, target).await? else { return Ok(()) };

    match ready.warcraft_logs.damage_report(&code, boss.as_deref(), &ability, &ignored).await {
        Ok(damage) => {
            ctx.send(CreateReply::default().embed(warcraftlogs::damage_embed(&damage))).await?;
            Ok(())
        }
        Err(e) => failed(ctx, &e).await,
    }
}

/// The names of this server's tracked abilities that contain what has been typed so far. Poise
/// doesn't run a command's checks for autocomplete, so officers are checked here.
pub async fn autocomplete_ability(ctx: Context<'_>, partial: &str) -> impl Iterator<Item = String> {
    let names = match ctx.guild_id() {
        Some(guild_id) if crate::checks::is_admin_or_moderator(ctx, guild_id) => {
            wcl_mechanics::list(&ctx.data().db, guild_id).await.unwrap_or_default().into_iter().map(|ability| ability.name).collect()
        }
        _ => Vec::new(),
    };

    let partial = partial.to_lowercase();
    names.into_iter().filter(move |name| name.to_lowercase().contains(&partial)).take(25)
}

/// The ignored players whose names contain what has been typed so far; officers only, like
/// `autocomplete_ability`.
pub async fn autocomplete_ignored_player(ctx: Context<'_>, partial: &str) -> impl Iterator<Item = String> {
    let names = match ctx.guild_id() {
        Some(guild_id) if crate::checks::is_admin_or_moderator(ctx, guild_id) => wcl_ignored_players::list(&ctx.data().db, guild_id).await.unwrap_or_default(),
        _ => Vec::new(),
    };

    let partial = partial.to_lowercase();
    names.into_iter().filter(move |name| name.to_lowercase().contains(&partial)).take(25)
}

/// What an officer typed for an ability: a tracked ability's name (ignoring case), or a spell ID,
/// which needn't be tracked (it is then shown as "Ability <id>").
fn resolve_ability(tracked: &[TrackedAbility], input: &str) -> Option<TrackedAbility> {
    let input = input.trim();
    if let Some(ability) = tracked.iter().find(|ability| ability.name.eq_ignore_ascii_case(input)) {
        return Some(ability.clone());
    }

    let ability_id: u32 = input.parse().ok().filter(|id| *id > 0)?;
    Some(tracked.iter().find(|ability| ability.ability_id == ability_id).cloned().unwrap_or_else(|| TrackedAbility {
        ability_id,
        name: format!("Ability {ability_id}"),
    }))
}

/// What the `/wcl` commands need once the checks that don't use the network have passed.
struct Ready<'a> {
    warcraft_logs: &'a WarcraftLogs,
    guild_id: poise::serenity_prelude::GuildId,
    settings: GuildSettings,
}

/// Checks that the command runs in the Warcraft Logs channel and that the bot has API credentials.
/// If not, tells only the officer who ran it and returns `None`. Everything here is checked before
/// the command is deferred, so that a refusal is private rather than a public follow-up.
async fn ready(ctx: Context<'_>) -> Result<Option<Ready<'_>>, Error> {
    // `check_is_admin_or_moderator` has already established this is a guild.
    let Some(guild_id) = ctx.guild_id() else { return Ok(None) };
    let settings = ctx.data().guild_settings.get(guild_id);

    let Some(channel_id) = settings.wcl_channel_id else {
        refuse(ctx, "No Warcraft Logs channel is registered for this server. An administrator or moderator can set one with `/settings wcl_channel`.".to_string()).await?;
        return Ok(None);
    };
    if ctx.channel_id() != channel_id {
        refuse(ctx, format!("Warcraft Logs results are only posted in <#{channel_id}>. Use the command there.")).await?;
        return Ok(None);
    }
    let Some(warcraft_logs) = ctx.data().warcraft_logs.as_ref() else {
        refuse(ctx, "Warcraft Logs isn't set up for this bot: `WCL_CLIENT_ID` and `WCL_CLIENT_SECRET` haven't been provided.".to_string()).await?;
        return Ok(None);
    };

    Ok(Some(Ready { warcraft_logs, guild_id, settings }))
}

/// Which report a command was asked for.
#[derive(Debug)]
enum Target {
    Code(String),
    /// The newest report from the registered uploader.
    Newest(WclSource),
}

/// The report an officer asked for, or why it can't be worked out.
fn target(report: Option<String>, settings: &GuildSettings) -> Result<Target, String> {
    match report {
        Some(input) => warcraftlogs::parse_report_code(&input)
            .map(Target::Code)
            .ok_or_else(|| format!("Couldn't find a report code in `{}`. Give a report link or its code.", input.trim())),
        None => settings
            .wcl_source
            .clone()
            .map(Target::Newest)
            .ok_or_else(|| "Give a report link or code, or register the uploader with `/settings wcl_source` to use the newest report.".to_string()),
    }
}

/// The code of the report asked for, looking up the newest one if that is what was asked. If that
/// fails or finds nothing, says so in the channel and returns `None`.
async fn resolve_code(ctx: Context<'_>, warcraft_logs: &WarcraftLogs, target: Target) -> Result<Option<String>, Error> {
    match target {
        Target::Code(code) => Ok(Some(code)),
        Target::Newest(source) => match warcraft_logs.latest_report_code(&source).await {
            Ok(Some(code)) => Ok(Some(code)),
            Ok(None) => {
                ctx.say("None of the guild's newest reports are from the registered uploader. Give a report link or code instead.").await?;
                Ok(None)
            }
            Err(e) => {
                failed(ctx, &e).await?;
                Ok(None)
            }
        },
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    fn tracked(ability_id: u32, name: &str) -> TrackedAbility {
        TrackedAbility { ability_id, name: name.to_string() }
    }

    #[test]
    fn an_ability_is_a_tracked_name_or_a_spell_id() {
        let tracked_abilities = [tracked(1282408, "Coalesced Venom"), tracked(1299838, "Venom Rupture")];

        // By name, in any case, with the ID and the name as registered.
        assert_eq!(resolve_ability(&tracked_abilities, "  coalesced VENOM "), Some(tracked(1282408, "Coalesced Venom")));
        // A tracked ability's ID finds its name too.
        assert_eq!(resolve_ability(&tracked_abilities, "1299838"), Some(tracked(1299838, "Venom Rupture")));
        // Any other ID works, under a placeholder name.
        assert_eq!(resolve_ability(&tracked_abilities, "5"), Some(tracked(5, "Ability 5")));
        assert_eq!(resolve_ability(&[], "5"), Some(tracked(5, "Ability 5")));

        // A name beats an ID that happens to read the same, and junk is turned down.
        assert_eq!(resolve_ability(&[tracked(9, "123")], "123"), Some(tracked(9, "123")));
        assert_eq!(resolve_ability(&tracked_abilities, "Nope"), None);
        assert_eq!(resolve_ability(&tracked_abilities, "0"), None);
        assert_eq!(resolve_ability(&tracked_abilities, "-4"), None);
        assert_eq!(resolve_ability(&tracked_abilities, ""), None);
    }

    #[test]
    fn the_report_asked_for_is_a_code_or_the_newest_from_the_uploader() {
        let without_source = GuildSettings::default();
        let source = WclSource { guild_id: 1, uploader_id: 2 };
        let with_source = GuildSettings { wcl_source: Some(source.clone()), ..Default::default() };

        assert!(matches!(target(Some("https://www.warcraftlogs.com/reports/ABCdef123".to_string()), &without_source), Ok(Target::Code(code)) if code == "ABCdef123"));
        assert!(matches!(target(None, &with_source), Ok(Target::Newest(found)) if found == source));
        // A link wins over the registered uploader.
        assert!(matches!(target(Some("ABC".to_string()), &with_source), Ok(Target::Code(_))));

        assert!(target(None, &without_source).unwrap_err().contains("/settings wcl_source"));
        assert!(target(Some("not a code!".to_string()), &with_source).unwrap_err().contains("`not a code!`"));
    }
}
