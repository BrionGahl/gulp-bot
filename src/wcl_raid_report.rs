use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Duration as ChronoDuration, Utc};
use chrono_tz::Tz;
use log::{error, info};
use poise::serenity_prelude::{CreateMessage, GuildId, Http};
use sqlx::PgPool;

use crate::guild_settings::{GuildSettings, GuildSettingsStore};
use crate::raid_attendance::MAX_RAID_HOURS;
use crate::raid_notes;
use crate::types::bot::Error;
use crate::warcraftlogs::{self, WarcraftLogsClients};
use crate::wcl_ignored_players;
use crate::wcl_mechanics;

/// How long after a raid's scheduled end its report is fetched. Raids run past their scheduled
/// end and a live log trails the game a little, so a report read on the scheduled minute would
/// usually be missing the night's last pulls.
const UPLOAD_GRACE: Duration = Duration::from_secs(30 * 60);

/// Posted in place of the overview when the uploader has no report from this raid.
const NO_REPORT: &str = "No Warcraft Logs report from this raid was found. Run `/wcl report` here once it has been uploaded.";

/// Posts the Warcraft Logs overview of the raid that ended at `raid_end` to that week's raid
/// notes thread: the same overview `/wcl report` gives for the newest report. Called when a
/// guild's raid end schedule fires (see `raid_attendance::schedule_snapshots`), and does nothing
/// for a guild that hasn't set Warcraft Logs up.
pub async fn post_after_raid(
    http: Arc<Http>,
    db: PgPool,
    guild_settings: Arc<GuildSettingsStore>,
    warcraft_logs: Arc<WarcraftLogsClients>,
    guild_id: GuildId,
    raid_end: DateTime<Tz>,
) {
    // Checked before the wait too, so that guilds without Warcraft Logs don't each hold a
    // sleeping task after every raid.
    if !is_set_up(&guild_settings.get(guild_id)) {
        return;
    }
    tokio::time::sleep(UPLOAD_GRACE).await;

    if let Err(e) = post(&http, &db, &guild_settings, &warcraft_logs, guild_id, raid_end).await {
        error!("Failed to post the Warcraft Logs overview after the raid for guild {}: {}", guild_id, e);
    }
}

/// Whether the guild has registered everything the overview after a raid needs.
fn is_set_up(settings: &GuildSettings) -> bool {
    settings.wcl_credentials.is_some() && settings.wcl_source.is_some() && settings.raid_notes_channel_id.is_some()
}

async fn post(
    http: &Http,
    db: &PgPool,
    guild_settings: &GuildSettingsStore,
    warcraft_logs: &WarcraftLogsClients,
    guild_id: GuildId,
    raid_end: DateTime<Tz>,
) -> Result<(), Error> {
    // Read again after the wait, so that a change made in the meantime counts.
    let settings = guild_settings.get(guild_id);
    let (Some(credentials), Some(source), Some(raid_notes_channel_id)) =
        (settings.wcl_credentials.as_ref(), settings.wcl_source.as_ref(), settings.raid_notes_channel_id)
    else {
        return Ok(());
    };
    let client = warcraft_logs.for_guild(guild_id, credentials);

    // A report started before this raid can't be this raid's: without the limit, a night whose
    // log hasn't been uploaded yet would get the previous raid's overview.
    let earliest_start = raid_end.with_timezone(&Utc) - ChronoDuration::hours(MAX_RAID_HOURS);
    let message = match client.latest_report_code_since(source, earliest_start).await? {
        Some(code) => {
            let tracked = wcl_mechanics::list(db, guild_id).await?;
            let ignored = wcl_ignored_players::list(db, guild_id).await?;
            let overview = client.overview(&code, &tracked, &ignored).await?;
            info!("Posting the Warcraft Logs overview of report {} after the raid for guild {}", code, guild_id);
            CreateMessage::new().embed(warcraftlogs::overview_embed(&overview))
        }
        None => {
            info!("No Warcraft Logs report from this raid for guild {}", guild_id);
            CreateMessage::new().content(NO_REPORT)
        }
    };

    let post = raid_notes::week_post(http, db, guild_id, raid_notes_channel_id, raid_end.date_naive()).await?;
    post.thread_id.send_message(http, message).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::guild_settings::{WclCredentials, WclSource};
    use poise::serenity_prelude::ChannelId;

    #[test]
    fn the_overview_after_a_raid_needs_credentials_a_source_and_a_raid_notes_channel() {
        let complete = GuildSettings {
            wcl_credentials: Some(WclCredentials { client_id: "id".to_string(), client_secret: "secret".to_string() }),
            wcl_source: Some(WclSource { guild_id: 1, uploader_id: 2 }),
            raid_notes_channel_id: Some(ChannelId::new(3)),
            ..Default::default()
        };
        assert!(is_set_up(&complete));
        assert!(!is_set_up(&GuildSettings { wcl_credentials: None, ..complete.clone() }));
        assert!(!is_set_up(&GuildSettings { wcl_source: None, ..complete.clone() }));
        assert!(!is_set_up(&GuildSettings { raid_notes_channel_id: None, ..complete }));
        assert!(!is_set_up(&GuildSettings::default()));
    }
}
