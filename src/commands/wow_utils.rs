use log::info;
use poise::CreateReply;
use serde::Serialize;

use crate::types::bot::{Context, Error};

const URL: &str = "https://api.wowutils.com/v1/";

#[derive(Serialize)]
struct DroptimizerPayload<'a> {
    url: &'a str,
    #[serde(rename = "profileKey")]
    profile_key: &'a str,
}

/// Submits a Raidbots droptimizer report to the guild's WowUtils group.
#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "WoW Guild",
    check = "crate::checks::check_is_raider",
)]
pub async fn submit_droptimizer(
    ctx: Context<'_>,
    #[description = "Raidbots simulation report URL"] url: String,
    #[description = "Profile key, e.g. heroic-max"] profile_key: String,
) -> Result<(), Error> {
    ctx.defer_ephemeral().await?;

    let token = &ctx.data().config.wowutils_token;
    let group_id = &ctx.data().config.wowutils_group_id;
    let http = &ctx.data().http_client;

    submit_droptimizer_report(http, token, group_id, &url, &profile_key).await?;

    let embed = crate::helper::create_base_embed(&ctx)
        .title("Droptimizer Submitted")
        .description(format!("Submitted report for profile `{}`.", profile_key));

    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}

/// Submits a droptimizer report url + profile key to the given WowUtils group.
async fn submit_droptimizer_report(
    http: &reqwest::Client,
    token: &str,
    group_id: &str,
    url: &str,
    profile_key: &str,
) -> Result<(), Error> {
    let endpoint = format!("{}groups/{}/droptimizers", URL, group_id);
    let payload = DroptimizerPayload { url, profile_key };

    info!("Submitting droptimizer report to WowUtils group {}", group_id);
    let response = http.post(&endpoint)
        .bearer_auth(token)
        .json(&payload)
        .send()
        .await?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        return Err(format!("WowUtils API returned {}: {}", status, body).into());
    }

    info!("Successfully submitted droptimizer report to WowUtils group {}", group_id);
    Ok(())
}
