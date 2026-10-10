use poise::serenity_prelude::GuildId;
use sqlx::{PgPool, Row};

/// The most players one server can ignore.
pub const MAX_IGNORED: usize = 50;

/// The longest name accepted: a character name is at most 12 letters, so this leaves room for a
/// mistyped one without letting a whole sentence in.
pub const MAX_NAME_CHARS: usize = 24;

#[derive(Debug, PartialEq, Eq)]
pub enum AddOutcome {
    Added,
    /// The player is already ignored, possibly under a different capitalisation.
    AlreadyIgnored,
    /// The server already ignores `MAX_IGNORED` players.
    Full,
}

/// Whether two character names are the same one: names compare ignoring case, including the
/// accented letters character names can have.
pub fn same_name(a: &str, b: &str) -> bool {
    a.to_lowercase() == b.to_lowercase()
}

/// Leaves `name` out of this guild's Warcraft Logs overview.
pub async fn add(db: &PgPool, guild_id: GuildId, name: &str) -> Result<AddOutcome, sqlx::Error> {
    let ignored = list(db, guild_id).await?;
    if ignored.iter().any(|existing| same_name(existing, name)) {
        return Ok(AddOutcome::AlreadyIgnored);
    }
    if ignored.len() >= MAX_IGNORED {
        return Ok(AddOutcome::Full);
    }

    sqlx::query("INSERT INTO wcl_ignored_players (guild_id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING")
        .bind(guild_id.get() as i64)
        .bind(name)
        .execute(db)
        .await?;

    Ok(AddOutcome::Added)
}

/// Stops ignoring `name` (in any capitalisation). Returns whether the player was ignored.
pub async fn remove(db: &PgPool, guild_id: GuildId, name: &str) -> Result<bool, sqlx::Error> {
    let ignored = list(db, guild_id).await?;
    let Some(stored) = ignored.iter().find(|existing| same_name(existing, name)) else { return Ok(false) };

    sqlx::query("DELETE FROM wcl_ignored_players WHERE guild_id = $1 AND name = $2")
        .bind(guild_id.get() as i64)
        .bind(stored)
        .execute(db)
        .await?;

    Ok(true)
}

/// Every player this guild ignores, in name order (ignoring case).
pub async fn list(db: &PgPool, guild_id: GuildId) -> Result<Vec<String>, sqlx::Error> {
    let rows = sqlx::query("SELECT name FROM wcl_ignored_players WHERE guild_id = $1")
        .bind(guild_id.get() as i64)
        .fetch_all(db)
        .await?;

    let mut names = rows.iter().map(|row| row.try_get::<String, _>("name")).collect::<Result<Vec<_>, _>>()?;
    names.sort_by_key(|name| name.to_lowercase());
    Ok(names)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_match_ignoring_case_and_accents_case() {
        assert!(same_name("Tritonian", "tritonian"));
        assert!(same_name("Aâyo", "AÂYO"));
        assert!(same_name("Rè", "rÈ"));
        assert!(!same_name("Rè", "Re"));
        assert!(!same_name("Tritonian", "Tritonia"));
    }

    /// Needs a throwaway Postgres: `TEST_DATABASE_URL=postgres://... cargo test -- --ignored`.
    #[tokio::test]
    #[ignore = "needs TEST_DATABASE_URL pointing at a disposable Postgres database"]
    async fn ignored_players_are_scoped_to_their_guild_and_limited() {
        let url = std::env::var("TEST_DATABASE_URL").expect("TEST_DATABASE_URL must be set");
        let db = PgPool::connect(&url).await.unwrap();
        sqlx::migrate!().run(&db).await.unwrap();
        sqlx::query("TRUNCATE wcl_ignored_players").execute(&db).await.unwrap();

        let (guild_a, guild_b) = (GuildId::new(111), GuildId::new(222));

        assert_eq!(add(&db, guild_a, "Tritonian").await.unwrap(), AddOutcome::Added);
        assert_eq!(add(&db, guild_a, "aâyo").await.unwrap(), AddOutcome::Added);
        // The same player in another case is the same player, accents included.
        assert_eq!(add(&db, guild_a, "TRITONIAN").await.unwrap(), AddOutcome::AlreadyIgnored);
        assert_eq!(add(&db, guild_a, "AÂYO").await.unwrap(), AddOutcome::AlreadyIgnored);
        // Another guild is independent.
        assert_eq!(add(&db, guild_b, "Tritonian").await.unwrap(), AddOutcome::Added);

        assert_eq!(list(&db, guild_a).await.unwrap(), vec!["aâyo", "Tritonian"]);

        // Removal ignores case, and only touches the guild that asked.
        assert!(!remove(&db, guild_a, "Graveshrug").await.unwrap());
        assert!(remove(&db, guild_a, "tRiToNiAn").await.unwrap());
        assert!(!remove(&db, guild_a, "Tritonian").await.unwrap());
        assert_eq!(list(&db, guild_b).await.unwrap(), vec!["Tritonian"]);

        // The limit stops new players.
        for n in 0..MAX_IGNORED - 1 {
            assert_eq!(add(&db, guild_a, &format!("Player{n}")).await.unwrap(), AddOutcome::Added);
        }
        assert_eq!(list(&db, guild_a).await.unwrap().len(), MAX_IGNORED);
        assert_eq!(add(&db, guild_a, "OneTooMany").await.unwrap(), AddOutcome::Full);
    }
}
