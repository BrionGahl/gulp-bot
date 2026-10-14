//! What the assistant remembers and who may ask it. Plain `std` only, so it can be tested without
//! Discord; channels and users are their numeric IDs.

use std::collections::{HashMap, VecDeque};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    User,
    Assistant,
}

impl Role {
    /// The name the API uses for the role.
    pub fn as_str(self) -> &'static str {
        match self {
            Role::User => "user",
            Role::Assistant => "assistant",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Turn {
    pub role: Role,
    pub content: String,
}

impl Turn {
    pub fn user(content: impl Into<String>) -> Self {
        Self { role: Role::User, content: content.into() }
    }

    pub fn assistant(content: impl Into<String>) -> Self {
        Self { role: Role::Assistant, content: content.into() }
    }
}

/// A turn, and the access level of the member whose exchange it belongs to.
#[derive(Debug)]
struct Stored {
    turn: Turn,
    level: u8,
}

#[derive(Debug)]
struct Conversation {
    turns: VecDeque<Stored>,
    last_used: Instant,
}

/// The recent conversation of each channel. Only complete exchanges (a question and its answer)
/// are stored, so what is sent to the model always alternates user, assistant, user, ... and
/// starts with the user. Kept in memory only: a restart forgets it.
///
/// A channel is shared by members of different access levels, so each exchange is remembered with
/// the level of whoever asked, and is shown only to members of that level or higher. Otherwise
/// something an Officer was told could be repeated to a raider who asks "what did you just say?".
#[derive(Debug)]
pub struct History {
    channels: HashMap<u64, Conversation>,
    max_turns: usize,
    forget_after: Duration,
}

impl History {
    /// Keeps the last `max_turns` turns of a channel, and forgets a channel that has been quiet
    /// for `forget_after`: an hours-old chat is no use as context, but is billed on every request.
    pub fn new(max_turns: usize, forget_after: Duration) -> Self {
        Self { channels: HashMap::new(), max_turns, forget_after }
    }

    /// The channel's remembered turns that a member of access `level` may see, oldest first.
    pub fn snapshot(&mut self, channel: u64, level: u8, now: Instant) -> Vec<Turn> {
        self.forget_stale(now);
        self.channels
            .get(&channel)
            .map(|conversation| {
                conversation
                    .turns
                    .iter()
                    .filter(|stored| stored.level <= level)
                    .map(|stored| stored.turn.clone())
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Remembers a question, asked by a member of access `level`, and the answer it got.
    pub fn record_exchange(&mut self, channel: u64, question: String, answer: String, level: u8, now: Instant) {
        self.forget_stale(now);
        let max_turns = self.max_turns;
        let conversation = self
            .channels
            .entry(channel)
            .or_insert_with(|| Conversation { turns: VecDeque::new(), last_used: now });

        conversation.turns.push_back(Stored { turn: Turn::user(question), level });
        conversation.turns.push_back(Stored { turn: Turn::assistant(answer), level });
        conversation.last_used = now;

        while conversation.turns.len() > max_turns {
            conversation.turns.pop_front();
        }
        // The cut may have left an answer first; the API wants the user to speak first.
        while conversation.turns.front().is_some_and(|stored| stored.turn.role == Role::Assistant) {
            conversation.turns.pop_front();
        }
    }

    fn forget_stale(&mut self, now: Instant) {
        let forget_after = self.forget_after;
        self.channels
            .retain(|_, conversation| now.saturating_duration_since(conversation.last_used) < forget_after);
    }
}

/// At most `max` requests per user in any `window`.
#[derive(Debug)]
pub struct RateLimiter {
    max: usize,
    window: Duration,
    requests: HashMap<u64, VecDeque<Instant>>,
}

impl RateLimiter {
    pub fn new(max: usize, window: Duration) -> Self {
        Self { max, window, requests: HashMap::new() }
    }

    /// Counts a request by `user`, or says how long until they may ask again.
    pub fn check(&mut self, user: u64, now: Instant) -> Result<(), Duration> {
        let window = self.window;
        // Other users' old requests are dropped too, so the map doesn't grow without bound.
        self.requests.retain(|_, times| {
            while times.front().is_some_and(|time| now.saturating_duration_since(*time) >= window) {
                times.pop_front();
            }
            !times.is_empty()
        });

        let times = self.requests.entry(user).or_default();
        if times.len() >= self.max {
            let oldest = times.front().copied().unwrap_or(now);
            return Err(window.saturating_sub(now.saturating_duration_since(oldest)));
        }

        times.push_back(now);
        Ok(())
    }

    /// Takes back the request `check` last counted for `user`: for one that got no answer through no
    /// fault of theirs, or that another limit refused after this one had counted it.
    pub fn undo(&mut self, user: u64) {
        if let Some(times) = self.requests.get_mut(&user) {
            times.pop_back();
            if times.is_empty() {
                self.requests.remove(&user);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const HOUR: Duration = Duration::from_secs(3600);
    const RAIDER: u8 = 1;
    const OFFICER: u8 = 2;

    fn history(max_turns: usize) -> History {
        History::new(max_turns, HOUR)
    }

    #[test]
    fn a_new_channel_remembers_nothing() {
        let mut history = history(6);
        assert!(history.snapshot(1, OFFICER, Instant::now()).is_empty());
    }

    #[test]
    fn exchanges_are_remembered_in_order_per_channel() {
        let mut history = history(6);
        let now = Instant::now();

        history.record_exchange(1, "q1".into(), "a1".into(), RAIDER, now);
        history.record_exchange(1, "q2".into(), "a2".into(), RAIDER, now);
        history.record_exchange(2, "other".into(), "chat".into(), RAIDER, now);

        assert_eq!(
            history.snapshot(1, RAIDER, now),
            vec![Turn::user("q1"), Turn::assistant("a1"), Turn::user("q2"), Turn::assistant("a2")]
        );
        assert_eq!(history.snapshot(2, RAIDER, now), vec![Turn::user("other"), Turn::assistant("chat")]);
    }

    #[test]
    fn what_an_officer_was_told_is_not_shown_to_a_raider() {
        let mut history = history(10);
        let now = Instant::now();

        history.record_exchange(1, "raider q".into(), "raider a".into(), RAIDER, now);
        history.record_exchange(1, "officer q".into(), "secret a".into(), OFFICER, now);
        history.record_exchange(1, "raider q2".into(), "raider a2".into(), RAIDER, now);

        // A raider sees only the raiders' exchanges, still in pairs and starting with a question.
        let seen_by_raider = history.snapshot(1, RAIDER, now);
        assert_eq!(
            seen_by_raider,
            vec![Turn::user("raider q"), Turn::assistant("raider a"), Turn::user("raider q2"), Turn::assistant("raider a2")]
        );
        assert!(seen_by_raider.iter().all(|turn| !turn.content.contains("secret")));

        // An officer sees everything.
        assert_eq!(history.snapshot(1, OFFICER, now).len(), 6);
    }

    #[test]
    fn the_oldest_turns_are_dropped_and_the_user_still_speaks_first() {
        let mut history = history(4);
        let now = Instant::now();
        for n in 1..=5 {
            history.record_exchange(1, format!("q{n}"), format!("a{n}"), RAIDER, now);
        }

        let turns = history.snapshot(1, RAIDER, now);
        assert_eq!(turns, vec![Turn::user("q4"), Turn::assistant("a4"), Turn::user("q5"), Turn::assistant("a5")]);

        // An odd limit would cut an answer loose from its question; the orphan is dropped.
        let mut history = History::new(3, HOUR);
        for n in 1..=3 {
            history.record_exchange(1, format!("q{n}"), format!("a{n}"), RAIDER, now);
        }
        let turns = history.snapshot(1, RAIDER, now);
        assert_eq!(turns.first().map(|turn| turn.role), Some(Role::User));
        assert_eq!(turns, vec![Turn::user("q3"), Turn::assistant("a3")]);
    }

    #[test]
    fn a_quiet_channel_is_forgotten() {
        let mut history = History::new(6, Duration::from_secs(60));
        let start = Instant::now();
        history.record_exchange(1, "q".into(), "a".into(), RAIDER, start);

        assert_eq!(history.snapshot(1, RAIDER, start + Duration::from_secs(59)).len(), 2);
        assert!(history.snapshot(1, RAIDER, start + Duration::from_secs(60)).is_empty());
        // And it starts afresh.
        history.record_exchange(1, "new".into(), "chat".into(), RAIDER, start + Duration::from_secs(61));
        assert_eq!(
            history.snapshot(1, RAIDER, start + Duration::from_secs(61)),
            vec![Turn::user("new"), Turn::assistant("chat")]
        );
    }

    #[test]
    fn roles_are_named_as_the_api_names_them() {
        assert_eq!(Role::User.as_str(), "user");
        assert_eq!(Role::Assistant.as_str(), "assistant");
    }

    #[test]
    fn a_user_is_limited_within_the_window_only() {
        let mut limiter = RateLimiter::new(2, Duration::from_secs(60));
        let start = Instant::now();

        assert_eq!(limiter.check(1, start), Ok(()));
        assert_eq!(limiter.check(1, start + Duration::from_secs(10)), Ok(()));
        // The third is refused, and told when the first leaves the window.
        assert_eq!(limiter.check(1, start + Duration::from_secs(20)), Err(Duration::from_secs(40)));
        // A refused request doesn't count.
        assert_eq!(limiter.check(1, start + Duration::from_secs(60)), Ok(()));
        assert_eq!(limiter.check(1, start + Duration::from_secs(61)), Err(Duration::from_secs(9)));
    }

    #[test]
    fn users_are_limited_separately() {
        let mut limiter = RateLimiter::new(1, Duration::from_secs(60));
        let now = Instant::now();

        assert_eq!(limiter.check(1, now), Ok(()));
        assert!(limiter.check(1, now).is_err());
        assert_eq!(limiter.check(2, now), Ok(()));
    }

    #[test]
    fn a_request_can_be_taken_back() {
        let mut limiter = RateLimiter::new(1, Duration::from_secs(60));
        let now = Instant::now();

        assert_eq!(limiter.check(1, now), Ok(()));
        assert!(limiter.check(1, now).is_err());

        limiter.undo(1);
        assert_eq!(limiter.check(1, now), Ok(()));

        // Taking back what was never counted does nothing.
        limiter.undo(7);
        assert_eq!(limiter.check(7, now), Ok(()));
    }

    #[test]
    fn idle_users_are_not_kept() {
        let mut limiter = RateLimiter::new(1, Duration::from_secs(60));
        let start = Instant::now();
        for user in 0..100 {
            assert_eq!(limiter.check(user, start), Ok(()));
        }
        assert_eq!(limiter.check(1000, start + Duration::from_secs(120)), Ok(()));
        assert_eq!(limiter.requests.len(), 1);
    }
}
