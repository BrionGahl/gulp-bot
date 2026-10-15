//! `/tldr`: which messages of a channel are summarized, how they are written out for the model,
//! and the cooldown between two summaries of a channel. Plain `std` only, so it can be tested
//! without Discord; channels are their numeric IDs and times Unix seconds.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use super::text::{single_line, truncate_chars};

/// How far back a summary reaches: the last 4 hours.
pub const WINDOW_SECS: i64 = 4 * 60 * 60;

/// A pause this long between two messages ends the conversation being summarized: what was said
/// before it is another conversation.
pub const GAP_SECS: i64 = 60 * 60;

/// How long after a summary of a channel the next one may be made.
pub const COOLDOWN: Duration = Duration::from_secs(60 * 60);

/// The most characters of one message that go into the transcript.
const MAX_LINE_CHARS: usize = 400;

/// One message, as it goes into the transcript.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Line {
    /// When it was sent, in Unix seconds.
    pub at: i64,
    /// When it was sent, as shown to the model (e.g. `21:04`).
    pub time: String,
    /// Who sent it, as the model should name them.
    pub author: String,
    pub text: String,
}

/// The conversation to summarize out of `newest_first` (a channel's messages, the newest first):
/// the messages of the last `WINDOW_SECS` before `now`, back to the first pause of `GAP_SECS` or
/// more between two of them. Returned the oldest first, as a conversation is read.
pub fn conversation(newest_first: Vec<Line>, now: i64) -> Vec<Line> {
    let mut kept: Vec<Line> = Vec::new();
    for line in newest_first {
        if now - line.at > WINDOW_SECS {
            break;
        }
        if let Some(later) = kept.last() {
            if later.at - line.at >= GAP_SECS {
                break;
            }
        }
        kept.push(line);
    }
    kept.reverse();
    kept
}

/// The conversation as text for the model, one `[time] author: text` line per message, each on
/// one line and cut to length. If it all doesn't fit in `max_chars`, the oldest lines are left out;
/// returns how many were.
pub fn transcript(lines: &[Line], max_chars: usize) -> (String, usize) {
    let rendered: Vec<String> = lines
        .iter()
        .map(|line| format!("[{}] {}: {}", line.time, single_line(&line.author), single_line(&truncate_chars(&line.text, MAX_LINE_CHARS))))
        .collect();

    // The newest lines are kept first, then as many older ones as fit.
    let mut kept = 0;
    let mut used = 0;
    for line in rendered.iter().rev() {
        let cost = line.chars().count() + 1;
        if used + cost > max_chars {
            break;
        }
        used += cost;
        kept += 1;
    }

    let omitted = rendered.len() - kept;
    (rendered[omitted..].join("\n"), omitted)
}

/// `seconds` as a reader would say it: `45m`, `2h`, `3h 20m`. Under a minute counts as a minute.
pub fn describe_span(seconds: i64) -> String {
    let minutes = (seconds.max(0) + 59) / 60;
    let minutes = minutes.max(1);
    match (minutes / 60, minutes % 60) {
        (0, minutes) => format!("{minutes}m"),
        (hours, 0) => format!("{hours}h"),
        (hours, minutes) => format!("{hours}h {minutes}m"),
    }
}

/// What a channel's cooldown says about making a summary now.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Cooldown {
    /// Go ahead: the channel is now held until `finish` or `cancel`.
    Ready,
    /// A summary of this channel is being made right now.
    Busy,
    /// The last summary of this channel is still fresh: it is at `link`, was posted at
    /// `posted_unix`, and the next may be made in `remaining`.
    Wait { link: String, posted_unix: i64, remaining: Duration },
}

#[derive(Debug)]
struct Entry {
    started: Instant,
    /// Where the summary is and when it was posted; `None` while it's being made.
    posted: Option<(String, i64)>,
}

/// The cooldown of each channel. In memory only: a restart lets every channel be summarized again.
#[derive(Debug)]
pub struct Cooldowns {
    period: Duration,
    channels: HashMap<u64, Entry>,
}

impl Cooldowns {
    pub fn new(period: Duration) -> Self {
        Self { period, channels: HashMap::new() }
    }

    /// Whether a summary of `channel` may be made now; if so, holds the channel for it.
    pub fn start(&mut self, channel: u64, now: Instant) -> Cooldown {
        let period = self.period;
        self.channels.retain(|_, entry| now.saturating_duration_since(entry.started) < period);

        match self.channels.get(&channel) {
            Some(Entry { posted: None, .. }) => Cooldown::Busy,
            Some(Entry { started, posted: Some((link, posted_unix)) }) => Cooldown::Wait {
                link: link.clone(),
                posted_unix: *posted_unix,
                remaining: period.saturating_sub(now.saturating_duration_since(*started)),
            },
            None => {
                self.channels.insert(channel, Entry { started: now, posted: None });
                Cooldown::Ready
            }
        }
    }

    /// The summary of `channel` is posted at `link`: the cooldown runs from now.
    pub fn finish(&mut self, channel: u64, link: String, posted_unix: i64, now: Instant) {
        self.channels.insert(channel, Entry { started: now, posted: Some((link, posted_unix)) });
    }

    /// No summary of `channel` was posted after all: it may be tried again at once.
    pub fn cancel(&mut self, channel: u64) {
        if self.channels.get(&channel).is_some_and(|entry| entry.posted.is_none()) {
            self.channels.remove(&channel);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: i64 = 1_000_000;
    const MINUTE: i64 = 60;

    fn line(minutes_ago: i64, text: &str) -> Line {
        Line { at: NOW - minutes_ago * MINUTE, time: format!("-{minutes_ago}m"), author: "Kail".to_string(), text: text.to_string() }
    }

    fn texts(lines: &[Line]) -> Vec<&str> {
        lines.iter().map(|line| line.text.as_str()).collect()
    }

    #[test]
    fn the_conversation_is_the_last_four_hours_oldest_first() {
        let newest_first = vec![line(1, "c"), line(30, "b"), line(200, "a"), line(250, "too old")];
        // 200 minutes ago is inside 4 hours; 250 isn't. The 170-minute pause between "a" and "b"
        // would end it anyway, so make the messages close together instead.
        let close = vec![line(1, "c"), line(50, "b"), line(100, "a2"), line(150, "a1"), line(200, "a0"), line(241, "too old")];
        assert_eq!(texts(&conversation(close, NOW)), vec!["a0", "a1", "a2", "b", "c"]);
        assert_eq!(texts(&conversation(newest_first, NOW)), vec!["b", "c"]);
    }

    #[test]
    fn a_pause_of_an_hour_ends_the_conversation() {
        let newest_first = vec![line(5, "now"), line(30, "earlier"), line(91, "before the break"), line(95, "older")];
        // 61 minutes between "earlier" and "before the break".
        assert_eq!(texts(&conversation(newest_first, NOW)), vec!["earlier", "now"]);

        // 59 minutes is not a break.
        let newest_first = vec![line(5, "now"), line(64, "still the same chat")];
        assert_eq!(texts(&conversation(newest_first, NOW)), vec!["still the same chat", "now"]);

        // Exactly an hour is.
        let newest_first = vec![line(5, "now"), line(65, "another chat")];
        assert_eq!(texts(&conversation(newest_first, NOW)), vec!["now"]);
    }

    #[test]
    fn a_quiet_channel_has_no_conversation() {
        assert!(conversation(Vec::new(), NOW).is_empty());
        assert!(conversation(vec![line(300, "long ago")], NOW).is_empty());
        // A last message an hour and more ago is still summarized: the pause is only between messages.
        assert_eq!(texts(&conversation(vec![line(90, "a"), line(100, "b")], NOW)), vec!["b", "a"]);
    }

    #[test]
    fn the_transcript_has_a_line_per_message() {
        let lines = vec![
            Line { at: 1, time: "20:01".to_string(), author: "Kail".to_string(), text: "who pulled?".to_string() },
            Line { at: 2, time: "20:02".to_string(), author: "Sonodruud".to_string(), text: "not me\nI swear".to_string() },
        ];
        let (text, omitted) = transcript(&lines, 10_000);
        assert_eq!(text, "[20:01] Kail: who pulled?\n[20:02] Sonodruud: not me I swear");
        assert_eq!(omitted, 0);
    }

    #[test]
    fn a_long_message_is_cut_and_the_oldest_lines_go_first() {
        let long = Line { at: 1, time: "20:00".to_string(), author: "Kail".to_string(), text: "x".repeat(5000) };
        let (text, _) = transcript(&[long], 10_000);
        assert_eq!(text.matches('x').count(), MAX_LINE_CHARS);

        let lines: Vec<Line> = (0..10).map(|n| Line { at: n, time: "20:00".to_string(), author: "K".to_string(), text: format!("m{n}") }).collect();
        // Each line is "[20:00] K: mN" (13 characters) and a line break.
        let (text, omitted) = transcript(&lines, 14 * 3);
        assert_eq!(omitted, 7);
        assert_eq!(text, "[20:00] K: m7\n[20:00] K: m8\n[20:00] K: m9");

        let (text, omitted) = transcript(&lines, 5);
        assert_eq!((text.as_str(), omitted), ("", 10));
    }

    #[test]
    fn a_span_is_described_in_hours_and_minutes() {
        assert_eq!(describe_span(0), "1m");
        assert_eq!(describe_span(30), "1m");
        assert_eq!(describe_span(45 * 60), "45m");
        assert_eq!(describe_span(2 * 3600), "2h");
        assert_eq!(describe_span(3 * 3600 + 20 * 60), "3h 20m");
        assert_eq!(describe_span(-5), "1m");
    }

    #[test]
    fn a_channel_waits_an_hour_and_is_pointed_at_the_last_summary() {
        let mut cooldowns = Cooldowns::new(COOLDOWN);
        let start = Instant::now();

        assert_eq!(cooldowns.start(1, start), Cooldown::Ready);
        // While it's being made.
        assert_eq!(cooldowns.start(1, start), Cooldown::Busy);
        // Other channels are their own.
        assert_eq!(cooldowns.start(2, start), Cooldown::Ready);

        cooldowns.finish(1, "https://discord.com/channels/9/1/77".to_string(), 500, start);
        let later = start + Duration::from_secs(20 * 60);
        assert_eq!(
            cooldowns.start(1, later),
            Cooldown::Wait { link: "https://discord.com/channels/9/1/77".to_string(), posted_unix: 500, remaining: Duration::from_secs(40 * 60) }
        );

        // After an hour, it may be summarized again.
        assert_eq!(cooldowns.start(1, start + COOLDOWN), Cooldown::Ready);
    }

    #[test]
    fn a_summary_that_failed_does_not_hold_the_channel() {
        let mut cooldowns = Cooldowns::new(COOLDOWN);
        let now = Instant::now();

        assert_eq!(cooldowns.start(1, now), Cooldown::Ready);
        cooldowns.cancel(1);
        assert_eq!(cooldowns.start(1, now), Cooldown::Ready);

        // Cancelling doesn't undo a summary that was posted.
        cooldowns.finish(1, "link".to_string(), 1, now);
        cooldowns.cancel(1);
        assert!(matches!(cooldowns.start(1, now), Cooldown::Wait { .. }));
    }
}
