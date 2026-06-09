use log::{info, warn};
use poise::CreateReply;
use poise::futures_util::future::join_all;
use poise::serenity_prelude::{CreateMessage};
use reqwest::Response;

use crate::types::bot::{Context, Error};
use crate::types::raids::{Raid, Raids};

const URL: &str = "https://wowaudit.com/v1/";
const IMG: &str = "https://data.wowaudit.com/img/new-logo.svg";

/// Fetches Liquid WeakAura / Addon info if you have a raider role.
#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "WoW Guild",
    check = "crate::checks::check_is_raider",
)]
pub async fn get_liquid_info(ctx: Context<'_>) -> Result<(), Error> {
    let bart_token = &ctx.data().config.bart_token;

    let embed = crate::helper::create_base_embed(&ctx)
        .title("Bart Timeline Reminders Addon Information")
        .field("This is your tier 2 personal (permanent) access token. It is valid for the duration of our Patreon subscription.",
               format!("```plaintext\n{}\n```", bart_token), true)
        .description("Please do not share this token publicly.")
        .field("Install Instructions",
               "- Install WowUp with CurseForge from https://wowup.io/\
               \n- Open up the WowUp app, and navigate to Options > Addons\
               \n- In the bottom right, where it says \"Personal Access Token\", input the above token.\
               \n- Navigate to Get Addons (sidebar) > Install from URL (top right)\
               \n- Paste https://github.com/bart-dev-wow/AuraUpdater and click Import\
               \n- You should then see the addon, click install\"\
               \n- Repeat the previous step for https://github.com/bart-dev-wow/TimelineReminders", false
        );

    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}

/// Shows links to all the class Discord servers
#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "WoW Guild",
)]
pub async fn class_discords(ctx: Context<'_>) -> Result<(), Error> {
    let embed = crate::helper::create_base_embed(&ctx)
        .title("Class Discords")
        .description(
            "Death Knight - https://discord.gg/acherus\n\
            Demon Hunter - https://discord.gg/felhammer\n\
            Druid - https://discord.gg/dreamgrove\n\
            Evoker - https://discord.gg/evoker\n\
            Hunter - https://discord.gg/trueshot\n\
            Mage - https://discord.gg/makGfZA\n\
            Monk - https://discord.gg/peakofserenity\n\
            Paladin - https://discord.gg/hammerofwrath\n\
            Priest - https://discord.gg/WarcraftPriests\n\
            Rogue - https://discord.gg/ravenholdt\n\
            Shaman - https://discord.gg/earthshrine\n\
            Warlock - https://discord.gg/BlackHarvest\n\
            Warrior - https://discord.gg/SkyHold"
        );

    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}

/// Fetches upcoming raids if you have a raider role
#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "WoW Guild",
    check = "crate::checks::check_is_raider",
)]
pub async fn get_upcoming_raids(ctx: Context<'_>, #[description = "Number of raids to show"] #[min = 1] #[max = 8] count: Option<usize>) -> Result<(), Error> {
    // For API requests, gives extra time on replies
    ctx.defer_ephemeral().await?;

    let wow_audit_token = &ctx.data().config.wow_audit_token;
    let http = &ctx.data().http_client;

    let count = count.unwrap_or(4);
    let raids = get_raids(http, wow_audit_token).await?;

    let mapping = raids.iter().take(count).map(|raid| {
        (format!("{} ({})", &raid.instance, &raid.difficulty), format!("Date: {}\nTime: {} - {}", &raid.date, &raid.start_time, &raid.end_time), false)
    });
    let embed = crate::helper::create_base_embed(&ctx)
        .title(format!("Upcoming {} Raids", count))
        .fields(mapping);

    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}


/// Fetches upcoming absences if you have a moderator role
#[poise::command(
    prefix_command,
    slash_command,
    category = "WoW Guild",
    discard_spare_arguments,
    check = "crate::checks::check_is_moderator",
)]
pub async fn get_upcoming_absences(ctx: Context<'_>, #[description = "Number of raids to show"] #[min = 1] #[max = 8] count: Option<usize>) -> Result<(), Error> {
    // For API requests, gives extra time on replies
    ctx.defer_ephemeral().await?;
    let count = count.unwrap_or(2);

    let wow_audit_token = &ctx.data().config.wow_audit_token;
    let http = &ctx.data().http_client;

    let raids = get_raids(http, wow_audit_token).await?;
    let raids_to_search = raids.iter().take(count);

    let mut absences_list = Vec::new();

    let tasks = raids_to_search.map(|raid| get_raid(http, wow_audit_token, raid.id));
    let results = join_all(tasks).await;

    for result in results {
        match result {
            Ok(raid) => {
                let absentees: Vec<String> = raid.signups
                    .into_iter()
                    .flatten()
                    .filter(|signup| signup.status == "Absent")
                    .map(|signup| signup.character.name)
                    .collect();

                if !absentees.is_empty() {
                    absences_list.push((raid.date, absentees));
                }
            }
            Err(_) => warn!("Failed to fetch individual raid.")
        };
    }

    let mapping = absences_list.into_iter().map(|(date, list)| {
        (date, list.join(", "), false)
    });
    let embed = crate::helper::create_base_embed(&ctx)
        .title(format!("Upcoming Absences For the Next {} Raids", count))
        .fields(mapping);

    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}

/// Gets all upcoming raids
async fn get_raids(http: &reqwest::Client, token: &str) -> Result<Vec<Raid>, Error> {
    let url = format!("{}raids?include_past=false", URL);
    let found_raids = get_response(http, token, &url)
        .await?
        .json::<Raids>()
        .await?;
    info!("Successfully pulled upcoming raids");
    Ok(found_raids.raids)
}

/// Gets a specific raid based on a raid ID
async fn get_raid(http: &reqwest::Client, token: &str, id: u32) -> Result<Raid, Error> {
    let url = format!("{}raids/{}", URL, id);
    let found_raid = get_response(http, token, &url)
        .await?
        .json::<Raid>()
        .await?;
    info!("Successfully pulled raid with id {}", id);
    Ok(found_raid)
}


/// Makes a GET request to a specific WoWAudit URL and returns the response
async fn get_response(http: &reqwest::Client, token: &str, url: &str) -> Result<Response, reqwest::Error> {
    info!("Attempting GET on URL: {}", url);
    http.get(url)
        .header("Authorization", token)
        .send()
        .await
}