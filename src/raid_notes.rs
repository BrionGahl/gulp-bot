use std::sync::Arc;
use std::time::Duration;

use chrono::{Datelike, Duration as ChronoDuration, NaiveDate};
use cron::Schedule;
use log::{error, info};
use poise::serenity_prelude::{ChannelId, CreateForumPost, CreateMessage, Error, Http};

use crate::guild_settings::GuildSettingsStore;
use crate::schedules;

/// Cron expression used for a guild that hasn't registered its own with `/settings
/// raid_notes_cron`. Quartz-style 7-field format (`sec min hour day-of-month month day-of-week
/// year`): Fridays at 9:00 AM.
pub const DEFAULT_RAID_NOTES_CRON: &str = "0 0 9 * * Fri *";

/// The longest the scheduler sleeps before re-reading every guild's settings, which bounds how
/// long a schedule or channel changed through `/settings` takes to be picked up.
const MAX_SLEEP: Duration = Duration::from_secs(60);

/// Runs forever, creating a new post in every guild's registered raid notes forum channel on that
/// guild's schedule (`/settings raid_notes_cron`, or `DEFAULT_RAID_NOTES_CRON` when it has none).
///
/// Schedules differ per guild and can change at any time, so rather than sleeping until one fixed
/// fire time this wakes at the earliest upcoming fire time across all guilds, or after `MAX_SLEEP`
/// if that comes first, and posts for every guild with a fire time since the previous wake-up.
/// Fire times are computed in `America/New_York`, see `schedules::now`.
pub async fn schedule_weekly_posts(http: Arc<Http>, guild_settings: Arc<GuildSettingsStore>) {
    let default_schedule: Schedule = DEFAULT_RAID_NOTES_CRON.parse().expect("DEFAULT_RAID_NOTES_CRON is a valid cron expression");
    let mut last_checked = schedules::now();

    loop {
        let now = schedules::now();
        let mut next_wake = now + ChronoDuration::from_std(MAX_SLEEP).expect("MAX_SLEEP fits in a chrono duration");

        for (guild_id, settings) in guild_settings.all() {
            let Some(channel_id) = settings.raid_notes_channel_id else { continue };
            let schedule = settings.raid_notes_cron.as_ref().unwrap_or(&default_schedule);

            let due = schedule.after(&last_checked).next().filter(|fire_time| *fire_time <= now);
            if let Some(fire_time) = due {
                if let Err(e) = create_raid_notes_post(&http, channel_id, fire_time.date_naive()).await {
                    error!("Failed to create raid notes post for guild {}: {}", guild_id, e);
                }
            }

            if let Some(upcoming) = schedule.after(&now).next() {
                next_wake = next_wake.min(upcoming);
            }
        }

        last_checked = now;
        // Measured from the current time, not `now`: posting above may have taken a while.
        let sleep_duration = (next_wake - schedules::now()).to_std().unwrap_or(Duration::ZERO);
        tokio::time::sleep(sleep_duration).await;
    }
}

/// Creates the raid notes forum post for the week (Mon–Sun) containing `date`, with a placeholder
/// starter message. Returns the post title on success.
pub async fn create_raid_notes_post(
    http: &Http,
    channel_id: ChannelId,
    date: NaiveDate,
) -> Result<String, Error> {
    let monday = date - ChronoDuration::days(date.weekday().num_days_from_monday() as i64);
    let sunday = monday + ChronoDuration::days(6);
    let title = format!("Week of {} - {}", monday.format("%b %-d"), sunday.format("%b %-d"));

    let message = CreateMessage::new().content("Raid notes for this week go here.");
    channel_id.create_forum_post(http, CreateForumPost::new(title.clone(), message)).await?;

    info!("Created weekly raid notes post: {}", title);
    Ok(title)
}
