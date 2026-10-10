use poise::serenity_prelude::GuildId;
use sqlx::{PgPool, Row};

/// The most abilities one server can track. Each is a query to the Warcraft Logs API and a field
/// of the overview embed, which has a size limit of its own.
pub const MAX_TRACKED: usize = 6;

/// The longest name an officer can give an ability: it is a field title in the overview, and
/// Discord allows 256 characters there.
pub const MAX_NAME_CHARS: usize = 100;

/// An ability whose damage taken is shown in the Warcraft Logs overview.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrackedAbility {
    /// The game's ability ID, the same number Warcraft Logs and Wowhead use.
    pub ability_id: u32,
    pub name: String,
}

#[derive(Debug, PartialEq, Eq)]
pub enum AddOutcome {
    Added,
    /// The ability was already tracked, and now has the new name.
    Renamed,
    /// Another ability of this server already has this name (carries that ability's ID).
    NameTaken(u32),
    /// The server already tracks `MAX_TRACKED` abilities.
    Full,
}

/// Tracks `ability_id` under `name` for this guild, or renames it if it is already tracked.
pub async fn add(db: &PgPool, guild_id: GuildId, ability_id: u32, name: &str) -> Result<AddOutcome, sqlx::Error> {
    let tracked = list(db, guild_id).await?;
    let already_tracked = tracked.iter().any(|ability| ability.ability_id == ability_id);

    if let Some(other) = tracked.iter().find(|ability| ability.ability_id != ability_id && ability.name.eq_ignore_ascii_case(name)) {
        return Ok(AddOutcome::NameTaken(other.ability_id));
    }
    if !already_tracked && tracked.len() >= MAX_TRACKED {
        return Ok(AddOutcome::Full);
    }

    sqlx::query(
        "INSERT INTO wcl_tracked_abilities (guild_id, ability_id, name) VALUES ($1, $2, $3) \
         ON CONFLICT (guild_id, ability_id) DO UPDATE SET name = EXCLUDED.name",
    )
    .bind(guild_id.get() as i64)
    .bind(i64::from(ability_id))
    .bind(name)
    .execute(db)
    .await?;

    Ok(if already_tracked { AddOutcome::Renamed } else { AddOutcome::Added })
}

/// Stops tracking the ability with this name (ignoring case). Returns whether there was one.
pub async fn remove(db: &PgPool, guild_id: GuildId, name: &str) -> Result<bool, sqlx::Error> {
    let result = sqlx::query("DELETE FROM wcl_tracked_abilities WHERE guild_id = $1 AND lower(name) = lower($2)")
        .bind(guild_id.get() as i64)
        .bind(name)
        .execute(db)
        .await?;

    Ok(result.rows_affected() == 1)
}

/// Every ability this guild tracks, by name.
pub async fn list(db: &PgPool, guild_id: GuildId) -> Result<Vec<TrackedAbility>, sqlx::Error> {
    let rows = sqlx::query("SELECT ability_id, name FROM wcl_tracked_abilities WHERE guild_id = $1 ORDER BY lower(name), ability_id")
        .bind(guild_id.get() as i64)
        .fetch_all(db)
        .await?;

    rows.iter()
        .map(|row| {
            Ok(TrackedAbility {
                // Stored from a `u32`, so it always converts back.
                ability_id: u32::try_from(row.try_get::<i64, _>("ability_id")?).unwrap_or_default(),
                name: row.try_get("name")?,
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Needs a throwaway Postgres: `TEST_DATABASE_URL=postgres://... cargo test -- --ignored`.
    #[tokio::test]
    #[ignore = "needs TEST_DATABASE_URL pointing at a disposable Postgres database"]
    async fn tracked_abilities_are_scoped_to_their_guild_and_limited() {
        let url = std::env::var("TEST_DATABASE_URL").expect("TEST_DATABASE_URL must be set");
        let db = PgPool::connect(&url).await.unwrap();
        sqlx::migrate!().run(&db).await.unwrap();
        sqlx::query("TRUNCATE wcl_tracked_abilities").execute(&db).await.unwrap();

        let (guild_a, guild_b) = (GuildId::new(111), GuildId::new(222));

        assert_eq!(add(&db, guild_a, 1282408, "Coalesced Venom").await.unwrap(), AddOutcome::Added);
        assert_eq!(add(&db, guild_a, 1299838, "Venom Rupture").await.unwrap(), AddOutcome::Added);
        // The same ability again is a rename, whatever the case of the new name.
        assert_eq!(add(&db, guild_a, 1282408, "coalesced venom (soak)").await.unwrap(), AddOutcome::Renamed);
        // A different ability can't take a name that is in use, ignoring case.
        assert_eq!(add(&db, guild_a, 5, "VENOM RUPTURE").await.unwrap(), AddOutcome::NameTaken(1299838));
        // Another guild is independent, including for names and IDs.
        assert_eq!(add(&db, guild_b, 1282408, "Venom Rupture").await.unwrap(), AddOutcome::Added);

        let names: Vec<String> = list(&db, guild_a).await.unwrap().into_iter().map(|ability| ability.name).collect();
        assert_eq!(names, vec!["coalesced venom (soak)", "Venom Rupture"]);
        assert_eq!(list(&db, guild_b).await.unwrap().len(), 1);

        // Removal is by name, ignoring case, and only in the guild that asked.
        assert!(!remove(&db, guild_b, "coalesced venom (soak)").await.unwrap());
        assert!(remove(&db, guild_a, "COALESCED VENOM (SOAK)").await.unwrap());
        assert!(!remove(&db, guild_a, "coalesced venom (soak)").await.unwrap());

        // The limit stops new abilities, but an already tracked one can still be renamed.
        for id in 100..(100 + MAX_TRACKED as u32 - 1) {
            assert_eq!(add(&db, guild_a, id, &format!("Ability {id}")).await.unwrap(), AddOutcome::Added);
        }
        assert_eq!(list(&db, guild_a).await.unwrap().len(), MAX_TRACKED);
        assert_eq!(add(&db, guild_a, 999, "One too many").await.unwrap(), AddOutcome::Full);
        assert_eq!(add(&db, guild_a, 100, "Renamed at the limit").await.unwrap(), AddOutcome::Renamed);
    }
}
