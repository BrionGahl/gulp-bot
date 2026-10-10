use log::{error, info};
use poise::serenity_prelude::{
    ChannelId, ChannelType, Context, CreateChannel, GuildId, Member, Permissions,
    PermissionOverwrite, PermissionOverwriteType, UserId,
};
use sqlx::{PgPool, Row};

use crate::guild_settings::GuildSettings;
use crate::types::bot::Data;

const CHANNEL_TOPIC_PREFIX: &str = "Personal officer channel for";

/// If `new` currently has the trial or raider role (and isn't themselves an officer), creates a
/// private text channel for them under the personal officer channels category, visible only to
/// them and the officer role. No-ops if the `personal_officer_channels` table already has a row
/// for them, whether the bot created that channel or an administrator registered an existing one.
///
/// Deliberately ignores `old_if_available` rather than gating on a before/after role diff:
/// serenity's cache only has "old" member state when the member was already cached, which isn't
/// reliable for a guild this size (no member chunk request on startup), so that diff produced
/// false positives on unrelated member updates (nickname, avatar, etc.) after every restart.
/// Idempotency comes entirely from the table.
pub async fn handle_role_update(ctx: &Context, data: &Data, new: &Option<Member>) {
    let Some(new_member) = new else { return };
    let guild_id = new_member.guild_id;
    let settings = data.guild_settings.get(guild_id);

    if !has_trial_or_raider_role(&settings, new_member) {
        return;
    }

    // The feature needs both of these registered for the member's guild; without them it's off.
    let (Some(category_id), Some(officer_role)) = (settings.personal_officer_category_id, settings.mod_role_id) else {
        return;
    };
    let user_id = new_member.user.id;

    if new_member.roles.contains(&officer_role) {
        return;
    }

    // Claim the member in the table before touching Discord. The insert is atomic, so of any
    // number of events for the same member racing each other, exactly one gets to create a
    // channel; the rest see the existing row and stop here.
    match claim(&data.db, guild_id, user_id).await {
        Ok(true) => {}
        Ok(false) => return,
        Err(e) => {
            error!("Failed to check for an existing personal officer channel for {}: {}", new_member.user.name, e);
            return;
        }
    }

    let everyone_role = guild_id.everyone_role();
    let topic_marker = topic_marker_for(user_id);

    let permissions = vec![
        PermissionOverwrite {
            allow: Permissions::empty(),
            deny: Permissions::VIEW_CHANNEL,
            kind: PermissionOverwriteType::Role(everyone_role),
        },
        PermissionOverwrite {
            allow: Permissions::VIEW_CHANNEL | Permissions::SEND_MESSAGES | Permissions::READ_MESSAGE_HISTORY,
            deny: Permissions::empty(),
            kind: PermissionOverwriteType::Role(officer_role),
        },
        PermissionOverwrite {
            allow: Permissions::VIEW_CHANNEL | Permissions::SEND_MESSAGES | Permissions::READ_MESSAGE_HISTORY,
            deny: Permissions::empty(),
            kind: PermissionOverwriteType::Member(user_id),
        },
    ];

    let builder = CreateChannel::new(channel_name_for(new_member))
        .kind(ChannelType::Text)
        .category(category_id)
        .topic(format!("{topic_marker} — visible only to them and officers."))
        .permissions(permissions);

    match guild_id.create_channel(&ctx.http, builder).await {
        Ok(channel) => {
            info!("Created personal officer channel #{} for {}", channel.name, new_member.user.name);
            if let Err(e) = register(&data.db, guild_id, user_id, channel.id).await {
                // The claim row is still there, so this can't lead to a duplicate channel; the
                // row just doesn't say which channel it is.
                error!("Created personal officer channel #{} for {} but failed to record it: {}", channel.name, new_member.user.name, e);
            }
        }
        Err(e) => {
            error!("Failed to create personal officer channel for {}: {}", new_member.user.name, e);
            // Give the claim back so a later member update can try again.
            if let Err(e) = unregister(&data.db, guild_id, user_id).await {
                error!("Failed to release the personal officer channel claim for {}: {}", new_member.user.name, e);
            }
        }
    }
}

/// Inserts the row for this member if they don't have one. Returns whether it was inserted, i.e.
/// whether the caller now owns creating their channel.
async fn claim(db: &PgPool, guild_id: GuildId, user_id: UserId) -> Result<bool, sqlx::Error> {
    let result = sqlx::query("INSERT INTO personal_officer_channels (guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING")
        .bind(guild_id.get() as i64)
        .bind(user_id.get() as i64)
        .execute(db)
        .await?;

    Ok(result.rows_affected() == 1)
}

/// Records `channel_id` as this member's personal officer channel, replacing whatever was
/// recorded before. Used both after the bot creates a channel and when an administrator
/// registers one that already existed.
pub async fn register(db: &PgPool, guild_id: GuildId, user_id: UserId, channel_id: ChannelId) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO personal_officer_channels (guild_id, user_id, channel_id) VALUES ($1, $2, $3) \
         ON CONFLICT (guild_id, user_id) DO UPDATE SET channel_id = EXCLUDED.channel_id",
    )
    .bind(guild_id.get() as i64)
    .bind(user_id.get() as i64)
    .bind(channel_id.get() as i64)
    .execute(db)
    .await?;

    Ok(())
}

/// Forgets this member's personal officer channel, so the bot will create one for them again.
/// Returns whether they had a row.
pub async fn unregister(db: &PgPool, guild_id: GuildId, user_id: UserId) -> Result<bool, sqlx::Error> {
    let result = sqlx::query("DELETE FROM personal_officer_channels WHERE guild_id = $1 AND user_id = $2")
        .bind(guild_id.get() as i64)
        .bind(user_id.get() as i64)
        .execute(db)
        .await?;

    Ok(result.rows_affected() == 1)
}

/// Every member of `guild_id` with a row, oldest first. The channel is `None` for a row whose
/// channel was never recorded (see `handle_role_update`).
pub async fn list(db: &PgPool, guild_id: GuildId) -> Result<Vec<(UserId, Option<ChannelId>)>, sqlx::Error> {
    let rows = sqlx::query("SELECT user_id, channel_id FROM personal_officer_channels WHERE guild_id = $1 ORDER BY created_at, user_id")
        .bind(guild_id.get() as i64)
        .fetch_all(db)
        .await?;

    rows.iter()
        .map(|row| {
            let user_id = UserId::new(row.try_get::<i64, _>("user_id")? as u64);
            let channel_id = row.try_get::<Option<i64>, _>("channel_id")?.map(|id| ChannelId::new(id as u64));
            Ok((user_id, channel_id))
        })
        .collect()
}

fn has_trial_or_raider_role(settings: &GuildSettings, member: &Member) -> bool {
    [settings.trial_role_id, settings.raider_role_id].iter().flatten().any(|role_id| member.roles.contains(role_id))
}

fn topic_marker_for(user_id: UserId) -> String {
    format!("{CHANNEL_TOPIC_PREFIX} <@{user_id}>")
}

fn channel_name_for(member: &Member) -> String {
    let base = member.nick.as_deref().unwrap_or(&member.user.name);
    let sanitized: String = base
        .to_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
        .collect();
    let trimmed = sanitized.trim_matches('-');
    let trimmed = if trimmed.is_empty() { "member" } else { trimmed };

    format!("{trimmed}")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Needs a throwaway Postgres: `TEST_DATABASE_URL=postgres://... cargo test -- --ignored`.
    #[tokio::test]
    #[ignore = "needs TEST_DATABASE_URL pointing at a disposable Postgres database"]
    async fn a_member_can_only_be_claimed_once_per_guild() {
        let url = std::env::var("TEST_DATABASE_URL").expect("TEST_DATABASE_URL must be set");
        let db = PgPool::connect(&url).await.unwrap();
        sqlx::migrate!().run(&db).await.unwrap();
        sqlx::query("TRUNCATE personal_officer_channels").execute(&db).await.unwrap();

        let (guild_a, guild_b) = (GuildId::new(111), GuildId::new(222));
        let (alice, bob) = (UserId::new(1), UserId::new(2));

        // Racing claims for the same member: exactly one wins.
        let claims = tokio::join!(claim(&db, guild_a, alice), claim(&db, guild_a, alice), claim(&db, guild_a, alice));
        let won = [claims.0.unwrap(), claims.1.unwrap(), claims.2.unwrap()];
        assert_eq!(won.iter().filter(|won| **won).count(), 1);

        // The same member in another guild is independent.
        assert!(claim(&db, guild_b, alice).await.unwrap());

        // An administrator registering an existing channel blocks creation for that member.
        register(&db, guild_a, bob, ChannelId::new(50)).await.unwrap();
        assert!(!claim(&db, guild_a, bob).await.unwrap());

        // Recording the created channel fills in the claimed row rather than adding one.
        register(&db, guild_a, alice, ChannelId::new(40)).await.unwrap();
        assert_eq!(
            list(&db, guild_a).await.unwrap(),
            vec![(alice, Some(ChannelId::new(40))), (bob, Some(ChannelId::new(50)))],
        );
        assert_eq!(list(&db, guild_b).await.unwrap(), vec![(alice, None)]);

        // Unregistering lets the member be claimed again.
        assert!(unregister(&db, guild_a, bob).await.unwrap());
        assert!(!unregister(&db, guild_a, bob).await.unwrap());
        assert!(claim(&db, guild_a, bob).await.unwrap());
    }
}
