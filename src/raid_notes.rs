use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Datelike, Duration as ChronoDuration, TimeZone, Weekday};
use chrono_tz::{America::New_York, Tz};
use log::{error, info};
use poise::serenity_prelude::{ChannelId, CreateForumPost, CreateMessage, Error, Http};

const POST_HOUR: u32 = 9;

/// Runs forever, creating a new post in the raid notes forum channel every Tuesday at
/// 9:00 AM Eastern. Uses `America/New_York` rather than a fixed UTC offset so the post
/// keeps landing at 9:00 AM local time across the EST/EDT switch.
pub async fn schedule_weekly_posts(http: Arc<Http>, channel_id: ChannelId) {
    loop {
        let now = chrono::Utc::now().with_timezone(&New_York);
        let target = next_tuesday_9am(now);

        let sleep_duration = (target - now).to_std().unwrap_or(Duration::ZERO);
        info!("Next weekly raid notes post scheduled for {}", target);
        tokio::time::sleep(sleep_duration).await;

        if let Err(e) = post_weekly_raid_notes(&http, channel_id, target).await {
            error!("Failed to create weekly raid notes post: {}", e);
        }
    }
}

/// Finds the next Tuesday 9:00 AM strictly after `now`, in the same timezone as `now`.
fn next_tuesday_9am(now: DateTime<Tz>) -> DateTime<Tz> {
    let mut date = now.date_naive();
    loop {
        if date.weekday() == Weekday::Tue {
            let local = date.and_hms_opt(POST_HOUR, 0, 0).expect("valid time");
            if let Some(candidate) = New_York.from_local_datetime(&local).single() {
                if candidate > now {
                    return candidate;
                }
            }
        }
        date = date.succ_opt().expect("date overflow while computing next Tuesday");
    }
}

async fn post_weekly_raid_notes(
    http: &Http,
    channel_id: ChannelId,
    post_time: DateTime<Tz>,
) -> Result<(), Error> {
    let monday = post_time.date_naive()
        - ChronoDuration::days(post_time.weekday().num_days_from_monday() as i64);
    let sunday = monday + ChronoDuration::days(6);
    let title = format!("Week of {} - {}", monday.format("%b %-d"), sunday.format("%b %-d"));

    let message = CreateMessage::new().content("Raid notes for this week go here.");
    channel_id.create_forum_post(http, CreateForumPost::new(title.clone(), message)).await?;

    info!("Created weekly raid notes post: {}", title);
    Ok(())
}
