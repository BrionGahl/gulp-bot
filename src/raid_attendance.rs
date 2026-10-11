use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Duration as ChronoDuration, Utc};
use chrono_tz::Tz;
use log::{error, info};
use poise::serenity_prelude::{Cache, ChannelId, Colour, CreateEmbed, CreateMessage, GuildId, Http, UserId};
use sqlx::{PgPool, Row};

use crate::guild_settings::GuildSettingsStore;
use crate::raid_notes;
use crate::schedules;
use crate::types::bot::Error;
use crate::warcraftlogs::WarcraftLogsClients;
use crate::wcl_raid_report;

/// The longest the scheduler sleeps before re-reading every guild's settings, which bounds how
/// long a change made through `/settings raid_voice` takes to be picked up.
const MAX_SLEEP: Duration = Duration::from_secs(60);

/// Discord's limits on an embed's description and on one field's value; a longer attendance list
/// is cut short to fit.
const MAX_DESCRIPTION_CHARS: usize = 4096;
const MAX_FIELD_CHARS: usize = 1024;

/// The purple used by the bot's other embeds (see `helper::create_base_embed`).
const EMBED_COLOUR: u32 = 0xAF69EE;

/// How long before a raid-end snapshot a raid-start snapshot can have been taken and still be
/// treated as the start of that same raid. Anything older is a start whose end never fired (the
/// bot was down, or the schedules don't pair up), and comparing against it would be misleading.
pub const MAX_RAID_HOURS: i64 = 12;

/// Which point of the raid a snapshot was taken at.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Moment {
    Start,
    End,
    /// Taken on demand with `/test_raid_attendance`.
    Test,
}

impl Moment {
    fn label(self) -> &'static str {
        match self {
            Moment::Start => "start",
            Moment::End => "end",
            Moment::Test => "manual check",
        }
    }
}

/// Runs forever, recording who is in each guild's raid voice channel whenever that guild's raid
/// start or raid end schedule fires (`/settings raid_voice`), and posting the list to that week's
/// raid notes thread. Uses the same wake-up scheme as `raid_notes::schedule_weekly_posts`.
///
/// The raid end schedule is also what the Warcraft Logs overview of the night goes by: it is
/// posted to the same thread a little later, see `wcl_raid_report.rs`.
pub async fn schedule_snapshots(
    http: Arc<Http>,
    cache: Arc<Cache>,
    db: PgPool,
    guild_settings: Arc<GuildSettingsStore>,
    warcraft_logs: Arc<WarcraftLogsClients>,
) {
    let mut last_checked = schedules::now();

    loop {
        let now = schedules::now();
        let mut next_wake = now + ChronoDuration::from_std(MAX_SLEEP).expect("MAX_SLEEP fits in a chrono duration");

        for (guild_id, settings) in guild_settings.all() {
            let Some(raid_voice) = settings.raid_voice else { continue };

            for (moment, schedule) in [(Moment::Start, &raid_voice.start), (Moment::End, &raid_voice.end)] {
                let due = schedule.after(&last_checked).next().filter(|fire_time| *fire_time <= now);
                if let Some(fire_time) = due {
                    // Checked only once a snapshot is due, so that registering the raid notes
                    // channel later needs no change here.
                    match settings.raid_notes_channel_id {
                        Some(raid_notes_channel_id) => {
                            let result = record(&http, &cache, &db, guild_id, raid_voice.channel_id, raid_notes_channel_id, moment, fire_time).await;
                            if let Err(e) = result {
                                error!("Failed to record raid {} attendance for guild {}: {}", moment.label(), guild_id, e);
                            }
                        }
                        None => error!("Skipping raid {} attendance for guild {}: no raid notes channel is registered", moment.label(), guild_id),
                    }

                    // On a task of its own: it waits for the log to finish uploading, and the
                    // other guilds' snapshots can't wait with it.
                    if moment == Moment::End {
                        tokio::spawn(wcl_raid_report::post_after_raid(
                            http.clone(),
                            db.clone(),
                            guild_settings.clone(),
                            warcraft_logs.clone(),
                            guild_id,
                            fire_time,
                        ));
                    }
                }

                if let Some(upcoming) = schedule.after(&now).next() {
                    next_wake = next_wake.min(upcoming);
                }
            }
        }

        last_checked = now;
        // Measured from the current time, not `now`: posting above may have taken a while.
        let sleep_duration = (next_wake - schedules::now()).to_std().unwrap_or(Duration::ZERO);
        tokio::time::sleep(sleep_duration).await;
    }
}

/// Takes a snapshot of who is in `voice_channel_id` right now and posts it to the raid notes
/// thread for the week containing `at`, creating that thread if the week doesn't have one yet
/// (raids usually happen before the weekly thread's own schedule fires). Returns the thread.
#[allow(clippy::too_many_arguments)]
pub async fn record(
    http: &Http,
    cache: &Cache,
    db: &PgPool,
    guild_id: GuildId,
    voice_channel_id: ChannelId,
    raid_notes_channel_id: ChannelId,
    moment: Moment,
    at: DateTime<Tz>,
) -> Result<ChannelId, Error> {
    // Taken before any awaiting below, so the list reflects the scheduled moment as closely as
    // possible rather than whenever Discord got round to creating the thread.
    let present = members_in_voice(cache, guild_id, voice_channel_id)?;

    let post = raid_notes::week_post(http, db, guild_id, raid_notes_channel_id, at.date_naive()).await?;

    // Mentions inside an embed never notify anyone, so the list is a record, not a summons.
    let message = CreateMessage::new().embed(snapshot_embed(moment, at, voice_channel_id, &present));
    post.thread_id.send_message(http, message).await?;
    info!("Recorded raid {} attendance for guild {}: {} present", moment.label(), guild_id, present.len());

    match moment {
        Moment::Start => save_start(db, guild_id, voice_channel_id, at, &present).await?,
        Moment::End => {
            // Taken (not just read) so one start is only ever compared against one end.
            let start = take_start(db, guild_id).await?.filter(|start| start.pairs_with(voice_channel_id, at));
            let message = CreateMessage::new().embed(summary_embed(start.as_ref(), at, &present));
            post.thread_id.send_message(http, message).await?;
        }
        // A manual check is neither end of a raid: it mustn't disturb a real start snapshot.
        Moment::Test => {}
    }

    Ok(post.thread_id)
}

/// A stored raid-start snapshot.
#[derive(Debug, PartialEq, Eq)]
struct StartSnapshot {
    voice_channel_id: ChannelId,
    taken_at: DateTime<Utc>,
    present: Vec<UserId>,
}

impl StartSnapshot {
    /// Whether this is plausibly the start of the raid ending at `end` in `voice_channel_id`.
    fn pairs_with(&self, voice_channel_id: ChannelId, end: DateTime<Tz>) -> bool {
        let elapsed = end.with_timezone(&Utc) - self.taken_at;
        self.voice_channel_id == voice_channel_id
            && elapsed >= ChronoDuration::zero()
            && elapsed <= ChronoDuration::hours(MAX_RAID_HOURS)
    }
}

/// Stores `present` as the guild's current raid-start snapshot, replacing any earlier one.
async fn save_start(
    db: &PgPool,
    guild_id: GuildId,
    voice_channel_id: ChannelId,
    at: DateTime<Tz>,
    present: &[UserId],
) -> Result<(), sqlx::Error> {
    let user_ids: Vec<i64> = present.iter().map(|user_id| user_id.get() as i64).collect();
    sqlx::query(
        "INSERT INTO raid_attendance_starts (guild_id, voice_channel_id, taken_at, user_ids) VALUES ($1, $2, $3, $4) \
         ON CONFLICT (guild_id) DO UPDATE SET voice_channel_id = EXCLUDED.voice_channel_id, \
         taken_at = EXCLUDED.taken_at, user_ids = EXCLUDED.user_ids",
    )
    .bind(guild_id.get() as i64)
    .bind(voice_channel_id.get() as i64)
    .bind(at.with_timezone(&Utc))
    .bind(user_ids)
    .execute(db)
    .await?;

    Ok(())
}

/// Removes and returns the guild's stored raid-start snapshot, if it has one.
async fn take_start(db: &PgPool, guild_id: GuildId) -> Result<Option<StartSnapshot>, sqlx::Error> {
    let row = sqlx::query("DELETE FROM raid_attendance_starts WHERE guild_id = $1 RETURNING voice_channel_id, taken_at, user_ids")
        .bind(guild_id.get() as i64)
        .fetch_optional(db)
        .await?;
    let Some(row) = row else { return Ok(None) };

    Ok(Some(StartSnapshot {
        voice_channel_id: ChannelId::new(row.try_get::<i64, _>("voice_channel_id")? as u64),
        taken_at: row.try_get("taken_at")?,
        present: row.try_get::<Vec<i64>, _>("user_ids")?.into_iter().map(|id| UserId::new(id as u64)).collect(),
    }))
}

/// Everyone currently connected to `voice_channel_id`, from the gateway cache, sorted by ID so
/// the start and end lists are easy to compare. Bots are left out where the cache knows the
/// account is one.
fn members_in_voice(cache: &Cache, guild_id: GuildId, voice_channel_id: ChannelId) -> Result<Vec<UserId>, Error> {
    let guild = cache.guild(guild_id).ok_or("the server isn't in the bot's cache, so its voice channels can't be read")?;

    let mut present: Vec<UserId> = guild
        .voice_states
        .values()
        .filter(|voice_state| voice_state.channel_id == Some(voice_channel_id))
        .filter(|voice_state| !voice_state.member.as_ref().is_some_and(|member| member.user.bot))
        .map(|voice_state| voice_state.user_id)
        .collect();
    present.sort();

    Ok(present)
}

/// The embed for one snapshot: who was in the voice channel at that moment.
fn snapshot_embed(moment: Moment, at: DateTime<Tz>, voice_channel_id: ChannelId, present: &[UserId]) -> CreateEmbed {
    base_embed(format!("Raid attendance — {}", moment.label()))
        .description(snapshot_description(at, voice_channel_id, present))
        .timestamp(at)
}

fn snapshot_description(at: DateTime<Tz>, voice_channel_id: ChannelId, present: &[UserId]) -> String {
    let mut description = format!("<#{}> · <t:{}:f>\n\n", voice_channel_id, at.timestamp());

    if present.is_empty() {
        description.push_str("Nobody was in the channel.");
        return description;
    }

    description.push_str(&format!("**{} present**\n", present.len()));
    let budget = MAX_DESCRIPTION_CHARS - description.len();
    description.push_str(&mention_list(present, budget));
    description
}

/// The embed posted after a raid-end snapshot, comparing it with the raid's start snapshot, or
/// saying there was none to compare with.
fn summary_embed(start: Option<&StartSnapshot>, end_at: DateTime<Tz>, present_at_end: &[UserId]) -> CreateEmbed {
    let embed = base_embed("Raid attendance — summary".to_string()).timestamp(end_at);
    let Some(start) = start else {
        return embed.description("No start snapshot was recorded for this raid, so there's nothing to compare the end against.");
    };

    let comparison = Comparison::between(&start.present, present_at_end);
    let list = |users: &[UserId]| if users.is_empty() { "Nobody".to_string() } else { mention_list(users, MAX_FIELD_CHARS) };

    embed
        .description(format!("<t:{}:t> to <t:{}:t>", start.taken_at.timestamp(), end_at.timestamp()))
        .field("There at both the start and the end", comparison.stayed.to_string(), false)
        .field(format!("Left before the end ({})", comparison.left_early.len()), list(&comparison.left_early), false)
        .field(format!("Joined after the start ({})", comparison.joined_late.len()), list(&comparison.joined_late), false)
}

/// How the people present at the end of a raid differ from those present at its start.
#[derive(Debug, PartialEq, Eq)]
struct Comparison {
    /// How many were there at both moments.
    stayed: usize,
    left_early: Vec<UserId>,
    joined_late: Vec<UserId>,
}

impl Comparison {
    fn between(present_at_start: &[UserId], present_at_end: &[UserId]) -> Self {
        // Both lists are sorted by ID, but `contains` keeps this obviously right at raid sizes.
        let left_early: Vec<UserId> = present_at_start.iter().copied().filter(|user_id| !present_at_end.contains(user_id)).collect();
        let joined_late: Vec<UserId> = present_at_end.iter().copied().filter(|user_id| !present_at_start.contains(user_id)).collect();

        Self { stayed: present_at_start.len() - left_early.len(), left_early, joined_late }
    }
}

fn base_embed(title: String) -> CreateEmbed {
    CreateEmbed::new().title(title).colour(Colour::from(EMBED_COLOUR))
}

/// `users` as comma-separated mentions, in at most `max_chars`. If they don't all fit, the list
/// is cut short and ends with a line saying how many were left off.
fn mention_list(users: &[UserId], max_chars: usize) -> String {
    let mut list = String::new();
    for (listed, user_id) in users.iter().enumerate() {
        let mention = format!("<@{user_id}>");
        let separator = if listed == 0 { "" } else { ", " };
        let overflow_note = format!("\n…and {} more.", users.len() - listed);
        // Always keep room for the note, so the count of who was left off is never itself cut.
        if list.len() + separator.len() + mention.len() + overflow_note.len() > max_chars {
            list.push_str(&overflow_note);
            break;
        }
        list.push_str(separator);
        list.push_str(&mention);
    }
    list
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;
    use chrono_tz::America::New_York;

    fn at() -> DateTime<Tz> {
        New_York.with_ymd_and_hms(2026, 10, 13, 20, 0, 0).unwrap()
    }

    #[test]
    fn snapshot_lists_everyone_present() {
        let description = snapshot_description(at(), ChannelId::new(9), &[UserId::new(1), UserId::new(2)]);
        assert!(description.starts_with("<#9> · <t:"));
        assert!(description.ends_with("**2 present**\n<@1>, <@2>"));

        assert!(snapshot_description(at(), ChannelId::new(9), &[]).ends_with("Nobody was in the channel."));
    }

    #[test]
    fn snapshot_cuts_a_long_list_to_fit_one_embed() {
        let present: Vec<UserId> = (1..=500).map(|n| UserId::new(100_000_000_000_000_000 + n)).collect();
        let description = snapshot_description(at(), ChannelId::new(9), &present);

        assert!(description.len() <= MAX_DESCRIPTION_CHARS);
        let listed = description.matches("<@").count();
        assert!(listed > 0 && listed < 500);
        assert!(description.ends_with(&format!("…and {} more.", 500 - listed)));
    }

    fn users(ids: &[u64]) -> Vec<UserId> {
        ids.iter().map(|id| UserId::new(*id)).collect()
    }

    fn start_snapshot(present: &[u64], hours_before_end: i64) -> StartSnapshot {
        StartSnapshot {
            voice_channel_id: ChannelId::new(9),
            taken_at: at().with_timezone(&Utc) - ChronoDuration::hours(hours_before_end),
            present: users(present),
        }
    }

    #[test]
    fn comparison_reports_who_left_early_and_who_joined_late() {
        assert_eq!(
            Comparison::between(&users(&[1, 2, 3]), &users(&[2, 3, 4, 5])),
            Comparison { stayed: 2, left_early: users(&[1]), joined_late: users(&[4, 5]) },
        );
        assert_eq!(
            Comparison::between(&users(&[1, 2]), &users(&[1, 2])),
            Comparison { stayed: 2, left_early: vec![], joined_late: vec![] },
        );
        assert_eq!(
            Comparison::between(&[], &users(&[1])),
            Comparison { stayed: 0, left_early: vec![], joined_late: users(&[1]) },
        );
    }

    #[test]
    fn a_long_list_is_cut_to_fit_an_embed_field() {
        let everyone: Vec<UserId> = (1..=300).map(|n| UserId::new(100_000_000_000_000_000 + n)).collect();
        let list = mention_list(&everyone, MAX_FIELD_CHARS);

        assert!(list.len() <= MAX_FIELD_CHARS);
        assert!(list.ends_with(&format!("…and {} more.", 300 - list.matches("<@").count())));
        // A list that fits is left whole.
        assert_eq!(mention_list(&users(&[4, 5]), MAX_FIELD_CHARS), "<@4>, <@5>");
    }

    #[test]
    fn a_start_only_pairs_with_an_end_of_the_same_raid() {
        let voice = ChannelId::new(9);
        assert!(start_snapshot(&[1], 3).pairs_with(voice, at()));
        assert!(start_snapshot(&[1], 0).pairs_with(voice, at()));
        // Left over from a raid whose end never fired.
        assert!(!start_snapshot(&[1], MAX_RAID_HOURS + 1).pairs_with(voice, at()));
        // Taken after this end.
        assert!(!start_snapshot(&[1], -1).pairs_with(voice, at()));
        // The raid voice channel was changed in between.
        assert!(!start_snapshot(&[1], 3).pairs_with(ChannelId::new(10), at()));
    }

    /// Needs a throwaway Postgres: `TEST_DATABASE_URL=postgres://... cargo test -- --ignored`.
    #[tokio::test]
    #[ignore = "needs TEST_DATABASE_URL pointing at a disposable Postgres database"]
    async fn a_start_snapshot_is_stored_per_guild_and_taken_once() {
        let url = std::env::var("TEST_DATABASE_URL").expect("TEST_DATABASE_URL must be set");
        let db = PgPool::connect(&url).await.unwrap();
        sqlx::migrate!().run(&db).await.unwrap();
        sqlx::query("TRUNCATE raid_attendance_starts").execute(&db).await.unwrap();

        let (guild_a, guild_b) = (GuildId::new(111), GuildId::new(222));
        // IDs above i64::MAX cover the u64 <-> BIGINT[] conversion.
        let present = users(&[1, u64::MAX - 5]);

        save_start(&db, guild_a, ChannelId::new(8), at(), &users(&[7])).await.unwrap();
        save_start(&db, guild_a, ChannelId::new(9), at(), &present).await.unwrap();
        save_start(&db, guild_b, ChannelId::new(9), at(), &[]).await.unwrap();

        // The second save replaced the first, and the timestamp survives the round trip.
        let taken = take_start(&db, guild_a).await.unwrap().unwrap();
        assert_eq!(taken, StartSnapshot { voice_channel_id: ChannelId::new(9), taken_at: at().with_timezone(&Utc), present });
        assert!(taken.pairs_with(ChannelId::new(9), at()));

        assert!(take_start(&db, guild_a).await.unwrap().is_none());
        assert!(take_start(&db, guild_b).await.unwrap().unwrap().present.is_empty());
    }
}
