use std::sync::Arc;
use std::time::Duration;

use chrono::{Datelike, Duration as ChronoDuration, NaiveDate};
use cron::Schedule;
use log::{error, info};
use poise::serenity_prelude::{ChannelId, CreateForumPost, CreateMessage, GuildId, Http};
use sqlx::{PgPool, Row};
use tokio::sync::Mutex;

use crate::guild_settings::GuildSettingsStore;
use crate::schedules;
use crate::types::bot::Error;

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
/// A week that already has a thread (because attendance was recorded earlier in it, see
/// `raid_attendance.rs`) doesn't get a second one.
/// Fire times are computed in `America/New_York`, see `schedules::now`.
pub async fn schedule_weekly_posts(http: Arc<Http>, db: PgPool, guild_settings: Arc<GuildSettingsStore>) {
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
                if let Err(e) = week_post(&http, &db, guild_id, channel_id, fire_time.date_naive()).await {
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

/// The raid notes forum thread for one week.
pub struct WeekPost {
    pub thread_id: ChannelId,
    pub title: String,
    /// Whether this call created the thread, as opposed to finding the one already recorded.
    pub created: bool,
}

/// Serializes `week_post` so that the scheduler here and the attendance scheduler, both wanting
/// the same week's thread at the same moment, can't each create one. Enough on its own because a
/// single bot process owns the database.
static WEEK_POST_LOCK: Mutex<()> = Mutex::const_new(());

/// The Tuesday on or before `date`, which is what identifies a week's thread. Raid weeks run
/// Tuesday (the game's weekly reset) to Monday, so a Tuesday always starts a new week and a
/// Monday belongs to the week that began six days earlier.
pub fn week_start(date: NaiveDate) -> NaiveDate {
    let days_since_tuesday = (date.weekday().num_days_from_monday() + 6) % 7;
    date - ChronoDuration::days(days_since_tuesday as i64)
}

/// Returns the raid notes forum thread for the week containing `date`, creating it (with a
/// placeholder starter message) in `channel_id` and recording it if the bot hasn't made one for
/// that week yet.
pub async fn week_post(
    http: &Http,
    db: &PgPool,
    guild_id: GuildId,
    channel_id: ChannelId,
    date: NaiveDate,
) -> Result<WeekPost, Error> {
    let tuesday = week_start(date);
    let monday = tuesday + ChronoDuration::days(6);
    let title = format!("Week of {} - {}", tuesday.format("%b %-d"), monday.format("%b %-d"));

    let _lock = WEEK_POST_LOCK.lock().await;

    let existing = sqlx::query("SELECT thread_id FROM raid_notes_posts WHERE guild_id = $1 AND week_start = $2")
        .bind(guild_id.get() as i64)
        .bind(tuesday)
        .fetch_optional(db)
        .await?;
    if let Some(row) = existing {
        let thread_id = ChannelId::new(row.try_get::<i64, _>("thread_id")? as u64);
        return Ok(WeekPost { thread_id, title, created: false });
    }

    let message = CreateMessage::new().content("Raid notes for this week go here.");
    let thread = channel_id.create_forum_post(http, CreateForumPost::new(title.clone(), message)).await?;
    info!("Created weekly raid notes post for guild {}: {}", guild_id, title);

    sqlx::query("INSERT INTO raid_notes_posts (guild_id, week_start, thread_id) VALUES ($1, $2, $3)")
        .bind(guild_id.get() as i64)
        .bind(tuesday)
        .bind(thread.id.get() as i64)
        .execute(db)
        .await?;

    Ok(WeekPost { thread_id: thread.id, title, created: true })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn weeks_run_tuesday_to_monday() {
        let date = |day| NaiveDate::from_ymd_opt(2026, 10, day).unwrap();
        // Oct 13 2026 is a Tuesday.
        assert_eq!(date(13).weekday(), chrono::Weekday::Tue);

        assert_eq!(week_start(date(13)), date(13)); // Tuesday starts its own week
        assert_eq!(week_start(date(16)), date(13)); // Friday
        assert_eq!(week_start(date(19)), date(13)); // Monday is the last day of the week
        assert_eq!(week_start(date(20)), date(20)); // the next Tuesday starts the next one
        assert_eq!(week_start(date(12)), date(6)); // the Monday before belongs to the week before
    }
}
