use poise::CreateReply;

use crate::types::bot::{Context, Error};

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
               \n- Paste https://github.com/bart-dev-wow/TimelineReminders and click Import\
               \n- You should then see the addon, click install", false
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
