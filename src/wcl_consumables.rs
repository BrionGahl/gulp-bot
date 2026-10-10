use poise::serenity_prelude::GuildId;
use sqlx::{PgPool, Row};

/// The most abilities one server can register for a category. A category is a few IDs (a plain and a
/// demonic healthstone, a potion and its stronger version), and each is a request to the API.
pub const MAX_PER_KIND: usize = 6;

/// What kind of consumable an ability is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, poise::ChoiceParameter)]
pub enum Kind {
    #[name = "Healthstone"]
    Healthstone,
    #[name = "Health potion"]
    HealthPotion,
}

impl Kind {
    pub const ALL: [Kind; 2] = [Kind::Healthstone, Kind::HealthPotion];

    /// How the kind is stored in the database.
    fn column_value(self) -> &'static str {
        match self {
            Kind::Healthstone => "healthstone",
            Kind::HealthPotion => "health_potion",
        }
    }

    fn from_column_value(value: &str) -> Option<Kind> {
        Kind::ALL.into_iter().find(|kind| kind.column_value() == value)
    }

    /// For headings: "Healthstones".
    pub fn plural(self) -> &'static str {
        match self {
            Kind::Healthstone => "Healthstones",
            Kind::HealthPotion => "Health potions",
        }
    }
}

/// An ability registered as a consumable of some kind.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Registered {
    pub kind: Kind,
    pub ability_id: u32,
}

/// The IDs registered for one kind.
pub fn ids(registered: &[Registered], kind: Kind) -> Vec<u32> {
    registered.iter().filter(|entry| entry.kind == kind).map(|entry| entry.ability_id).collect()
}

#[derive(Debug, PartialEq, Eq)]
pub enum AddOutcome {
    Added,
    /// The ability is already registered as this kind.
    AlreadyRegistered,
    /// The ability is registered as the other kind (carries that kind); an ability is one kind only.
    OtherKind(Kind),
    /// The server already has `MAX_PER_KIND` abilities of this kind.
    Full,
}

/// Registers `ability_id` as a `kind` for this guild.
pub async fn add(db: &PgPool, guild_id: GuildId, kind: Kind, ability_id: u32) -> Result<AddOutcome, sqlx::Error> {
    let registered = list(db, guild_id).await?;
    if let Some(existing) = registered.iter().find(|entry| entry.ability_id == ability_id) {
        return Ok(if existing.kind == kind { AddOutcome::AlreadyRegistered } else { AddOutcome::OtherKind(existing.kind) });
    }
    if ids(&registered, kind).len() >= MAX_PER_KIND {
        return Ok(AddOutcome::Full);
    }

    sqlx::query("INSERT INTO wcl_consumable_abilities (guild_id, ability_id, kind) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING")
        .bind(guild_id.get() as i64)
        .bind(i64::from(ability_id))
        .bind(kind.column_value())
        .execute(db)
        .await?;

    Ok(AddOutcome::Added)
}

/// Unregisters `ability_id`, returning what kind it was, or `None` if it wasn't registered.
pub async fn remove(db: &PgPool, guild_id: GuildId, ability_id: u32) -> Result<Option<Kind>, sqlx::Error> {
    let row = sqlx::query("DELETE FROM wcl_consumable_abilities WHERE guild_id = $1 AND ability_id = $2 RETURNING kind")
        .bind(guild_id.get() as i64)
        .bind(i64::from(ability_id))
        .fetch_optional(db)
        .await?;

    Ok(match row {
        Some(row) => Kind::from_column_value(&row.try_get::<String, _>("kind")?),
        None => None,
    })
}

/// Everything this guild has registered, healthstones first, each by ID.
pub async fn list(db: &PgPool, guild_id: GuildId) -> Result<Vec<Registered>, sqlx::Error> {
    let rows = sqlx::query("SELECT ability_id, kind FROM wcl_consumable_abilities WHERE guild_id = $1 ORDER BY kind, ability_id")
        .bind(guild_id.get() as i64)
        .fetch_all(db)
        .await?;

    let mut registered = Vec::new();
    for row in rows {
        // Stored from a `Kind` and a `u32`, so both always convert back; a row edited by hand into
        // something else is skipped.
        let kind = Kind::from_column_value(&row.try_get::<String, _>("kind")?);
        let ability_id = u32::try_from(row.try_get::<i64, _>("ability_id")?).ok();
        if let (Some(kind), Some(ability_id)) = (kind, ability_id) {
            registered.push(Registered { kind, ability_id });
        }
    }
    Ok(registered)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_kind_survives_being_stored_and_read_back() {
        for kind in Kind::ALL {
            assert_eq!(Kind::from_column_value(kind.column_value()), Some(kind));
        }
        assert_eq!(Kind::from_column_value("potion"), None);
        assert_eq!(Kind::Healthstone.plural(), "Healthstones");
        assert_eq!(Kind::HealthPotion.plural(), "Health potions");
    }

    #[test]
    fn the_ids_of_one_kind_are_picked_out() {
        let registered = [
            Registered { kind: Kind::Healthstone, ability_id: 6262 },
            Registered { kind: Kind::HealthPotion, ability_id: 1234768 },
            Registered { kind: Kind::Healthstone, ability_id: 452930 },
        ];

        assert_eq!(ids(&registered, Kind::Healthstone), vec![6262, 452930]);
        assert_eq!(ids(&registered, Kind::HealthPotion), vec![1234768]);
        assert!(ids(&[], Kind::Healthstone).is_empty());
    }

    /// Needs a throwaway Postgres: `TEST_DATABASE_URL=postgres://... cargo test -- --ignored`.
    #[tokio::test]
    #[ignore = "needs TEST_DATABASE_URL pointing at a disposable Postgres database"]
    async fn consumable_abilities_are_scoped_to_their_guild_and_limited() {
        let url = std::env::var("TEST_DATABASE_URL").expect("TEST_DATABASE_URL must be set");
        let db = PgPool::connect(&url).await.unwrap();
        sqlx::migrate!().run(&db).await.unwrap();
        sqlx::query("TRUNCATE wcl_consumable_abilities").execute(&db).await.unwrap();

        let (guild_a, guild_b) = (GuildId::new(111), GuildId::new(222));

        assert_eq!(add(&db, guild_a, Kind::Healthstone, 6262).await.unwrap(), AddOutcome::Added);
        assert_eq!(add(&db, guild_a, Kind::Healthstone, 452930).await.unwrap(), AddOutcome::Added);
        assert_eq!(add(&db, guild_a, Kind::HealthPotion, 1234768).await.unwrap(), AddOutcome::Added);
        // The same ability again, as the same kind or as the other one.
        assert_eq!(add(&db, guild_a, Kind::Healthstone, 6262).await.unwrap(), AddOutcome::AlreadyRegistered);
        assert_eq!(add(&db, guild_a, Kind::HealthPotion, 6262).await.unwrap(), AddOutcome::OtherKind(Kind::Healthstone));
        // Another guild is independent, including for IDs.
        assert_eq!(add(&db, guild_b, Kind::HealthPotion, 6262).await.unwrap(), AddOutcome::Added);

        let registered = list(&db, guild_a).await.unwrap();
        assert_eq!(ids(&registered, Kind::Healthstone), vec![6262, 452930]);
        assert_eq!(ids(&registered, Kind::HealthPotion), vec![1234768]);
        assert_eq!(ids(&list(&db, guild_b).await.unwrap(), Kind::HealthPotion), vec![6262]);

        // Removal returns the kind, and only touches the guild that asked.
        assert_eq!(remove(&db, guild_b, 452930).await.unwrap(), None);
        assert_eq!(remove(&db, guild_a, 452930).await.unwrap(), Some(Kind::Healthstone));
        assert_eq!(remove(&db, guild_a, 452930).await.unwrap(), None);

        // The limit is per kind.
        for id in 100..(100 + MAX_PER_KIND as u32 - 1) {
            assert_eq!(add(&db, guild_a, Kind::Healthstone, id).await.unwrap(), AddOutcome::Added);
        }
        assert_eq!(add(&db, guild_a, Kind::Healthstone, 999).await.unwrap(), AddOutcome::Full);
        assert_eq!(add(&db, guild_a, Kind::HealthPotion, 999).await.unwrap(), AddOutcome::Added);
    }
}
