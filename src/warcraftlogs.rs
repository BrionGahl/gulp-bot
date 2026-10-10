use std::collections::HashMap;
use std::fmt;
use std::time::{Duration, Instant};

use log::info;
use poise::serenity_prelude::{Colour, CreateEmbed, CreateEmbedFooter};
use reqwest::StatusCode;
use serde::de::DeserializeOwned;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::sync::Mutex;

use crate::guild_settings::WclSource;
use crate::types::bot::Error;

const TOKEN_URL: &str = "https://www.warcraftlogs.com/oauth/token";
const API_URL: &str = "https://www.warcraftlogs.com/api/v2/client";

/// Difficulty ID of Mythic raids in the Warcraft Logs API.
const MYTHIC_DIFFICULTY: u32 = 5;

/// Only this many deaths in a pull are counted. Every death after the second is the raid wiping
/// rather than someone's mistake, so the API is asked to ignore everything after it (`wipeCutoff`).
pub const WIPE_CUTOFF: u32 = 2;

/// The most fights asked about in one deaths query. An uncut deaths table came back with exactly
/// 200 entries, which looks like a cap; with the cutoff a fight yields about two entries, so this
/// stays well under it.
const FIGHTS_PER_QUERY: usize = 50;

/// How many of the guild's newest reports are searched for one from the registered uploader.
const REPORTS_TO_SEARCH: u32 = 25;

/// A token is replaced this long before it expires, so one is never sent just as it lapses.
const TOKEN_REFRESH_MARGIN: Duration = Duration::from_secs(60);

/// The purple used by the bot's other embeds (see `helper::create_base_embed`).
const EMBED_COLOUR: u32 = 0xAF69EE;

/// Discord's limits on an embed's title, description and one field's value.
const MAX_TITLE_CHARS: usize = 256;
const MAX_DESCRIPTION_CHARS: usize = 4096;
const MAX_FIELD_CHARS: usize = 1024;

/// How many players each ranking shows.
const MOST_DEATHS_SHOWN: usize = 10;
const FIRST_DEATHS_SHOWN: usize = 5;

/// Client for the Warcraft Logs v2 API, which is public-data only: it authenticates with the
/// bot's own client credentials (no user authorization), so it can read public and unlisted
/// reports but not private ones. See <https://www.warcraftlogs.com/api/docs>.
pub struct WarcraftLogs {
    http: reqwest::Client,
    client_id: String,
    client_secret: String,
    token: Mutex<Option<AccessToken>>,
}

struct AccessToken {
    value: String,
    expires_at: Instant,
}

// Written by hand so that the client secret can never end up in a log line.
impl fmt::Debug for WarcraftLogs {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("WarcraftLogs").finish_non_exhaustive()
    }
}

impl WarcraftLogs {
    pub fn new(http: reqwest::Client, client_id: String, client_secret: String) -> Self {
        Self { http, client_id, client_secret, token: Mutex::new(None) }
    }

    /// The newest report in the guild's recent uploads that came from the registered uploader,
    /// as its report code, or `None` if there isn't one among the guild's newest reports.
    pub async fn latest_report_code(&self, source: &WclSource) -> Result<Option<String>, Error> {
        let variables = json!({ "guildID": source.guild_id, "limit": REPORTS_TO_SEARCH });
        let response: GuildReportsQuery = self.query(GUILD_REPORTS_QUERY, variables).await?;

        Ok(newest_by_uploader(&response.report_data.reports.data, source.uploader_id).map(|report| report.code.clone()))
    }

    /// Everything the overview shows for one report: its Mythic boss pulls, and the deaths in them
    /// with the cutoff applied.
    pub async fn overview(&self, code: &str) -> Result<Overview, Error> {
        let response: ReportQuery = self.query(&report_query(), json!({ "code": code })).await?;
        let report = response.report_data.report.ok_or("That report doesn't exist, or isn't public.")?;

        // Pulls that are still being uploaded are left out: their deaths aren't all there yet.
        let fight_ids: Vec<u32> = report.fights.iter().filter(|fight| !fight.in_progress.unwrap_or(false)).map(|fight| fight.id).collect();

        let mut deaths = Vec::new();
        let query = deaths_query();
        for chunk in fight_ids.chunks(FIGHTS_PER_QUERY) {
            let response: DeathsQuery = self.query(&query, json!({ "code": code, "fightIDs": chunk })).await?;
            let table = response.report_data.report.ok_or("That report doesn't exist, or isn't public.")?.table;
            deaths.extend(table.data.entries);
        }

        info!("Fetched Warcraft Logs report {code}: {} Mythic pulls, {} counted deaths", fight_ids.len(), deaths.len());
        Ok(Overview::build(&report, &deaths))
    }

    /// Sends one GraphQL query and returns its data, or the API's own error messages.
    async fn query<T: DeserializeOwned>(&self, query: &str, variables: Value) -> Result<T, Error> {
        // Twice: a token the API turns down is dropped and a fresh one is tried once.
        for attempt in 0..2 {
            let token = self.access_token().await?;
            let response = self
                .http
                .post(API_URL)
                .bearer_auth(&token)
                .json(&json!({ "query": query, "variables": variables }))
                .send()
                .await?;

            if response.status() == StatusCode::UNAUTHORIZED && attempt == 0 {
                *self.token.lock().await = None;
                continue;
            }
            if !response.status().is_success() {
                let status = response.status();
                let body = response.text().await.unwrap_or_default();
                return Err(format!("Warcraft Logs API returned {status}: {body}").into());
            }
            return parse_response(response.json().await?);
        }
        Err("Warcraft Logs turned down a freshly issued access token.".into())
    }

    /// A valid access token, fetched with the client credentials flow when there is none yet or
    /// the one held is about to expire.
    async fn access_token(&self) -> Result<String, Error> {
        let mut held = self.token.lock().await;
        if let Some(token) = held.as_ref().filter(|token| token.expires_at > Instant::now() + TOKEN_REFRESH_MARGIN) {
            return Ok(token.value.clone());
        }

        let response = self
            .http
            .post(TOKEN_URL)
            .basic_auth(&self.client_id, Some(&self.client_secret))
            .form(&[("grant_type", "client_credentials")])
            .send()
            .await?;
        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(format!("Warcraft Logs refused the client credentials ({status}): {body}").into());
        }

        let issued: TokenResponse = response.json().await?;
        let value = issued.access_token.clone();
        *held = Some(AccessToken { value: issued.access_token, expires_at: Instant::now() + Duration::from_secs(issued.expires_in) });
        Ok(value)
    }
}

#[derive(Deserialize)]
struct TokenResponse {
    access_token: String,
    expires_in: u64,
}

// The queries take their values as GraphQL variables, never spliced into the text, so a report code
// typed into a command can't change what is asked.
fn report_query() -> String {
    format!(
        "query($code: String!) {{ reportData {{ report(code: $code) {{ \
         code title startTime owner {{ id }} \
         fights(difficulty: {MYTHIC_DIFFICULTY}, killType: Encounters) {{ id name kill inProgress }} }} }} }}"
    )
}

const GUILD_REPORTS_QUERY: &str = "query($guildID: Int, $limit: Int) { reportData { \
    reports(guildID: $guildID, limit: $limit) { data { code startTime owner { id } } } } }";

fn deaths_query() -> String {
    format!(
        "query($code: String!, $fightIDs: [Int]) {{ reportData {{ report(code: $code) {{ \
         table(dataType: Deaths, fightIDs: $fightIDs, wipeCutoff: {WIPE_CUTOFF}) }} }} }}"
    )
}

#[derive(Deserialize)]
struct GraphQlResponse<T> {
    data: Option<T>,
    errors: Option<Vec<GraphQlError>>,
}

#[derive(Deserialize)]
struct GraphQlError {
    message: String,
}

/// The data of a response, or the API's error messages if it sent any (even alongside partial data).
fn parse_response<T>(response: GraphQlResponse<T>) -> Result<T, Error> {
    if let Some(errors) = response.errors.filter(|errors| !errors.is_empty()) {
        let messages: Vec<String> = errors.into_iter().map(|error| error.message).collect();
        return Err(format!("Warcraft Logs reported: {}", messages.join("; ")).into());
    }
    response.data.ok_or_else(|| "Warcraft Logs sent no data.".into())
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ReportQuery {
    report_data: ReportData,
}

#[derive(Deserialize)]
struct ReportData {
    report: Option<Report>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Report {
    code: String,
    title: String,
    /// Milliseconds since the Unix epoch.
    start_time: f64,
    fights: Vec<Fight>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Fight {
    id: u32,
    name: String,
    kill: Option<bool>,
    in_progress: Option<bool>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct GuildReportsQuery {
    report_data: GuildReportsData,
}

#[derive(Deserialize)]
struct GuildReportsData {
    reports: ReportPage,
}

#[derive(Deserialize)]
struct ReportPage {
    data: Vec<ReportSummary>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ReportSummary {
    code: String,
    start_time: f64,
    owner: Option<Owner>,
}

#[derive(Deserialize)]
struct Owner {
    id: u32,
}

/// The newest of `reports` that `uploader_id` uploaded.
fn newest_by_uploader(reports: &[ReportSummary], uploader_id: u32) -> Option<&ReportSummary> {
    reports
        .iter()
        .filter(|report| report.owner.as_ref().is_some_and(|owner| owner.id == uploader_id))
        .max_by(|a, b| a.start_time.total_cmp(&b.start_time))
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DeathsQuery {
    report_data: DeathsReportData,
}

#[derive(Deserialize)]
struct DeathsReportData {
    report: Option<DeathsReport>,
}

#[derive(Deserialize)]
struct DeathsReport {
    table: DeathsTable,
}

/// The `table` field is untyped JSON; for deaths it is `{ "data": { "entries": [...] } }`.
#[derive(Deserialize)]
struct DeathsTable {
    data: DeathsData,
}

#[derive(Deserialize)]
struct DeathsData {
    entries: Vec<DeathEntry>,
}

/// One death in one pull. Only the fields the overview uses are read.
#[derive(Deserialize)]
struct DeathEntry {
    name: String,
    /// The class, written without spaces ("DeathKnight").
    #[serde(rename = "type", default)]
    class: String,
    fight: u32,
    /// Milliseconds since the start of the report.
    timestamp: f64,
}

/// What `/wcl report` shows for one report.
#[derive(Debug)]
pub struct Overview {
    code: String,
    title: String,
    /// Unix timestamp, in seconds, of the start of the report.
    started_at: i64,
    encounters: Vec<EncounterSummary>,
    /// Players ranked by how many of their deaths counted.
    deaths: Vec<PlayerCount>,
    /// Players ranked by how many pulls they were the first to die in.
    first_deaths: Vec<PlayerCount>,
}

#[derive(Debug, PartialEq, Eq)]
struct EncounterSummary {
    name: String,
    pulls: u32,
    kills: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PlayerCount {
    name: String,
    class: String,
    count: u32,
}

impl Overview {
    fn build(report: &Report, deaths: &[DeathEntry]) -> Self {
        let mut encounters: Vec<EncounterSummary> = Vec::new();
        for fight in report.fights.iter().filter(|fight| !fight.in_progress.unwrap_or(false)) {
            let position = match encounters.iter().position(|encounter| encounter.name == fight.name) {
                Some(position) => position,
                None => {
                    encounters.push(EncounterSummary { name: fight.name.clone(), pulls: 0, kills: 0 });
                    encounters.len() - 1
                }
            };
            encounters[position].pulls += 1;
            if fight.kill.unwrap_or(false) {
                encounters[position].kills += 1;
            }
        }

        Self {
            code: report.code.clone(),
            title: report.title.clone(),
            started_at: (report.start_time / 1000.0) as i64,
            encounters,
            deaths: tally(deaths.iter()),
            first_deaths: tally(first_death_of_each_pull(deaths).into_iter()),
        }
    }
}

/// Counts deaths per player, most first (ties in name order).
fn tally<'a>(deaths: impl Iterator<Item = &'a DeathEntry>) -> Vec<PlayerCount> {
    let mut counts: HashMap<&str, PlayerCount> = HashMap::new();
    for death in deaths {
        counts
            .entry(death.name.as_str())
            .or_insert_with(|| PlayerCount { name: death.name.clone(), class: death.class.clone(), count: 0 })
            .count += 1;
    }

    let mut ranking: Vec<PlayerCount> = counts.into_values().collect();
    ranking.sort_by(|a, b| b.count.cmp(&a.count).then_with(|| a.name.cmp(&b.name)));
    ranking
}

/// The earliest death in each pull. Deaths at the very same moment keep the order they were listed in.
fn first_death_of_each_pull(deaths: &[DeathEntry]) -> Vec<&DeathEntry> {
    let mut first: HashMap<u32, &DeathEntry> = HashMap::new();
    for death in deaths {
        match first.get(&death.fight) {
            Some(earlier) if earlier.timestamp <= death.timestamp => {}
            _ => {
                first.insert(death.fight, death);
            }
        }
    }
    first.into_values().collect()
}

pub fn report_url(code: &str) -> String {
    format!("https://www.warcraftlogs.com/reports/{code}")
}

/// The report code in what an officer typed: a bare code, or a link such as
/// `https://www.warcraftlogs.com/reports/YRwPxnJ4MKpWFyrg#fight=3`.
pub fn parse_report_code(input: &str) -> Option<String> {
    let input = input.trim();
    let after_path = input.split_once("/reports/").map_or(input, |(_, rest)| rest);
    let code = after_path.split(['/', '?', '#']).next().unwrap_or("");

    (!code.is_empty() && code.len() <= 32 && code.chars().all(|c| c.is_ascii_alphanumeric())).then(|| code.to_string())
}

pub fn overview_embed(overview: &Overview) -> CreateEmbed {
    let mut embed = CreateEmbed::new()
        .title(truncate(&overview.title, MAX_TITLE_CHARS))
        .url(report_url(&overview.code))
        .colour(Colour::from(EMBED_COLOUR))
        .description(overview_description(overview))
        .footer(CreateEmbedFooter::new(format!("Only the first {WIPE_CUTOFF} deaths in each pull are counted.")));

    if !overview.deaths.is_empty() {
        embed = embed
            .field("Most deaths", ranked_list(&overview.deaths, MOST_DEATHS_SHOWN, MAX_FIELD_CHARS), false)
            .field("First to die in a pull", ranked_list(&overview.first_deaths, FIRST_DEATHS_SHOWN, MAX_FIELD_CHARS), false);
    }
    embed
}

fn overview_description(overview: &Overview) -> String {
    let pulls: u32 = overview.encounters.iter().map(|encounter| encounter.pulls).sum();
    if pulls == 0 {
        return format!("<t:{}:F>\nNo Mythic boss pulls in this report.", overview.started_at);
    }

    let kills: u32 = overview.encounters.iter().map(|encounter| encounter.kills).sum();
    let mut description = format!("<t:{}:F>\n**{pulls}** Mythic pulls: {kills} kills, {} wipes\n", overview.started_at, pulls - kills);
    for (listed, encounter) in overview.encounters.iter().enumerate() {
        let line = format!("\n• {} — {} pulls, {} kills", encounter.name, encounter.pulls, encounter.kills);
        let overflow_note = format!("\n…and {} more.", overview.encounters.len() - listed);
        if description.len() + line.len() + overflow_note.len() > MAX_DESCRIPTION_CHARS {
            description.push_str(&overflow_note);
            break;
        }
        description.push_str(&line);
    }
    description
}

/// The top `max_lines` of a ranking, one numbered line each, in at most `max_chars`. If players are
/// left off, the list ends with a line saying how many.
fn ranked_list(players: &[PlayerCount], max_lines: usize, max_chars: usize) -> String {
    let mut list = String::new();
    for (index, player) in players.iter().enumerate() {
        let class = if player.class.is_empty() { String::new() } else { format!(" ({})", display_class(&player.class)) };
        let line = format!("{}. **{}**{class} — {}", index + 1, player.name, player.count);
        let separator = if index == 0 { "" } else { "\n" };
        let overflow_note = format!("\n…and {} more.", players.len() - index);

        if index >= max_lines || list.len() + separator.len() + line.len() + overflow_note.len() > max_chars {
            list.push_str(&overflow_note);
            break;
        }
        list.push_str(separator);
        list.push_str(&line);
    }
    list
}

/// "DeathKnight" as "Death Knight".
fn display_class(class: &str) -> String {
    let mut spaced = String::with_capacity(class.len() + 2);
    for (index, c) in class.chars().enumerate() {
        if index > 0 && c.is_uppercase() {
            spaced.push(' ');
        }
        spaced.push(c);
    }
    spaced
}

fn truncate(text: &str, max_chars: usize) -> String {
    if text.chars().count() <= max_chars {
        return text.to_string();
    }
    let mut cut: String = text.chars().take(max_chars - 1).collect();
    cut.push('…');
    cut
}

#[cfg(test)]
mod tests {
    use super::*;

    fn death(name: &str, class: &str, fight: u32, timestamp: f64) -> DeathEntry {
        DeathEntry { name: name.to_string(), class: class.to_string(), fight, timestamp }
    }

    fn player(name: &str, count: u32) -> PlayerCount {
        PlayerCount { name: name.to_string(), class: "Rogue".to_string(), count }
    }

    #[test]
    fn report_codes_are_read_from_links_and_bare_codes() {
        let code = Some("YRwPxnJ4MKpWFyrg".to_string());
        assert_eq!(parse_report_code("YRwPxnJ4MKpWFyrg"), code);
        assert_eq!(parse_report_code("  YRwPxnJ4MKpWFyrg "), code);
        assert_eq!(parse_report_code("https://www.warcraftlogs.com/reports/YRwPxnJ4MKpWFyrg"), code);
        assert_eq!(parse_report_code("https://www.warcraftlogs.com/reports/YRwPxnJ4MKpWFyrg#fight=3&type=deaths"), code);
        assert_eq!(parse_report_code("https://www.warcraftlogs.com/reports/YRwPxnJ4MKpWFyrg?fight=3"), code);
        assert_eq!(parse_report_code("https://www.warcraftlogs.com/reports/YRwPxnJ4MKpWFyrg/"), code);

        assert_eq!(parse_report_code(""), None);
        assert_eq!(parse_report_code("   "), None);
        assert_eq!(parse_report_code("not a code!"), None);
        assert_eq!(parse_report_code("abc\"){ evil }"), None);
        assert_eq!(parse_report_code(&"a".repeat(33)), None);
    }

    #[test]
    fn the_queries_ask_for_mythic_and_the_cutoff() {
        assert!(report_query().contains("fights(difficulty: 5, killType: Encounters)"));
        assert!(deaths_query().contains("wipeCutoff: 2)"));
    }

    #[test]
    fn api_errors_win_over_data() {
        let response: GraphQlResponse<u32> = serde_json::from_str(r#"{"errors":[{"message":"nope"},{"message":"really"}],"data":5}"#).unwrap();
        let error = parse_response(response).unwrap_err().to_string();
        assert!(error.contains("nope; really"), "{error}");

        let response: GraphQlResponse<u32> = serde_json::from_str(r#"{"data":5}"#).unwrap();
        assert_eq!(parse_response(response).unwrap(), 5);
        let response: GraphQlResponse<u32> = serde_json::from_str(r#"{"errors":[],"data":5}"#).unwrap();
        assert_eq!(parse_response(response).unwrap(), 5);
        let response: GraphQlResponse<u32> = serde_json::from_str(r#"{}"#).unwrap();
        assert!(parse_response(response).is_err());
    }

    /// The shape of a real deaths table response, cut down to a few fields per entry.
    const DEATHS_RESPONSE: &str = r#"{"data":{"reportData":{"report":{"table":{"data":{"entries":[
        {"name":"Graveshrug","id":9,"guid":227378200,"type":"DeathKnight","icon":"DeathKnight-Blood","timestamp":700932,"fight":3,"damage":{"total":747747},"overkill":0},
        {"name":"Rè","id":16,"guid":265890028,"type":"Rogue","icon":"Rogue-Assassination","timestamp":700996,"fight":3,"overkill":0},
        {"name":"Scrublock","id":1,"type":"Warlock","timestamp":900100,"fight":4},
        {"name":"Rè","id":16,"type":"Rogue","timestamp":900050,"fight":4}
    ]}}}}}}"#;

    #[test]
    fn a_deaths_table_is_read_and_ranked() {
        let response: GraphQlResponse<DeathsQuery> = serde_json::from_str(DEATHS_RESPONSE).unwrap();
        let entries = parse_response(response).unwrap().report_data.report.unwrap().table.data.entries;
        assert_eq!(entries.len(), 4);

        // Rè died in both pulls, the others once each; ties are in name order.
        let ranking = tally(entries.iter());
        let names: Vec<(&str, u32)> = ranking.iter().map(|p| (p.name.as_str(), p.count)).collect();
        assert_eq!(names, vec![("Rè", 2), ("Graveshrug", 1), ("Scrublock", 1)]);
        assert_eq!(ranking[0].class, "Rogue");

        // Graveshrug died first in pull 3; in pull 4 Rè was earlier than Scrublock despite being listed after.
        let first = tally(first_death_of_each_pull(&entries).into_iter());
        let names: Vec<(&str, u32)> = first.iter().map(|p| (p.name.as_str(), p.count)).collect();
        assert_eq!(names, vec![("Graveshrug", 1), ("Rè", 1)]);
    }

    #[test]
    fn deaths_at_the_same_moment_keep_their_listed_order() {
        let entries = vec![death("First", "", 1, 500.0), death("Second", "", 1, 500.0)];
        let first = first_death_of_each_pull(&entries);
        assert_eq!(first.len(), 1);
        assert_eq!(first[0].name, "First");
    }

    #[test]
    fn the_newest_report_from_the_uploader_is_picked() {
        let reports: Vec<ReportSummary> = serde_json::from_str(
            r#"[
            {"code":"old","startTime":100,"owner":{"id":7}},
            {"code":"other","startTime":900,"owner":{"id":8}},
            {"code":"new","startTime":500,"owner":{"id":7}},
            {"code":"nobody","startTime":950,"owner":null}
        ]"#,
        )
        .unwrap();

        assert_eq!(newest_by_uploader(&reports, 7).unwrap().code, "new");
        assert_eq!(newest_by_uploader(&reports, 8).unwrap().code, "other");
        assert!(newest_by_uploader(&reports, 9).is_none());
        assert!(newest_by_uploader(&[], 7).is_none());
    }

    fn report(fights: &[(&str, bool, bool)]) -> Report {
        Report {
            code: "ABC".to_string(),
            title: "Raid night".to_string(),
            start_time: 1_791_506_660_309.0,
            fights: fights
                .iter()
                .enumerate()
                .map(|(index, (name, kill, in_progress))| Fight {
                    id: index as u32 + 1,
                    name: name.to_string(),
                    kill: Some(*kill),
                    in_progress: Some(*in_progress),
                })
                .collect(),
        }
    }

    #[test]
    fn the_overview_counts_pulls_and_kills_per_boss() {
        let overview = Overview::build(
            &report(&[("Zul'jan", false, false), ("Zul'jan", false, false), ("Zul'jan", true, false), ("Other", false, false), ("Zul'jan", false, true)]),
            &[],
        );

        // The pull still being uploaded isn't counted.
        assert_eq!(
            overview.encounters,
            vec![
                EncounterSummary { name: "Zul'jan".to_string(), pulls: 3, kills: 1 },
                EncounterSummary { name: "Other".to_string(), pulls: 1, kills: 0 },
            ]
        );
        assert_eq!(overview.started_at, 1_791_506_660);

        let description = overview_description(&overview);
        assert!(description.contains("**4** Mythic pulls: 1 kills, 3 wipes"), "{description}");
        assert!(description.contains("• Zul'jan — 3 pulls, 1 kills"), "{description}");
    }

    #[test]
    fn a_report_without_mythic_pulls_says_so() {
        let overview = Overview::build(&report(&[]), &[]);
        assert!(overview_description(&overview).contains("No Mythic boss pulls"));
    }

    #[test]
    fn a_long_encounter_list_fits_in_a_description() {
        let fights: Vec<(String, bool, bool)> = (0..400).map(|n| (format!("Boss number {n} with a rather long name"), false, false)).collect();
        let fights: Vec<(&str, bool, bool)> = fights.iter().map(|(name, kill, progress)| (name.as_str(), *kill, *progress)).collect();
        let description = overview_description(&Overview::build(&report(&fights), &[]));

        assert!(description.len() <= MAX_DESCRIPTION_CHARS);
        assert!(description.contains("more."));
    }

    #[test]
    fn rankings_are_cut_to_the_lines_and_characters_allowed() {
        let players: Vec<PlayerCount> = (1..=30).map(|n| player(&format!("Player{n}"), 31 - n)).collect();

        let list = ranked_list(&players, 3, MAX_FIELD_CHARS);
        assert_eq!(list.lines().count(), 4);
        assert!(list.starts_with("1. **Player1** (Rogue) — 30\n2. **Player2**"));
        assert!(list.ends_with("…and 27 more."));

        // A list that fits is left whole.
        let list = ranked_list(&players[..2], 10, MAX_FIELD_CHARS);
        assert_eq!(list.lines().count(), 2);
        assert!(!list.contains("more."));

        // Long names can't push a field over Discord's limit.
        let wide: Vec<PlayerCount> = (1..=40).map(|n| player(&"x".repeat(60 + n), 1)).collect();
        let list = ranked_list(&wide, 40, MAX_FIELD_CHARS);
        assert!(list.len() <= MAX_FIELD_CHARS);
        assert!(list.ends_with(" more."));
    }

    /// Calls the real API with a public report:
    /// `WCL_CLIENT_ID=... WCL_CLIENT_SECRET=... WCL_TEST_REPORT=<code> WCL_TEST_GUILD=<id> WCL_TEST_UPLOADER=<id>
    /// cargo test -- --ignored --nocapture`.
    #[tokio::test]
    #[ignore = "needs WCL_CLIENT_ID, WCL_CLIENT_SECRET, WCL_TEST_REPORT, WCL_TEST_GUILD and WCL_TEST_UPLOADER, and calls the real Warcraft Logs API"]
    async fn the_real_api_returns_an_overview_and_finds_the_newest_report() {
        let var = |name: &str| std::env::var(name).unwrap_or_else(|_| panic!("{name} must be set"));
        let client = WarcraftLogs::new(reqwest::Client::new(), var("WCL_CLIENT_ID"), var("WCL_CLIENT_SECRET"));

        let overview = client.overview(&var("WCL_TEST_REPORT")).await.unwrap();
        let pulls: u32 = overview.encounters.iter().map(|encounter| encounter.pulls).sum();
        let counted: u32 = overview.deaths.iter().map(|player| player.count).sum();
        println!("{}\n{}", overview_description(&overview), ranked_list(&overview.deaths, MOST_DEATHS_SHOWN, MAX_FIELD_CHARS));
        println!("{}", ranked_list(&overview.first_deaths, FIRST_DEATHS_SHOWN, MAX_FIELD_CHARS));

        assert!(pulls > 0 && counted > 0);
        // Two deaths per pull, or a few more where several died at the very moment of the cutoff.
        assert!(counted <= pulls * 3, "{counted} deaths counted in {pulls} pulls: the cutoff wasn't applied");

        let source = WclSource { guild_id: var("WCL_TEST_GUILD").parse().unwrap(), uploader_id: var("WCL_TEST_UPLOADER").parse().unwrap() };
        let newest = client.latest_report_code(&source).await.unwrap();
        println!("newest report from the uploader: {newest:?}");
        assert!(newest.is_some());

        let nobody = WclSource { uploader_id: 1, ..source };
        assert_eq!(client.latest_report_code(&nobody).await.unwrap(), None);
        assert!(client.overview("doesnotexist1234").await.is_err());
    }

    #[test]
    fn classes_are_shown_with_spaces() {
        assert_eq!(display_class("DeathKnight"), "Death Knight");
        assert_eq!(display_class("DemonHunter"), "Demon Hunter");
        assert_eq!(display_class("Rogue"), "Rogue");
    }

    #[test]
    fn long_titles_are_cut_with_an_ellipsis() {
        assert_eq!(truncate("short", 256), "short");
        let cut = truncate(&"ä".repeat(300), 256);
        assert_eq!(cut.chars().count(), 256);
        assert!(cut.ends_with('…'));
    }
}
