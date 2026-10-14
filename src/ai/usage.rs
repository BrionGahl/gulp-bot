//! What the assistant has used and where it has failed, for the developers' `/ai` commands. Plain
//! `std` only, so it can be tested without Discord. All of it lives in memory: a restart starts
//! the counts from zero, and the Anthropic Console stays the authority on what was really billed.

use std::collections::{HashMap, VecDeque};
use std::time::{Duration, Instant};

use super::text::{single_line, truncate_chars};

/// Models whose price is known, as the API names them. Offered when a developer picks a model.
pub const KNOWN_MODELS: [&str; 4] = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"];

/// US dollars per million tokens, `(model, input, output)`, from Anthropic's price list. Haiku 5.5
/// costs more for a prompt of over 100,000 tokens; the assistant's prompts are far below that.
const PRICES: [(&str, f64, f64); 5] = [
    ("claude-haiku-5-5", 0.10, 0.50),
    ("claude-sonnet-5-5", 2.0, 10.0),
    ("claude-opus-5-5", 4.0, 20.0),
    ("claude-fable-5-1", 10.0, 50.0),
    ("claude-mythos-5-1", 10.0, 50.0),
];

/// The `(input, output)` price per million tokens of `model`, or `None` if it isn't known. A dated
/// version of a model (`claude-sonnet-5-5-20261001`) costs what the model does.
pub fn price_per_million(model: &str) -> Option<(f64, f64)> {
    PRICES
        .iter()
        .find(|(name, _, _)| model == *name || model.strip_prefix(*name).is_some_and(|rest| rest.starts_with('-')))
        .map(|&(_, input, output)| (input, output))
}

/// What a number of requests used.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Tokens {
    pub requests: u64,
    pub input: u64,
    pub output: u64,
}

impl Tokens {
    fn add(&mut self, input: u64, output: u64) {
        self.requests = self.requests.saturating_add(1);
        self.input = self.input.saturating_add(input);
        self.output = self.output.saturating_add(output);
    }

    /// What these tokens cost with `model`, in US dollars, or `None` if its price isn't known.
    pub fn cost(&self, model: &str) -> Option<f64> {
        let (input, output) = price_per_million(model)?;
        Some((self.input as f64 * input + self.output as f64 * output) / 1_000_000.0)
    }
}

#[derive(Debug)]
struct Entry {
    at: Instant,
    model: String,
    input: u64,
    output: u64,
}

/// The tokens used by each model, since the bot started and in the last `window`.
#[derive(Debug)]
pub struct UsageLog {
    window: Duration,
    totals: HashMap<String, Tokens>,
    recent: VecDeque<Entry>,
}

impl UsageLog {
    pub fn new(window: Duration) -> Self {
        Self { window, totals: HashMap::new(), recent: VecDeque::new() }
    }

    /// Counts a request to `model` that used `input` and `output` tokens.
    pub fn record(&mut self, model: &str, input: u64, output: u64, now: Instant) {
        self.totals.entry(model.to_string()).or_default().add(input, output);

        let window = self.window;
        while self.recent.front().is_some_and(|entry| now.saturating_duration_since(entry.at) >= window) {
            self.recent.pop_front();
        }
        self.recent.push_back(Entry { at: now, model: model.to_string(), input, output });
    }

    /// Everything since the bot started, by model name.
    pub fn since_start(&self) -> Vec<(String, Tokens)> {
        sorted(self.totals.clone())
    }

    /// The last `window`, by model name.
    pub fn recent(&self, now: Instant) -> Vec<(String, Tokens)> {
        let mut by_model: HashMap<String, Tokens> = HashMap::new();
        for entry in self.recent.iter().filter(|entry| now.saturating_duration_since(entry.at) < self.window) {
            by_model.entry(entry.model.clone()).or_default().add(entry.input, entry.output);
        }
        sorted(by_model)
    }
}

fn sorted(by_model: HashMap<String, Tokens>) -> Vec<(String, Tokens)> {
    let mut rows: Vec<(String, Tokens)> = by_model.into_iter().collect();
    rows.sort_by(|a, b| a.0.cmp(&b.0));
    rows
}

/// The usage `rows` as lines for a message: one per model, then a total if there are several.
pub fn describe(rows: &[(String, Tokens)]) -> String {
    if rows.is_empty() {
        return "No requests.".to_string();
    }

    let mut lines = Vec::new();
    let mut total = Tokens::default();
    let mut cost = 0.0;
    let mut unpriced = false;

    for (model, tokens) in rows {
        total.requests = total.requests.saturating_add(tokens.requests);
        total.input = total.input.saturating_add(tokens.input);
        total.output = total.output.saturating_add(tokens.output);

        let price = match tokens.cost(model) {
            Some(model_cost) => {
                cost += model_cost;
                format!("about {}", format_cost(model_cost))
            }
            None => {
                unpriced = true;
                "price unknown".to_string()
            }
        };
        lines.push(format!(
            "`{model}`: {}, {} in / {} out tokens, {price}",
            requests(tokens.requests),
            format_count(tokens.input),
            format_count(tokens.output),
        ));
    }

    if rows.len() > 1 {
        lines.push(format!(
            "**Total**: {}, {} in / {} out tokens, about {}{}",
            requests(total.requests),
            format_count(total.input),
            format_count(total.output),
            format_cost(cost),
            if unpriced { " (not counting the models whose price is unknown)" } else { "" },
        ));
    }
    lines.join("\n")
}

/// `1 request`, `2 requests`.
fn requests(n: u64) -> String {
    format!("{} request{}", format_count(n), if n == 1 { "" } else { "s" })
}

/// `1234567` as `1,234,567`.
pub fn format_count(n: u64) -> String {
    let digits = n.to_string();
    let mut out = String::new();
    for (index, digit) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index) % 3 == 0 {
            out.push(',');
        }
        out.push(digit);
    }
    out
}

/// US dollars: cents from a dollar up, a fraction of a cent below.
pub fn format_cost(usd: f64) -> String {
    if usd >= 1.0 {
        format!("${usd:.2}")
    } else {
        format!("${usd:.4}")
    }
}

/// What a developer typed as a model: `None` for "default" (the model the bot was configured with),
/// or the name, if it looks like one. Whether the API knows it is checked separately.
pub fn normalize_model(input: &str) -> Result<Option<String>, &'static str> {
    let input = input.trim();
    if input.eq_ignore_ascii_case("default") {
        return Ok(None);
    }
    if input.is_empty() || input.chars().count() > 64 {
        return Err("A model name is 1 to 64 characters long.");
    }
    if !input.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '.' | '_')) {
        return Err("A model name has only letters, digits, `-`, `.` and `_`.");
    }
    Ok(Some(input.to_string()))
}

/// The longest explanation of a failure that is kept.
const MAX_DETAIL_CHARS: usize = 200;

/// A request that got no answer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Failure {
    /// When it happened, in Unix seconds.
    pub at_unix: i64,
    /// What went wrong, on one line.
    pub detail: String,
}

/// The last few failures of the API, and how many there have been.
#[derive(Debug)]
pub struct FailureLog {
    max: usize,
    total: u64,
    items: VecDeque<Failure>,
}

impl FailureLog {
    /// Keeps the last `max` failures.
    pub fn new(max: usize) -> Self {
        Self { max, total: 0, items: VecDeque::new() }
    }

    pub fn record(&mut self, at_unix: i64, detail: &str) {
        self.total = self.total.saturating_add(1);
        self.items.push_back(Failure { at_unix, detail: truncate_chars(&single_line(detail), MAX_DETAIL_CHARS) });
        while self.items.len() > self.max {
            self.items.pop_front();
        }
    }

    /// How many failures there have been since the bot started, kept or not.
    pub fn total(&self) -> u64 {
        self.total
    }

    /// The kept failures, the latest first.
    pub fn latest(&self) -> Vec<Failure> {
        self.items.iter().rev().cloned().collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const DAY: Duration = Duration::from_secs(24 * 60 * 60);

    #[test]
    fn a_model_and_its_dated_versions_have_a_price() {
        assert_eq!(price_per_million("claude-sonnet-5-5"), Some((2.0, 10.0)));
        assert_eq!(price_per_million("claude-sonnet-5-5-20261001"), Some((2.0, 10.0)));
        assert_eq!(price_per_million("claude-haiku-5-5"), Some((0.10, 0.50)));
        assert_eq!(price_per_million("claude-opus-5-5"), Some((4.0, 20.0)));
    }

    #[test]
    fn other_models_have_no_price() {
        assert_eq!(price_per_million("claude-sonnet-5-50"), None);
        assert_eq!(price_per_million("claude-sonnet-5"), None);
        assert_eq!(price_per_million("gpt"), None);
        assert_eq!(price_per_million(""), None);
        assert!(KNOWN_MODELS.iter().all(|model| price_per_million(model).is_some()));
    }

    #[test]
    fn tokens_cost_their_share_of_a_million() {
        let tokens = Tokens { requests: 3, input: 500_000, output: 100_000 };
        // 0.5 * $2 + 0.1 * $10
        let cost = tokens.cost("claude-sonnet-5-5").unwrap();
        assert!((cost - 2.0).abs() < 1e-9);
        assert_eq!(tokens.cost("unknown"), None);
    }

    #[test]
    fn usage_is_counted_per_model_since_the_start() {
        let mut log = UsageLog::new(DAY);
        let now = Instant::now();
        log.record("b", 100, 10, now);
        log.record("a", 50, 5, now);
        log.record("b", 100, 10, now);

        assert_eq!(
            log.since_start(),
            vec![
                ("a".to_string(), Tokens { requests: 1, input: 50, output: 5 }),
                ("b".to_string(), Tokens { requests: 2, input: 200, output: 20 }),
            ]
        );
    }

    #[test]
    fn the_recent_usage_only_counts_the_window() {
        let mut log = UsageLog::new(Duration::from_secs(100));
        let start = Instant::now();
        log.record("m", 10, 1, start);
        log.record("m", 20, 2, start + Duration::from_secs(60));

        let later = start + Duration::from_secs(120);
        assert_eq!(log.recent(later), vec![("m".to_string(), Tokens { requests: 1, input: 20, output: 2 })]);
        // Recording drops what has left the window, but the totals keep it.
        log.record("m", 30, 3, later);
        assert_eq!(log.recent(later), vec![("m".to_string(), Tokens { requests: 2, input: 50, output: 5 })]);
        assert_eq!(log.since_start(), vec![("m".to_string(), Tokens { requests: 3, input: 60, output: 6 })]);
        assert!(log.recent(start + Duration::from_secs(1000)).is_empty());
    }

    #[test]
    fn usage_is_described_per_model_with_a_total() {
        assert_eq!(describe(&[]), "No requests.");

        let one = [("claude-sonnet-5-5".to_string(), Tokens { requests: 2, input: 1_500, output: 300 })];
        assert_eq!(describe(&one), "`claude-sonnet-5-5`: 2 requests, 1,500 in / 300 out tokens, about $0.0060");

        let two = [
            ("claude-haiku-5-5".to_string(), Tokens { requests: 1, input: 1_000_000, output: 0 }),
            ("mystery".to_string(), Tokens { requests: 1, input: 10, output: 10 }),
        ];
        let text = describe(&two);
        assert!(text.contains("`mystery`: 1 request, 10 in / 10 out tokens, price unknown"));
        assert!(text.contains("**Total**: 2 requests, 1,000,010 in / 10 out tokens, about $0.1000"));
        assert!(text.contains("not counting the models whose price is unknown"));
    }

    #[test]
    fn numbers_are_formatted_for_reading() {
        assert_eq!(format_count(0), "0");
        assert_eq!(format_count(999), "999");
        assert_eq!(format_count(1_000), "1,000");
        assert_eq!(format_count(1_234_567), "1,234,567");
        assert_eq!(format_cost(0.0123456), "$0.0123");
        assert_eq!(format_cost(12.3456), "$12.35");
    }

    #[test]
    fn a_model_name_is_checked_for_its_shape() {
        assert_eq!(normalize_model(" claude-haiku-5-5 "), Ok(Some("claude-haiku-5-5".to_string())));
        assert_eq!(normalize_model("Default"), Ok(None));
        assert!(normalize_model("").is_err());
        assert!(normalize_model("   ").is_err());
        assert!(normalize_model("two words").is_err());
        assert!(normalize_model("model\nname").is_err());
        assert!(normalize_model("`code`").is_err());
        assert!(normalize_model(&"m".repeat(65)).is_err());
        assert!(normalize_model(&"m".repeat(64)).is_ok());
    }

    #[test]
    fn the_last_failures_are_kept_latest_first() {
        let mut log = FailureLog::new(2);
        assert!(log.latest().is_empty());
        assert_eq!(log.total(), 0);

        log.record(1, "first");
        log.record(2, "second");
        log.record(3, "third");

        assert_eq!(log.total(), 3);
        assert_eq!(
            log.latest(),
            vec![Failure { at_unix: 3, detail: "third".to_string() }, Failure { at_unix: 2, detail: "second".to_string() }]
        );
    }

    #[test]
    fn a_failure_is_kept_on_one_short_line() {
        let mut log = FailureLog::new(5);
        log.record(1, "the API returned 500:\nline two");
        log.record(2, &"x".repeat(500));

        let latest = log.latest();
        assert_eq!(latest[1].detail, "the API returned 500: line two");
        assert_eq!(latest[0].detail.chars().count(), MAX_DETAIL_CHARS);
    }
}
