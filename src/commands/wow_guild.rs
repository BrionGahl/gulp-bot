use log::info;
use poise::CreateReply;
use poise::serenity_prelude::{Colour, CreateEmbed, CreateEmbedAuthor, CreateEmbedFooter, CreateMessage};
use reqwest::Response;

use crate::types::bot::{Context, Error};
use crate::types::raids::{Raid, Raids};

const URL: &str = "https://wowaudit.com/v1/";
const IMG: &str = "https://data.wowaudit.com/img/new-logo.svg";

#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "WoW Guild",
    discard_spare_arguments
)]
pub async fn get_liquid_info(ctx: Context<'_>) -> Result<(), Error> {
    let bart_token = &ctx.data().config.bart_token;
    let embed = CreateEmbed::default()
        .author(CreateEmbedAuthor::new(&ctx.data().config.bot_name))
        .title("Liquid Addon Information")
        .footer(CreateEmbedFooter::new("All rights reserved to Kail"))
        .colour(Colour::from(crate::helper::COLOUR))
        .field("This is your tier 2 personal (permanent) access token. It is valid for the duration of our Patreon subscription.",
               format!("```plaintext\n{}\n```", bart_token), true)
        .field("", "Please do not share this token publicly.", false)
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

#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "WoW Guild",
)]
pub async fn get_better_resources(ctx: Context<'_>) -> Result<(), Error> {
    let dm_channel = match ctx.author().create_dm_channel(&ctx).await {
        Ok(channel) => channel,
        Err(_) => {
            ctx.reply("Failed to send message, do you have me blocked").await?;
            return Ok(())
        }
    };

    let embed = CreateEmbed::default()
        .author(CreateEmbedAuthor::new(&ctx.data().config.bot_name))
        .title("Get Better Resources")
        .footer(CreateEmbedFooter::new("All rights reserved to Kail"))
        .colour(Colour::from(crate::helper::COLOUR))
        .field("Keybinds",
               "- [Keybinds Guide by mushuh](https://www.youtube.com/watch?v=sHIuHvlD__E)\n\
               - [Ready Check Workshop #1](https://www.youtube.com/watch?v=Kz2gd-Y2ndA)\n\
               - [Quazii's Keybinding Guide](https://www.youtube.com/watch?v=4bCzBstqlF0)\n\
               - [Should I unbind backpedal?](https://www.youtube.com/watch?v=d2BMbIWWMBA)",
               false)
        .field("User Interface",
               "- [Ready Check Workshop #2](https://youtu.be/jonFtAB0NCk)\n\
               - [Setup Guide: Big Wigs Boss Timers](https://youtu.be/jpelwtqQk0I)",
               false)
        .field("Learning a Class/Spec",
               "- [Ready Check Workshop #3](https://youtu.be/VqI063rhLmc)\n\
               Resources Mentioned:\n\
               - [Archon (Formerly Subcreation)](https://www.archon.gg/wow)\n\
               - [WarcraftLogs](https://www.warcraftlogs.com/)\n\
               - Class Discords can be found via the `/class_discords` command",
               false);

    match dm_channel.send_message(&ctx, CreateMessage::default().embed(embed)).await {
        Ok(_) => ctx.reply("I've sent you a DM with the requested resources.").await?,
        Err(_) => ctx.reply("Failed to send message, do you have me blocked").await?,
    };

    Ok(())
}

#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "WoW Guild",
)]
pub async fn class_discords(ctx: Context<'_>) -> Result<(), Error> {
    let embed = CreateEmbed::default()
        .author(CreateEmbedAuthor::new(&ctx.data().config.bot_name))
        .title("Class Discords")
        .footer(CreateEmbedFooter::new("All rights reserved to Kail"))
        .colour(Colour::from(crate::helper::COLOUR))
        .field("",
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
               Warrior - https://discord.gg/SkyHold",
               true
        );
    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}

#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "WoW Guild",
)]
pub async fn get_upcoming_raids(ctx: Context<'_>, #[description = "Number of raids to show"] #[min = 1] #[max = 8] count: Option<usize>) -> Result<(), Error> {
    let count = count.unwrap_or(4);
    let raids = get_raids(&ctx.data().config.wow_audit_token).await?;
    let mapping = raids.iter().take(count).map(|raid| {(
        format!("{} ({})", &raid.instance, &raid.difficulty),
        format!("Date: {}\nTime: {} - {}", &raid.date, &raid.start_time, &raid.end_time),
        false
    )});

    let embed = CreateEmbed::default()
        .author(CreateEmbedAuthor::new(&ctx.data().config.bot_name))
        .title(format!("Upcoming {} Raids", count))
        .footer(CreateEmbedFooter::new("All rights reserved to Kail"))
        .colour(Colour::from(crate::helper::COLOUR))
        .fields(mapping);

    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}

#[poise::command(
    prefix_command,
    slash_command,
    category = "WoW Guild",
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

    let mapping = absences_list.into_iter().map(|(date, list)| {(
        date,
        list.join(", "),
        false
    )});
    let embed = CreateEmbed::default()
        .author(CreateEmbedAuthor::new(&ctx.data().config.bot_name))
        .title(format!("Upcoming Absences For the Next {} Raids", count))
        .footer(CreateEmbedFooter::new("All rights reserved to Kail"))
        .colour(Colour::from(crate::helper::COLOUR))
        .fields(mapping);

    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}

async fn get_raids(token: &str) -> Result<Vec<Raid>, Error> {
    let url = format!("{}raids?include_past=false", URL);
    let found_raids = get_response(token, &url)
        .await?
        .json::<Raids>()
        .await?;
    info!("Successfully pulled upcoming raids");
    Ok(found_raids.raids)
}

async fn get_raid(token: &str, id: u32) -> Result<Raid, Error> {
    let url = format!("{}raids/{}", URL, id);
    let found_raid = get_response(token, &url)
        .await?
        .json::<Raid>()
        .await?;
    info!("Successfully pulled raid with id {}", id);
    Ok(found_raid)
}

async fn get_response(token: &str, url: &str) -> Result<Response, reqwest::Error> {
    info!("Attempting GET on URL: {}", url);
    let client = reqwest::Client::new();
    client.get(url)
        .header("Authorization", token)
        .send()
        .await
}