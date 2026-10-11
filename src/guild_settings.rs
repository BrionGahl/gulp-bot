use std::collections::{HashMap, HashSet};
use std::fmt;
use std::sync::RwLock;

use cron::Schedule;
use log::warn;
use poise::serenity_prelude::{ChannelId, GuildId, RoleId, UserId};
use sqlx::{PgPool, Row};

/// What a server's administrators have registered for it through the `/settings` commands.
/// Everything is optional: a feature whose values aren't registered is simply off for that server.
#[derive(Debug, Clone, Default)]
pub struct GuildSettings {
    pub mod_role_id: Option<RoleId>,
    pub raider_role_id: Option<RoleId>,
    pub trial_role_id: Option<RoleId>,
    pub personal_officer_category_id: Option<ChannelId>,
    pub raid_notes_channel_id: Option<ChannelId>,
    /// Members whose self-deleted messages the bot reposts.
    pub replay_user_ids: HashSet<UserId>,
    pub clips_channel_ids: HashSet<ChannelId>,
    /// Bart Timeline Reminders access token handed to this server's raiders.
    pub bart_token: Option<String>,
    /// WowUtils API token and the group droptimizer reports are submitted to. Registered and
    /// cleared together, so in practice both are set or neither is.
    pub wowutils_token: Option<String>,
    pub wowutils_group_id: Option<String>,
    /// When the raid notes post is created; `None` means `raid_notes::DEFAULT_RAID_NOTES_CRON`.
    pub raid_notes_cron: Option<Schedule>,
    /// Raid attendance tracking; `None` means it's off for this guild.
    pub raid_voice: Option<RaidVoice>,
    /// The channel Warcraft Logs results are posted in, and the only one `/wcl` works in.
    pub wcl_channel_id: Option<ChannelId>,
    /// Where this guild's Warcraft Logs reports are found.
    pub wcl_source: Option<WclSource>,
    /// This guild's Warcraft Logs API client; `None` means `/wcl` is off for it.
    pub wcl_credentials: Option<WclCredentials>,
}

/// A Warcraft Logs API client's ID and secret, as issued at warcraftlogs.com (Manage Your Clients).
/// See `warcraftlogs.rs`.
#[derive(Clone, PartialEq, Eq)]
pub struct WclCredentials {
    pub client_id: String,
    pub client_secret: String,
}

// Written by hand so that the client secret can never end up in a log line.
impl fmt::Debug for WclCredentials {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("WclCredentials").finish_non_exhaustive()
    }
}

/// Where a guild's Warcraft Logs reports come from: the Warcraft Logs guild they are uploaded to,
/// and the user ID of the one account whose uploads count. See `warcraftlogs.rs`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WclSource {
    pub guild_id: u32,
    pub uploader_id: u32,
}

/// A guild's raid voice channel and when to record who is in it. See `raid_attendance.rs`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RaidVoice {
    pub channel_id: ChannelId,
    /// Fires at the start of each raid.
    pub start: Schedule,
    /// Fires at the end of each raid.
    pub end: Schedule,
}

/// A single-valued ID setting, i.e. one of the BIGINT columns of the `guild_settings` table.
#[derive(Debug, Clone, Copy, PartialEq, Eq, poise::ChoiceParameter)]
pub enum Setting {
    #[name = "Moderator role"]
    ModRole,
    #[name = "Raider role"]
    RaiderRole,
    #[name = "Trial role"]
    TrialRole,
    #[name = "Personal officer category"]
    PersonalOfficerCategory,
    #[name = "Raid notes channel"]
    RaidNotesChannel,
    #[name = "Warcraft Logs channel"]
    WclChannel,
}

impl Setting {
    fn column(self) -> &'static str {
        match self {
            Setting::ModRole => "mod_role_id",
            Setting::RaiderRole => "raider_role_id",
            Setting::TrialRole => "trial_role_id",
            Setting::PersonalOfficerCategory => "personal_officer_category_id",
            Setting::RaidNotesChannel => "raid_notes_channel_id",
            Setting::WclChannel => "wcl_channel_id",
        }
    }
}

/// Postgres-backed store of every guild's settings, fronted by an in-memory copy.
///
/// Settings are read on every message the bot sees (clips channels, delete-and-replay), so reads
/// are served from memory. The copy is loaded once at startup and updated on every write; that is
/// only correct because a single bot process owns the database.
#[derive(Debug)]
pub struct GuildSettingsStore {
    pool: PgPool,
    cache: RwLock<HashMap<GuildId, GuildSettings>>,
}

impl GuildSettingsStore {
    pub async fn load(pool: PgPool) -> Result<Self, sqlx::Error> {
        let mut cache: HashMap<GuildId, GuildSettings> = HashMap::new();

        let rows = sqlx::query(
            "SELECT guild_id, mod_role_id, raider_role_id, trial_role_id, personal_officer_category_id, \
             raid_notes_channel_id, \
             bart_token, wowutils_token, wowutils_group_id, raid_notes_cron, \
             raid_voice_channel_id, raid_voice_start_cron, raid_voice_end_cron, \
             wcl_channel_id, wcl_guild_id, wcl_uploader_id, wcl_client_id, wcl_client_secret FROM guild_settings",
        )
        .fetch_all(&pool)
        .await?;

        for row in rows {
            let id = |column: &str| -> Result<Option<u64>, sqlx::Error> {
                Ok(row.try_get::<Option<i64>, _>(column)?.map(|id| id as u64))
            };
            let guild_id = GuildId::new(row.try_get::<i64, _>("guild_id")? as u64);

            // Expressions are validated before they're stored, so this only fails if the row was
            // edited by hand; fall back to the default schedule rather than refusing to start.
            let raid_notes_cron = row.try_get::<Option<String>, _>("raid_notes_cron")?.and_then(|expression| {
                expression
                    .parse::<Schedule>()
                    .inspect_err(|e| warn!("Ignoring unreadable raid notes cron `{expression}` for guild {guild_id}: {e}"))
                    .ok()
            });

            // Same reasoning as the raid notes cron above: an unreadable expression turns the
            // feature off for that guild rather than stopping the bot.
            let raid_voice = match (
                id("raid_voice_channel_id")?,
                row.try_get::<Option<String>, _>("raid_voice_start_cron")?,
                row.try_get::<Option<String>, _>("raid_voice_end_cron")?,
            ) {
                (Some(channel_id), Some(start), Some(end)) => match (start.parse::<Schedule>(), end.parse::<Schedule>()) {
                    (Ok(start), Ok(end)) => Some(RaidVoice { channel_id: ChannelId::new(channel_id), start, end }),
                    _ => {
                        warn!("Ignoring unreadable raid attendance schedule (`{start}` / `{end}`) for guild {guild_id}");
                        None
                    }
                },
                _ => None,
            };

            let wcl_id = |column: &str| -> Result<Option<u32>, sqlx::Error> {
                Ok(row.try_get::<Option<i64>, _>(column)?.and_then(|id| u32::try_from(id).ok()))
            };
            let wcl_source = match (wcl_id("wcl_guild_id")?, wcl_id("wcl_uploader_id")?) {
                (Some(guild_id), Some(uploader_id)) => Some(WclSource { guild_id, uploader_id }),
                _ => None,
            };
            let wcl_credentials = match (row.try_get::<Option<String>, _>("wcl_client_id")?, row.try_get::<Option<String>, _>("wcl_client_secret")?) {
                (Some(client_id), Some(client_secret)) => Some(WclCredentials { client_id, client_secret }),
                _ => None,
            };

            cache.insert(guild_id, GuildSettings {
                mod_role_id: id("mod_role_id")?.map(RoleId::new),
                raider_role_id: id("raider_role_id")?.map(RoleId::new),
                trial_role_id: id("trial_role_id")?.map(RoleId::new),
                personal_officer_category_id: id("personal_officer_category_id")?.map(ChannelId::new),
                raid_notes_channel_id: id("raid_notes_channel_id")?.map(ChannelId::new),
                replay_user_ids: HashSet::new(),
                clips_channel_ids: HashSet::new(),
                bart_token: row.try_get("bart_token")?,
                wowutils_token: row.try_get("wowutils_token")?,
                wowutils_group_id: row.try_get("wowutils_group_id")?,
                raid_notes_cron,
                raid_voice,
                wcl_channel_id: id("wcl_channel_id")?.map(ChannelId::new),
                wcl_source,
                wcl_credentials,
            });
        }

        let rows = sqlx::query("SELECT guild_id, channel_id FROM guild_clips_channels")
            .fetch_all(&pool)
            .await?;

        for row in rows {
            let guild_id = GuildId::new(row.try_get::<i64, _>("guild_id")? as u64);
            let channel_id = ChannelId::new(row.try_get::<i64, _>("channel_id")? as u64);
            cache.entry(guild_id).or_default().clips_channel_ids.insert(channel_id);
        }

        let rows = sqlx::query("SELECT guild_id, user_id FROM guild_replay_users")
            .fetch_all(&pool)
            .await?;

        for row in rows {
            let guild_id = GuildId::new(row.try_get::<i64, _>("guild_id")? as u64);
            let user_id = UserId::new(row.try_get::<i64, _>("user_id")? as u64);
            cache.entry(guild_id).or_default().replay_user_ids.insert(user_id);
        }

        Ok(Self { pool, cache: RwLock::new(cache) })
    }

    /// The settings registered for `guild_id`, or all-unset defaults if it has none. Lookups are
    /// always keyed by the guild an event came from, so an ID registered in one server is never
    /// applied in another.
    pub fn get(&self, guild_id: GuildId) -> GuildSettings {
        self.cache.read().unwrap().get(&guild_id).cloned().unwrap_or_default()
    }

    /// Every guild that has settings, for the scheduled jobs that aren't driven by a guild event.
    pub fn all(&self) -> Vec<(GuildId, GuildSettings)> {
        self.cache.read().unwrap().iter().map(|(guild_id, settings)| (*guild_id, settings.clone())).collect()
    }

    /// Registers `value` for `setting` in `guild_id`, or clears the setting when `value` is `None`.
    pub async fn set(&self, guild_id: GuildId, setting: Setting, value: Option<u64>) -> Result<(), sqlx::Error> {
        let column = setting.column();
        sqlx::query(&format!(
            "INSERT INTO guild_settings (guild_id, {column}) VALUES ($1, $2) \
             ON CONFLICT (guild_id) DO UPDATE SET {column} = EXCLUDED.{column}, updated_at = now()"
        ))
        .bind(guild_id.get() as i64)
        .bind(value.map(|id| id as i64))
        .execute(&self.pool)
        .await?;

        let mut cache = self.cache.write().unwrap();
        let settings = cache.entry(guild_id).or_default();
        match setting {
            Setting::ModRole => settings.mod_role_id = value.map(RoleId::new),
            Setting::RaiderRole => settings.raider_role_id = value.map(RoleId::new),
            Setting::TrialRole => settings.trial_role_id = value.map(RoleId::new),
            Setting::PersonalOfficerCategory => settings.personal_officer_category_id = value.map(ChannelId::new),
            Setting::RaidNotesChannel => settings.raid_notes_channel_id = value.map(ChannelId::new),
            Setting::WclChannel => settings.wcl_channel_id = value.map(ChannelId::new),
        }
        Ok(())
    }

    /// Registers where this guild's Warcraft Logs reports are found, or clears it when `None`.
    pub async fn set_wcl_source(&self, guild_id: GuildId, source: Option<WclSource>) -> Result<(), sqlx::Error> {
        sqlx::query(
            "INSERT INTO guild_settings (guild_id, wcl_guild_id, wcl_uploader_id) VALUES ($1, $2, $3) \
             ON CONFLICT (guild_id) DO UPDATE SET wcl_guild_id = EXCLUDED.wcl_guild_id, \
             wcl_uploader_id = EXCLUDED.wcl_uploader_id, updated_at = now()",
        )
        .bind(guild_id.get() as i64)
        .bind(source.as_ref().map(|source| i64::from(source.guild_id)))
        .bind(source.as_ref().map(|source| i64::from(source.uploader_id)))
        .execute(&self.pool)
        .await?;

        self.cache.write().unwrap().entry(guild_id).or_default().wcl_source = source;
        Ok(())
    }

    /// Registers this guild's Warcraft Logs API client, or clears it when `None`.
    pub async fn set_wcl_credentials(&self, guild_id: GuildId, credentials: Option<WclCredentials>) -> Result<(), sqlx::Error> {
        sqlx::query(
            "INSERT INTO guild_settings (guild_id, wcl_client_id, wcl_client_secret) VALUES ($1, $2, $3) \
             ON CONFLICT (guild_id) DO UPDATE SET wcl_client_id = EXCLUDED.wcl_client_id, \
             wcl_client_secret = EXCLUDED.wcl_client_secret, updated_at = now()",
        )
        .bind(guild_id.get() as i64)
        .bind(credentials.as_ref().map(|credentials| credentials.client_id.as_str()))
        .bind(credentials.as_ref().map(|credentials| credentials.client_secret.as_str()))
        .execute(&self.pool)
        .await?;

        self.cache.write().unwrap().entry(guild_id).or_default().wcl_credentials = credentials;
        Ok(())
    }

    /// Registers this guild's Bart Timeline Reminders token, or clears it when `token` is `None`.
    pub async fn set_bart_token(&self, guild_id: GuildId, token: Option<&str>) -> Result<(), sqlx::Error> {
        sqlx::query(
            "INSERT INTO guild_settings (guild_id, bart_token) VALUES ($1, $2) \
             ON CONFLICT (guild_id) DO UPDATE SET bart_token = EXCLUDED.bart_token, updated_at = now()",
        )
        .bind(guild_id.get() as i64)
        .bind(token)
        .execute(&self.pool)
        .await?;

        self.cache.write().unwrap().entry(guild_id).or_default().bart_token = token.map(str::to_string);
        Ok(())
    }

    /// Registers this guild's WowUtils `(token, group_id)`, or clears both when `None`.
    pub async fn set_wowutils(&self, guild_id: GuildId, credentials: Option<(&str, &str)>) -> Result<(), sqlx::Error> {
        let (token, group_id) = credentials.unzip();
        sqlx::query(
            "INSERT INTO guild_settings (guild_id, wowutils_token, wowutils_group_id) VALUES ($1, $2, $3) \
             ON CONFLICT (guild_id) DO UPDATE SET wowutils_token = EXCLUDED.wowutils_token, \
             wowutils_group_id = EXCLUDED.wowutils_group_id, updated_at = now()",
        )
        .bind(guild_id.get() as i64)
        .bind(token)
        .bind(group_id)
        .execute(&self.pool)
        .await?;

        let mut cache = self.cache.write().unwrap();
        let settings = cache.entry(guild_id).or_default();
        settings.wowutils_token = token.map(str::to_string);
        settings.wowutils_group_id = group_id.map(str::to_string);
        Ok(())
    }

    /// Registers this guild's raid notes schedule, or reverts it to the default when `None`.
    pub async fn set_raid_notes_cron(&self, guild_id: GuildId, schedule: Option<Schedule>) -> Result<(), sqlx::Error> {
        sqlx::query(
            "INSERT INTO guild_settings (guild_id, raid_notes_cron) VALUES ($1, $2) \
             ON CONFLICT (guild_id) DO UPDATE SET raid_notes_cron = EXCLUDED.raid_notes_cron, updated_at = now()",
        )
        .bind(guild_id.get() as i64)
        .bind(schedule.as_ref().map(Schedule::source))
        .execute(&self.pool)
        .await?;

        self.cache.write().unwrap().entry(guild_id).or_default().raid_notes_cron = schedule;
        Ok(())
    }

    /// Registers this guild's raid voice channel and schedules, or turns attendance tracking off
    /// when `None`.
    pub async fn set_raid_voice(&self, guild_id: GuildId, raid_voice: Option<RaidVoice>) -> Result<(), sqlx::Error> {
        sqlx::query(
            "INSERT INTO guild_settings (guild_id, raid_voice_channel_id, raid_voice_start_cron, raid_voice_end_cron) \
             VALUES ($1, $2, $3, $4) \
             ON CONFLICT (guild_id) DO UPDATE SET raid_voice_channel_id = EXCLUDED.raid_voice_channel_id, \
             raid_voice_start_cron = EXCLUDED.raid_voice_start_cron, raid_voice_end_cron = EXCLUDED.raid_voice_end_cron, \
             updated_at = now()",
        )
        .bind(guild_id.get() as i64)
        .bind(raid_voice.as_ref().map(|raid_voice| raid_voice.channel_id.get() as i64))
        .bind(raid_voice.as_ref().map(|raid_voice| raid_voice.start.source()))
        .bind(raid_voice.as_ref().map(|raid_voice| raid_voice.end.source()))
        .execute(&self.pool)
        .await?;

        self.cache.write().unwrap().entry(guild_id).or_default().raid_voice = raid_voice;
        Ok(())
    }

    pub async fn add_replay_user(&self, guild_id: GuildId, user_id: UserId) -> Result<(), sqlx::Error> {
        sqlx::query("INSERT INTO guild_replay_users (guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING")
            .bind(guild_id.get() as i64)
            .bind(user_id.get() as i64)
            .execute(&self.pool)
            .await?;

        self.cache.write().unwrap().entry(guild_id).or_default().replay_user_ids.insert(user_id);
        Ok(())
    }

    /// Returns whether `user_id` was registered as a replay user before this call.
    pub async fn remove_replay_user(&self, guild_id: GuildId, user_id: UserId) -> Result<bool, sqlx::Error> {
        sqlx::query("DELETE FROM guild_replay_users WHERE guild_id = $1 AND user_id = $2")
            .bind(guild_id.get() as i64)
            .bind(user_id.get() as i64)
            .execute(&self.pool)
            .await?;

        Ok(self.cache.write().unwrap().entry(guild_id).or_default().replay_user_ids.remove(&user_id))
    }

    pub async fn add_clips_channel(&self, guild_id: GuildId, channel_id: ChannelId) -> Result<(), sqlx::Error> {
        sqlx::query("INSERT INTO guild_clips_channels (guild_id, channel_id) VALUES ($1, $2) ON CONFLICT DO NOTHING")
            .bind(guild_id.get() as i64)
            .bind(channel_id.get() as i64)
            .execute(&self.pool)
            .await?;

        self.cache.write().unwrap().entry(guild_id).or_default().clips_channel_ids.insert(channel_id);
        Ok(())
    }

    /// Returns whether `channel_id` was registered as a clips channel before this call.
    pub async fn remove_clips_channel(&self, guild_id: GuildId, channel_id: ChannelId) -> Result<bool, sqlx::Error> {
        sqlx::query("DELETE FROM guild_clips_channels WHERE guild_id = $1 AND channel_id = $2")
            .bind(guild_id.get() as i64)
            .bind(channel_id.get() as i64)
            .execute(&self.pool)
            .await?;

        Ok(self.cache.write().unwrap().entry(guild_id).or_default().clips_channel_ids.remove(&channel_id))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Needs a throwaway Postgres: `TEST_DATABASE_URL=postgres://... cargo test -- --ignored`.
    #[tokio::test]
    #[ignore = "needs TEST_DATABASE_URL pointing at a disposable Postgres database"]
    async fn settings_round_trip_and_stay_scoped_to_their_guild() {
        let url = std::env::var("TEST_DATABASE_URL").expect("TEST_DATABASE_URL must be set");
        let pool = PgPool::connect(&url).await.unwrap();
        sqlx::migrate!().run(&pool).await.unwrap();
        sqlx::query("TRUNCATE guild_settings, guild_clips_channels, guild_replay_users").execute(&pool).await.unwrap();

        let guild_a = GuildId::new(111);
        let guild_b = GuildId::new(222);
        // Larger than i64::MAX, to cover the u64 <-> BIGINT conversion.
        let big_role = u64::MAX - 5;

        let store = GuildSettingsStore::load(pool.clone()).await.unwrap();
        store.set(guild_a, Setting::ModRole, Some(big_role)).await.unwrap();
        store.set(guild_a, Setting::RaidNotesChannel, Some(42)).await.unwrap();
        store.set(guild_a, Setting::TrialRole, Some(7)).await.unwrap();
        store.set(guild_a, Setting::TrialRole, None).await.unwrap();
        store.add_replay_user(guild_a, UserId::new(8)).await.unwrap();
        store.add_replay_user(guild_a, UserId::new(9)).await.unwrap();
        store.add_replay_user(guild_a, UserId::new(9)).await.unwrap();
        store.add_replay_user(guild_b, UserId::new(8)).await.unwrap();
        assert!(store.remove_replay_user(guild_a, UserId::new(8)).await.unwrap());
        assert!(!store.remove_replay_user(guild_a, UserId::new(8)).await.unwrap());
        let tuesdays: Schedule = "0 30 17 * * Tue *".parse().unwrap();
        store.set_raid_notes_cron(guild_a, Some(tuesdays.clone())).await.unwrap();
        store.set_raid_notes_cron(guild_b, Some(tuesdays.clone())).await.unwrap();
        store.set_raid_notes_cron(guild_b, None).await.unwrap();
        let raid_voice = RaidVoice {
            channel_id: ChannelId::new(77),
            start: "0 0 20 * * Tue,Thu *".parse().unwrap(),
            end: "0 0 23 * * Tue,Thu *".parse().unwrap(),
        };
        store.set_raid_voice(guild_a, Some(raid_voice.clone())).await.unwrap();
        store.set_raid_voice(guild_b, Some(raid_voice.clone())).await.unwrap();
        store.set_raid_voice(guild_b, None).await.unwrap();
        store.set_bart_token(guild_a, Some("bart-a")).await.unwrap();
        store.set_wowutils(guild_a, Some(("wow-a", "group-a"))).await.unwrap();
        store.set_bart_token(guild_b, Some("bart-b")).await.unwrap();
        store.set_wowutils(guild_b, Some(("wow-b", "group-b"))).await.unwrap();
        store.set_wowutils(guild_b, None).await.unwrap();
        let wcl_source = WclSource { guild_id: 811636, uploader_id: 240984 };
        store.set(guild_a, Setting::WclChannel, Some(55)).await.unwrap();
        store.set_wcl_source(guild_a, Some(wcl_source.clone())).await.unwrap();
        store.set_wcl_source(guild_b, Some(wcl_source.clone())).await.unwrap();
        store.set_wcl_source(guild_b, None).await.unwrap();
        let wcl_credentials = WclCredentials { client_id: "wcl-id-a".to_string(), client_secret: "wcl-secret-a".to_string() };
        store.set_wcl_credentials(guild_a, Some(wcl_credentials.clone())).await.unwrap();
        store.set_wcl_credentials(guild_b, Some(wcl_credentials.clone())).await.unwrap();
        store.set_wcl_credentials(guild_b, None).await.unwrap();
        store.add_clips_channel(guild_a, ChannelId::new(5)).await.unwrap();
        store.add_clips_channel(guild_a, ChannelId::new(5)).await.unwrap();
        store.add_clips_channel(guild_b, ChannelId::new(6)).await.unwrap();
        assert!(store.remove_clips_channel(guild_b, ChannelId::new(6)).await.unwrap());
        assert!(!store.remove_clips_channel(guild_b, ChannelId::new(6)).await.unwrap());

        // The in-memory copy and a fresh load from the database must agree.
        let reloaded = GuildSettingsStore::load(pool).await.unwrap();
        for store in [&store, &reloaded] {
            let a = store.get(guild_a);
            assert_eq!(a.mod_role_id, Some(RoleId::new(big_role)));
            assert_eq!(a.raid_notes_channel_id, Some(ChannelId::new(42)));
            assert_eq!(a.trial_role_id, None);
            // Removing one replay user leaves the guild's others, and other guilds, alone.
            assert_eq!(a.replay_user_ids, HashSet::from([UserId::new(9)]));
            assert_eq!(store.get(guild_b).replay_user_ids, HashSet::from([UserId::new(8)]));
            assert_eq!(a.raider_role_id, None);
            assert_eq!(a.clips_channel_ids, HashSet::from([ChannelId::new(5)]));
            assert_eq!(a.bart_token.as_deref(), Some("bart-a"));
            assert_eq!(a.wowutils_token.as_deref(), Some("wow-a"));
            assert_eq!(a.wowutils_group_id.as_deref(), Some("group-a"));
            assert_eq!(a.raid_notes_cron.as_ref(), Some(&tuesdays));
            assert_eq!(a.raid_voice.as_ref(), Some(&raid_voice));
            assert_eq!(a.wcl_channel_id, Some(ChannelId::new(55)));
            assert_eq!(a.wcl_source.as_ref(), Some(&wcl_source));
            assert_eq!(a.wcl_credentials.as_ref(), Some(&wcl_credentials));

            let b = store.get(guild_b);
            assert_eq!(b.mod_role_id, None);
            assert!(b.clips_channel_ids.is_empty());
            // Setting the earlier role columns for guild A must not have touched these, and
            // guild B only ever sees its own values.
            assert_eq!(b.bart_token.as_deref(), Some("bart-b"));
            assert_eq!(b.wowutils_token, None);
            assert_eq!(b.wowutils_group_id, None);
            assert_eq!(b.raid_notes_cron, None);
            assert_eq!(b.raid_voice, None);
            assert_eq!(b.wcl_channel_id, None);
            assert_eq!(b.wcl_source, None);
            assert_eq!(b.wcl_credentials, None);
        }
    }
}
