use std::collections::{HashMap, VecDeque};
use std::time::Duration;

use log::{error, info, warn};
use poise::serenity_prelude::{
    self as serenity, AuditLogEntryId, ChannelId, Colour, Context, CreateAllowedMentions,
    CreateEmbed, CreateEmbedAuthor, CreateMessage, GuildId, Message, MessageId, Timestamp, User,
    UserId,
};
use poise::serenity_prelude::model::guild::audit_log::{Action, MessageAction};
use tokio::sync::Mutex;

use crate::types::bot::Data;

/// How many of the target user's most recent messages to keep so we can repost one if it's
/// deleted. Only messages authored by a guild's registered replay user are ever stored, so this
/// stays cheap.
const MAX_STORED_MESSAGES: usize = 200;

/// Discord writes the audit log entry for a moderator deletion a beat after the gateway delivers
/// the `MessageDelete` event, so we wait before going to look for it.
const AUDIT_LOG_LOOKUP_DELAY: Duration = Duration::from_secs(2);

/// On a fresh process we have no previous audit log state to diff against, so we fall back to
/// "is the newest matching MESSAGE_DELETE entry recent enough to plausibly be the deletion we're
/// handling?". Needs to comfortably cover `AUDIT_LOG_LOOKUP_DELAY` plus Discord's own lag.
const COLD_START_RECENT_SECS: i64 = 20;

/// Discord's hard limit on embed description length; the replay is truncated to fit.
const MAX_DESCRIPTION_CHARS: usize = 4096;

/// The purple used by the bot's other embeds (see `helper::create_base_embed`).
const EMBED_COLOUR: u32 = 0xAF69EE;

#[derive(Debug)]
struct StoredMessage {
    channel_id: ChannelId,
    author_name: String,
    author_avatar_url: Option<String>,
    author_id: UserId,
    content: String,
    timestamp: Timestamp,
    attachments: Vec<StoredAttachment>,
}

#[derive(Debug)]
struct StoredAttachment {
    url: String,
    is_image: bool,
}

#[derive(Debug, Clone, Copy)]
struct SeenDeletion {
    entry_id: AuditLogEntryId,
    count: u64,
}

/// Remembers each guild's replay user's recent messages and, when one is deleted, reposts it — unless
/// the audit log shows someone other than the author (i.e. a moderator) deleted it.
#[derive(Debug)]
pub struct MessageReplay {
    messages: Mutex<MessageBuffer>,
    /// The most recent MESSAGE_DELETE audit log entry we've already attributed to a moderator.
    /// Discord coalesces repeat deletions by the same mod into a single entry (bumping its
    /// `count`), so tracking this lets us still recognise each subsequent deletion.
    last_seen_deletion: Mutex<Option<SeenDeletion>>,
}

#[derive(Debug, Default)]
struct MessageBuffer {
    by_id: HashMap<MessageId, StoredMessage>,
    order: VecDeque<MessageId>,
}

impl MessageReplay {
    pub fn new() -> Self {
        Self {
            messages: Mutex::new(MessageBuffer::default()),
            last_seen_deletion: Mutex::new(None),
        }
    }

    async fn remember(&self, id: MessageId, message: StoredMessage) {
        let mut buffer = self.messages.lock().await;
        if buffer.by_id.insert(id, message).is_none() {
            buffer.order.push_back(id);
        }
        while buffer.order.len() > MAX_STORED_MESSAGES {
            if let Some(evicted) = buffer.order.pop_front() {
                buffer.by_id.remove(&evicted);
            }
        }
    }

    async fn take(&self, id: MessageId) -> Option<StoredMessage> {
        let mut buffer = self.messages.lock().await;
        let message = buffer.by_id.remove(&id)?;
        buffer.order.retain(|queued| *queued != id);
        Some(message)
    }

    async fn forget(&self, id: MessageId) {
        let _ = self.take(id).await;
    }
}

/// Stores the message if it was written by the replay user registered for the guild it was posted
/// in. No-op otherwise.
pub async fn remember_message(data: &Data, message: &Message) {
    let Some(guild_id) = message.guild_id else { return };
    let Some(target) = data.guild_settings.get(guild_id).replay_user_id else { return };
    if message.author.id != target {
        return;
    }

    data.message_replay
        .remember(
            message.id,
            StoredMessage {
                channel_id: message.channel_id,
                author_name: message.author.name.clone(),
                author_avatar_url: message.author.avatar_url(),
                author_id: message.author.id,
                content: message.content.clone(),
                timestamp: message.timestamp,
                attachments: message
                    .attachments
                    .iter()
                    .map(|a| StoredAttachment {
                        url: a.url.clone(),
                        is_image: a.content_type.as_deref().is_some_and(|ct| ct.starts_with("image/")),
                    })
                    .collect(),
            },
        )
        .await;
}

/// A bulk delete always needs Manage Messages and is always logged, so it's definitionally a
/// moderator action — we never replay those, just drop any we were holding.
pub async fn forget_messages(data: &Data, ids: &[MessageId]) {
    for id in ids {
        data.message_replay.forget(*id).await;
    }
}

/// Called for every `MessageDelete`. If the deleted message was one of the target user's that we
/// had stored, repost it in the same channel unless the audit log shows a moderator deleted it.
pub async fn handle_message_delete(
    ctx: &Context,
    data: &Data,
    message_id: MessageId,
    guild_id: Option<GuildId>,
) {
    let Some(stored) = data.message_replay.take(message_id).await else { return };

    if stored.content.is_empty() && stored.attachments.is_empty() {
        return;
    }

    // Outside a guild there's no audit log to consult. We only ever store guild messages in
    // practice, so this is just a guard rather than a real case.
    let Some(guild_id) = guild_id else { return };

    tokio::time::sleep(AUDIT_LOG_LOOKUP_DELAY).await;

    match deleted_by_moderator(ctx, &data.message_replay, guild_id, stored.channel_id, stored.author_id).await {
        Ok(true) => {
            info!(
                "Not replaying deleted message {}: audit log shows a moderator deleted it",
                message_id
            );
        }
        Ok(false) => replay(ctx, &stored).await,
        Err(e) => {
            warn!(
                "Couldn't read audit log to see who deleted message {} ({}); replaying it anyway",
                message_id, e
            );
            replay(ctx, &stored).await;
        }
    }
}

/// Returns `true` if the audit log shows a recent MESSAGE_DELETE, targeting `target` in
/// `channel_id`, that we haven't already accounted for. A user deleting their own message never
/// produces an audit log entry, so "an entry exists" is taken to mean a moderator did it.
async fn deleted_by_moderator(
    ctx: &Context,
    replay: &MessageReplay,
    guild_id: GuildId,
    channel_id: ChannelId,
    target: UserId,
) -> Result<bool, serenity::Error> {
    let logs = guild_id
        .audit_logs(&ctx.http, Some(Action::Message(MessageAction::Delete)), None, None, Some(25))
        .await?;

    // Entries come back newest-first; take the most recent deletion of one of the target's
    // messages in this channel.
    let entry = logs.entries.iter().find(|entry| {
        entry.target_id.map(|id| id.get()) == Some(target.get())
            && entry.options.as_ref().and_then(|opts| opts.channel_id) == Some(channel_id)
    });

    let Some(entry) = entry else {
        // No record of anyone but the author ever deleting the target's messages here.
        return Ok(false);
    };

    let count = entry.options.as_ref().and_then(|opts| opts.count).unwrap_or(1);
    let age_secs = Timestamp::now().unix_timestamp() - entry.id.created_at().unix_timestamp();

    let mut last_seen = replay.last_seen_deletion.lock().await;
    let attributable = match *last_seen {
        // Discord coalesces repeat deletions by one mod into a single entry, keeping its id and
        // bumping `count`, so a genuinely new deletion is either a new id or a higher count.
        Some(prev) => entry.id != prev.entry_id || count > prev.count,
        // Nothing to diff against yet: trust it only if it's recent enough to be this deletion.
        None => age_secs <= COLD_START_RECENT_SECS,
    };
    *last_seen = Some(SeenDeletion { entry_id: entry.id, count });

    Ok(attributable)
}

/// Posts the delete-and-replay embed for `content` as if `author` had written and then deleted it,
/// in `channel_id`. Backs the `/test_replay` debug command — this exercises the embed rendering
/// only, not the audit log check or the message buffer.
pub async fn render_test_replay(ctx: &Context, channel_id: ChannelId, author: &User, content: &str) {
    let stored = StoredMessage {
        channel_id,
        author_name: author.name.clone(),
        author_avatar_url: author.avatar_url(),
        author_id: author.id,
        content: content.to_string(),
        timestamp: Timestamp::now(),
        attachments: Vec::new(),
    };
    replay(ctx, &stored).await;
}

async fn replay(ctx: &Context, stored: &StoredMessage) {
    let mut description = stored.content.clone();

    // One image attachment gets rendered inline on the embed; everything else (non-images, plus
    // any extra images) is listed as links in the description.
    let inline_image = stored.attachments.iter().position(|a| a.is_image);
    let links: Vec<&str> = stored
        .attachments
        .iter()
        .enumerate()
        .filter(|(i, _)| Some(*i) != inline_image)
        .map(|(_, a)| a.url.as_str())
        .collect();
    if !links.is_empty() {
        if !description.is_empty() {
            description.push_str("\n\n");
        }
        description.push_str(&links.join("\n"));
    }

    if description.chars().count() > MAX_DESCRIPTION_CHARS {
        description = description.chars().take(MAX_DESCRIPTION_CHARS - 1).collect::<String>();
        description.push('…');
    }

    let mut author = CreateEmbedAuthor::new(format!("{} deleted a message", stored.author_name));
    if let Some(url) = &stored.author_avatar_url {
        author = author.icon_url(url);
    }

    let mut embed = CreateEmbed::new()
        .author(author)
        .colour(Colour::from(EMBED_COLOUR))
        .timestamp(stored.timestamp);
    if !description.is_empty() {
        embed = embed.description(description);
    }
    if let Some(idx) = inline_image {
        embed = embed.image(stored.attachments[idx].url.as_str());
    }

    let message = CreateMessage::new()
        .embed(embed)
        // A reposted message must never ping anyone (including @everyone) the original mentioned.
        .allowed_mentions(CreateAllowedMentions::new());

    match stored.channel_id.send_message(&ctx.http, message).await {
        Ok(_) => info!("Replayed a message {} deleted in {}", stored.author_name, stored.channel_id),
        Err(e) => error!("Failed to replay deleted message in {}: {}", stored.channel_id, e),
    }
}
