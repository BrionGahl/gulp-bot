use std::sync::Arc;
use std::time::Duration;

use chrono::Duration as ChronoDuration;
use cron::Schedule;
use log::{error, info, warn};
use poise::serenity_prelude::{ChannelId, CreateMessage, GuildId, Http, UserId};
use sqlx::postgres::PgRow;
use sqlx::{PgPool, Row};

use crate::guild_settings::{GuildSettings, GuildSettingsStore};
use crate::mentions::replace_role_mentions;
use crate::schedules;

/// The longest the scheduler sleeps before re-reading the announcements table, which bounds how
/// long an announcement added or deleted by an officer takes to be picked up.
const MAX_SLEEP: Duration = Duration::from_secs(60);

/// An announcement an officer scheduled: `message` is posted to `channel_id` whenever `schedule`
/// fires.
#[derive(Debug, Clone)]
pub struct Announcement {
    pub id: i64,
    pub guild_id: GuildId,
    pub channel_id: ChannelId,
    pub schedule: Schedule,
    pub message: String,
    pub created_by: UserId,
}

const COLUMNS: &str = "id, guild_id, channel_id, cron, message, created_by";

/// Reads one row. Returns `Ok(None)` for a row whose cron expression can't be parsed: expressions
/// are validated before they're stored, so that only happens if the row was edited by hand, and
/// skipping it beats taking every other announcement down with it.
fn from_row(row: &PgRow) -> Result<Option<Announcement>, sqlx::Error> {
    let id: i64 = row.try_get("id")?;
    let cron: String = row.try_get("cron")?;
    let Ok(schedule) = cron.parse::<Schedule>() else {
        warn!("Skipping announcement {id}: unreadable cron expression `{cron}`");
        return Ok(None);
    };

    Ok(Some(Announcement {
        id,
        guild_id: GuildId::new(row.try_get::<i64, _>("guild_id")? as u64),
        channel_id: ChannelId::new(row.try_get::<i64, _>("channel_id")? as u64),
        schedule,
        message: row.try_get("message")?,
        created_by: UserId::new(row.try_get::<i64, _>("created_by")? as u64),
    }))
}

fn from_rows(rows: &[PgRow]) -> Result<Vec<Announcement>, sqlx::Error> {
    Ok(rows.iter().map(from_row).collect::<Result<Vec<_>, _>>()?.into_iter().flatten().collect())
}

/// Stores a new announcement and returns its ID.
pub async fn create(
    db: &PgPool,
    guild_id: GuildId,
    channel_id: ChannelId,
    schedule: &Schedule,
    message: &str,
    created_by: UserId,
) -> Result<i64, sqlx::Error> {
    let row = sqlx::query(
        "INSERT INTO announcements (guild_id, channel_id, cron, message, created_by) VALUES ($1, $2, $3, $4, $5) RETURNING id",
    )
    .bind(guild_id.get() as i64)
    .bind(channel_id.get() as i64)
    .bind(schedule.source())
    .bind(message)
    .bind(created_by.get() as i64)
    .fetch_one(db)
    .await?;

    row.try_get("id")
}

/// `guild_id`'s announcements, oldest first.
pub async fn list(db: &PgPool, guild_id: GuildId) -> Result<Vec<Announcement>, sqlx::Error> {
    let rows = sqlx::query(&format!("SELECT {COLUMNS} FROM announcements WHERE guild_id = $1 ORDER BY id"))
        .bind(guild_id.get() as i64)
        .fetch_all(db)
        .await?;

    from_rows(&rows)
}

pub async fn count(db: &PgPool, guild_id: GuildId) -> Result<i64, sqlx::Error> {
    sqlx::query("SELECT count(*) AS total FROM announcements WHERE guild_id = $1")
        .bind(guild_id.get() as i64)
        .fetch_one(db)
        .await?
        .try_get("total")
}

/// One announcement, but only if it belongs to `guild_id`: IDs are global, and an officer of one
/// server must not be able to read, post or delete another server's announcement by guessing one.
pub async fn get(db: &PgPool, guild_id: GuildId, id: i64) -> Result<Option<Announcement>, sqlx::Error> {
    let row = sqlx::query(&format!("SELECT {COLUMNS} FROM announcements WHERE id = $1 AND guild_id = $2"))
        .bind(id)
        .bind(guild_id.get() as i64)
        .fetch_optional(db)
        .await?;

    match row {
        Some(row) => from_row(&row),
        None => Ok(None),
    }
}

/// Deletes an announcement, scoped to `guild_id` like `get`. Returns whether it existed there.
pub async fn delete(db: &PgPool, guild_id: GuildId, id: i64) -> Result<bool, sqlx::Error> {
    let result = sqlx::query("DELETE FROM announcements WHERE id = $1 AND guild_id = $2")
        .bind(id)
        .bind(guild_id.get() as i64)
        .execute(db)
        .await?;

    Ok(result.rows_affected() == 1)
}

async fn all(db: &PgPool) -> Result<Vec<Announcement>, sqlx::Error> {
    let rows = sqlx::query(&format!("SELECT {COLUMNS} FROM announcements ORDER BY id")).fetch_all(db).await?;
    from_rows(&rows)
}

/// Runs forever, posting each stored announcement to its channel whenever its schedule fires.
///
/// Announcements can be added and deleted at any time, so rather than sleeping until one fixed
/// fire time this re-reads the table on every wake-up, wakes at the earliest upcoming fire time
/// across all announcements (or after `MAX_SLEEP` if that comes first), and posts every
/// announcement with a fire time since the previous wake-up.
pub async fn schedule_announcements(http: Arc<Http>, db: PgPool, guild_settings: Arc<GuildSettingsStore>) {
    let mut last_checked = schedules::now();

    loop {
        let now = schedules::now();
        let mut next_wake = now + ChronoDuration::from_std(MAX_SLEEP).expect("MAX_SLEEP fits in a chrono duration");

        match all(&db).await {
            Ok(announcements) => {
                for announcement in &announcements {
                    let due = announcement.schedule.after(&last_checked).next().is_some_and(|fire_time| fire_time <= now);
                    if due {
                        let settings = guild_settings.get(announcement.guild_id);
                        if let Err(e) = post(&http, announcement, &settings).await {
                            error!("Failed to post announcement {} for guild {}: {}", announcement.id, announcement.guild_id, e);
                        }
                    }

                    if let Some(upcoming) = announcement.schedule.after(&now).next() {
                        next_wake = next_wake.min(upcoming);
                    }
                }
                last_checked = now;
            }
            // `last_checked` is left alone, so anything that came due while the database was
            // unreachable is posted once it's back.
            Err(e) => error!("Failed to load announcements: {}", e),
        }

        // Measured from the current time, not `now`: posting above may have taken a while.
        let sleep_duration = (next_wake - schedules::now()).to_std().unwrap_or(Duration::ZERO);
        tokio::time::sleep(sleep_duration).await;
    }
}

/// Posts `announcement` to its channel now, expanding `@Raider`/`@Trial` into mentions of the
/// roles `settings` (its guild's) has registered.
pub async fn post(http: &Http, announcement: &Announcement, settings: &GuildSettings) -> Result<(), poise::serenity_prelude::Error> {
    let message = render(&announcement.message, settings);
    announcement.channel_id.send_message(http, CreateMessage::new().content(message)).await?;
    info!("Posted announcement {} for guild {}", announcement.id, announcement.guild_id);
    Ok(())
}

/// Expands the `@Raider`/`@Trial` placeholders in `message` into real role mentions. A role the
/// guild hasn't registered is left as its plain-text placeholder.
fn render(message: &str, settings: &GuildSettings) -> String {
    let replacements: Vec<_> = [("@Raider", settings.raider_role_id), ("@Trial", settings.trial_role_id)]
        .into_iter()
        .filter_map(|(placeholder, role_id)| role_id.map(|role_id| (placeholder, role_id)))
        .collect();

    replace_role_mentions(message, &replacements)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Needs a throwaway Postgres: `TEST_DATABASE_URL=postgres://... cargo test -- --ignored`.
    #[tokio::test]
    #[ignore = "needs TEST_DATABASE_URL pointing at a disposable Postgres database"]
    async fn announcements_are_scoped_to_their_guild() {
        let url = std::env::var("TEST_DATABASE_URL").expect("TEST_DATABASE_URL must be set");
        let db = PgPool::connect(&url).await.unwrap();
        sqlx::migrate!().run(&db).await.unwrap();
        sqlx::query("TRUNCATE announcements").execute(&db).await.unwrap();

        let (guild_a, guild_b) = (GuildId::new(111), GuildId::new(222));
        let schedule: Schedule = "0 0 12 * * Tue *".parse().unwrap();
        let message = "@Raider\n\n## Weekly Reset\nMulti-line, with *markdown*.";

        let first = create(&db, guild_a, ChannelId::new(10), &schedule, message, UserId::new(1)).await.unwrap();
        let second = create(&db, guild_a, ChannelId::new(11), &schedule, "second", UserId::new(1)).await.unwrap();
        let other = create(&db, guild_b, ChannelId::new(20), &schedule, "other guild", UserId::new(2)).await.unwrap();

        let listed = list(&db, guild_a).await.unwrap();
        assert_eq!(listed.iter().map(|a| a.id).collect::<Vec<_>>(), vec![first, second]);
        assert_eq!(listed[0].message, message);
        assert_eq!(listed[0].schedule, schedule);
        assert_eq!(listed[0].channel_id, ChannelId::new(10));
        assert_eq!(count(&db, guild_a).await.unwrap(), 2);

        // Another guild's announcement can't be read or deleted by ID.
        assert!(get(&db, guild_a, other).await.unwrap().is_none());
        assert!(!delete(&db, guild_a, other).await.unwrap());
        assert!(get(&db, guild_b, other).await.unwrap().is_some());

        assert!(delete(&db, guild_a, first).await.unwrap());
        assert!(!delete(&db, guild_a, first).await.unwrap());
        assert_eq!(all(&db).await.unwrap().iter().map(|a| a.id).collect::<Vec<_>>(), vec![second, other]);
    }

    #[test]
    fn render_only_mentions_registered_roles() {
        let settings = GuildSettings { raider_role_id: Some(poise::serenity_prelude::RoleId::new(5)), ..Default::default() };
        assert_eq!(render("@Raider, @Trial hello", &settings), "<@&5>, @Trial hello");
    }
}
