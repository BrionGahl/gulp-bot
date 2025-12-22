use poise::CreateReply;
use poise::serenity_prelude::{Colour, CreateEmbed, CreateEmbedAuthor, CreateEmbedFooter};
use reqwest::Response;
use crate::types::bot::{Context, Error};
use crate::types::raids::{Raid, Raids};

const URL: &str = "https://wowaudit.com/v1/";
const IMG: &str = "https://data.wowaudit.com/img/new-logo.svg";

#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "WoW Audit",
    discard_spare_arguments
)]
pub async fn get_upcoming_raids(ctx: Context<'_>, #[description = "Number of raids to show"] #[min = 1] #[max = 8] count: Option<usize>) -> Result<(), Error> {
    let count = count.unwrap_or(4);
    let raids = get_raids(&ctx.data().config.wow_audit_token).await?;
    let raids_to_show = raids.iter().take(count);

    let embed = CreateEmbed::default()
        .author(CreateEmbedAuthor::new(&ctx.data().config.bot_name))
        .title(format!("Upcoming {} Raids", count))
        .footer(CreateEmbedFooter::new(
            "All rights reserved to Kail-Area52",
        ))
        .colour(Colour::from_rgb(255, 0, 255))
        .fields(raids_to_show.map(|raid| {(
            format!("{} ({})", &raid.instance, &raid.difficulty),
            format!("Date: {}\nTime: {} - {}", &raid.date, &raid.start_time, &raid.end_time),
            false
        )})
    );

    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}

#[poise::command(
    prefix_command,
    slash_command,
    category = "WoW Audit",
    discard_spare_arguments
)]
pub async fn get_upcoming_absences(ctx: Context<'_>, #[description = "Number of raids to show"] #[min = 1] #[max = 8] count: Option<usize>) -> Result<(), Error> {
    let count = count.unwrap_or(2);
    let raids = get_raids(&ctx.data().config.wow_audit_token).await?;
    let raids_to_search = raids.iter().take(count);

    let mut absences_list = Vec::new();

    for raid in raids_to_search {
        let full_raid = get_raid(&ctx.data().config.wow_audit_token, raid.id).await?;
        let mut absentees = Vec::new();
        if let Some(signups_data) = full_raid.signups {
            for signup in signups_data {
                if &signup.status == "Absent" {
                    absentees.push(signup.character.name.clone())
                }
            }
        }
        if !absentees.is_empty() {
            absences_list.push((raid.date.clone(), absentees));
        }
    }

    let embed = CreateEmbed::default()
        .author(CreateEmbedAuthor::new(&ctx.data().config.bot_name))
        .title(format!("Upcoming Absences For the Next {} Raids", count))
        .footer(CreateEmbedFooter::new(
            "All rights reserved to Kail-Area52",
        ))
        .colour(Colour::from_rgb(255, 0, 255))
        .fields(absences_list.into_iter().map(|(date, list)| {(
            date,
            list.join(", "),
            false
        )})
    );

    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}

async fn get_raids(token: &str) -> Result<Vec<Raid>, Error> {
    let url = format!("{}raids?include_past=false", URL);
    let found_raids = get_response(token, &url)
        .await?
        .json::<Raids>()
        .await?;

    Ok(found_raids.raids)
}

async fn get_raid(token: &str, id: u32) -> Result<Raid, Error> {
    let url = format!("{}raids/{}", URL, id);
    let found_raid = get_response(token, &url)
        .await?
        .json::<Raid>()
        .await?;

    Ok(found_raid)
}

async fn get_response(token: &str, url: &str) -> Result<Response, reqwest::Error> {
    let client = reqwest::Client::new();
    client.get(url)
        .header("Authorization", token)
        .send()
        .await
}