use std::collections::{HashMap, HashSet};
use std::fmt;
use std::sync::Arc;
use std::time::{Duration, Instant};

use log::info;
use poise::serenity_prelude::{Colour, CreateEmbed, CreateEmbedFooter, GuildId};
use reqwest::StatusCode;
use serde::de::DeserializeOwned;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::sync::Mutex;

use crate::guild_settings::{WclCredentials, WclSource};
use crate::types::bot::Error;
use crate::wcl_consumables::{Kind, Registered};
use crate::wcl_mechanics::TrackedAbility;

const TOKEN_URL: &str = "https://www.warcraftlogs.com/oauth/token";
const API_URL: &str = "https://www.warcraftlogs.com/api/v2/client";

/// Difficulty ID of Mythic raids in the Warcraft Logs API.
const MYTHIC_DIFFICULTY: u32 = 5;

/// Only this many deaths in a pull are counted. Every death after the second is the raid wiping
/// rather than someone's mistake, so the API is asked to ignore everything after it (`wipeCutoff`).
/// Damage taken is cut off the same way: nothing after the last counted death.
pub const WIPE_CUTOFF: u32 = 2;

/// The most fights asked about in one query. An uncut deaths table came back with exactly 200
/// entries, which looks like a cap; with the cutoff a fight yields about two entries, so this stays
/// well under it.
const FIGHTS_PER_QUERY: usize = 50;

/// How many of the guild's newest reports are searched for one from the registered uploader.
const REPORTS_TO_SEARCH: u32 = 25;

/// How long a request to Warcraft Logs, token or query, may take before the bot gives up. Without a
/// limit a hung connection would leave the command waiting until Discord's own 15 minutes ran out.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

/// A token is replaced this long before it expires, so one is never sent just as it lapses.
const TOKEN_REFRESH_MARGIN: Duration = Duration::from_secs(60);

/// The purple used by the bot's other embeds (see `helper::create_base_embed`).
const EMBED_COLOUR: u32 = 0xAF69EE;

/// Discord's limits on an embed's title, description and one field's value.
const MAX_TITLE_CHARS: usize = 256;
const MAX_DESCRIPTION_CHARS: usize = 4096;
const MAX_FIELD_CHARS: usize = 1024;
/// Discord's limit on all the text in one embed (title, description, field names and values, footer).
const MAX_EMBED_CHARS: usize = 6000;
/// Room kept free in the overview for the footer note that says abilities were left out.
const FOOTER_NOTE_RESERVE: usize = 100;
/// The longest an ability name is shown as, in a title or a field name (Discord allows 256).
const MAX_ABILITY_NAME_CHARS: usize = 100;

/// How many players each ranking shows.
const MOST_DEATHS_SHOWN: usize = 10;
const FIRST_DEATHS_SHOWN: usize = 5;
const DAMAGE_SHOWN_IN_OVERVIEW: usize = 5;
const DAMAGE_SHOWN: usize = 15;
const CONSUMABLE_PLAYERS_SHOWN: usize = 10;

/// Each guild's Warcraft Logs client, built from the credentials the guild registered
/// (`/settings wcl client`) the first time it is needed and kept, so that its access token is
/// reused from one command to the next.
#[derive(Debug)]
pub struct WarcraftLogsClients {
    http: reqwest::Client,
    clients: std::sync::Mutex<HashMap<GuildId, (WclCredentials, Arc<WarcraftLogs>)>>,
}

impl WarcraftLogsClients {
    pub fn new(http: reqwest::Client) -> Self {
        Self { http, clients: std::sync::Mutex::new(HashMap::new()) }
    }

    /// The guild's client for `credentials`. A client built from credentials the guild has since
    /// replaced is dropped, along with its token.
    pub fn for_guild(&self, guild_id: GuildId, credentials: &WclCredentials) -> Arc<WarcraftLogs> {
        let mut clients = self.clients.lock().unwrap();
        if let Some((held, client)) = clients.get(&guild_id) {
            if held == credentials {
                return client.clone();
            }
        }

        let client = Arc::new(WarcraftLogs::new(self.http.clone(), credentials.client_id.clone(), credentials.client_secret.clone()));
        clients.insert(guild_id, (credentials.clone(), client.clone()));
        client
    }
}

/// Client for the Warcraft Logs v2 API, which is public-data only: it authenticates with a
/// guild's own client credentials (no user authorization), so it can read public and unlisted
/// reports but not private ones. See <https://www.warcraftlogs.com/api/docs>.
pub struct WarcraftLogs {
    http: reqwest::Client,
    client_id: String,
    client_secret: String,
    token: Mutex<Option<AccessToken>>,
    // Fields rather than constants so a test can point the client at a server of its own.
    token_url: String,
    api_url: String,
    timeout: Duration,
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
        Self {
            http,
            client_id,
            client_secret,
            token: Mutex::new(None),
            token_url: TOKEN_URL.to_string(),
            api_url: API_URL.to_string(),
            timeout: REQUEST_TIMEOUT,
        }
    }

    /// The newest report in the guild's recent uploads that came from the registered uploader,
    /// as its report code, or `None` if there isn't one among the guild's newest reports.
    pub async fn latest_report_code(&self, source: &WclSource) -> Result<Option<String>, Error> {
        let variables = json!({ "guildID": source.guild_id, "limit": REPORTS_TO_SEARCH });
        let response: GuildReportsQuery = self.query(GUILD_REPORTS_QUERY, variables).await?;

        Ok(newest_by_uploader(&response.report_data.reports.data, source.uploader_id).map(|report| report.code.clone()))
    }

    /// Everything the overview shows for one report: its Mythic boss pulls, the deaths in them with
    /// the cutoff applied, and the damage taken from each of the `tracked` abilities.
    ///
    /// Players named in `ignored` are left out of all of it: they are in no ranking, and what they
    /// took doesn't count towards any total. (The cutoff is the API's, so an ignored player's death
    /// among the first two in a pull still uses up one of them.)
    pub async fn overview(&self, code: &str, tracked: &[TrackedAbility], ignored: &[String]) -> Result<Overview, Error> {
        let report = self.report(code).await?;
        let fight_ids = finished_fight_ids(report.fights.iter());
        let ignored_names = ignored_set(ignored);

        let mut deaths = self.deaths(code, &fight_ids).await?;
        deaths.retain(|death| !is_ignored(&ignored_names, &death.name));
        let damage = self.damage_taken(code, &fight_ids, tracked, &ignored_names).await?;

        info!("Fetched Warcraft Logs report {code}: {} Mythic pulls, {} counted deaths, {} tracked abilities", fight_ids.len(), deaths.len(), tracked.len());
        Ok(Overview::build(&report, &deaths, damage, ignored.len()))
    }

    /// Who took damage from one ability, over the pulls of the boss whose name contains `boss`, or
    /// of every Mythic boss when there is none.
    /// Players named in `ignored` are left out, as in `overview`.
    pub async fn damage_report(&self, code: &str, boss: Option<&str>, ability: &TrackedAbility, ignored: &[String]) -> Result<DamageReport, Error> {
        let report = self.report(code).await?;
        let fights = select_fights(&report.fights, boss)?;
        let fight_ids: Vec<u32> = fights.iter().map(|fight| fight.id).collect();

        let damage = self
            .damage_taken(code, &fight_ids, std::slice::from_ref(ability), &ignored_set(ignored))
            .await?
            .pop()
            .ok_or("Warcraft Logs sent no damage data.")?;

        info!("Fetched damage taken from {} in Warcraft Logs report {code}: {} pulls", ability.name, fight_ids.len());
        Ok(DamageReport::build(&report, &fights, damage, boss, ignored.len()))
    }

    /// How many healthstones and health potions were used, over the pulls of the boss whose name
    /// contains `boss` or of every Mythic boss when there is none. Every use counts, not only those
    /// before the cutoff: this is about how much the raid consumed. `registered` says which abilities
    /// are which kind. Players named in `ignored` are left out, as in `overview`.
    pub async fn consumables(&self, code: &str, boss: Option<&str>, registered: &[Registered], ignored: &[String]) -> Result<ConsumablesReport, Error> {
        let report = self.report(code).await?;
        let fights = select_fights(&report.fights, boss)?;
        let fight_ids: Vec<u32> = fights.iter().map(|fight| fight.id).collect();

        let ability_ids: Vec<u32> = registered.iter().map(|entry| entry.ability_id).collect();
        let totals = self.ability_totals(code, &fight_ids, "Casts", &ability_ids, false, &ignored_set(ignored)).await?;

        info!("Fetched consumables in Warcraft Logs report {code}: {} pulls, {} registered abilities", fight_ids.len(), registered.len());
        Ok(ConsumablesReport::build(&report, &fights, boss, registered, totals, ignored.len()))
    }

    /// A report with its Mythic boss fights.
    async fn report(&self, code: &str) -> Result<Report, Error> {
        let response: ReportQuery = self.query(&report_query(), json!({ "code": code })).await?;
        response.report_data.report.ok_or_else(|| "That report doesn't exist, or isn't public.".into())
    }

    /// Every counted death in `fight_ids`: the first `WIPE_CUTOFF` of each pull.
    async fn deaths(&self, code: &str, fight_ids: &[u32]) -> Result<Vec<DeathEntry>, Error> {
        let mut deaths = Vec::new();
        let query = deaths_query();
        for chunk in fight_ids.chunks(FIGHTS_PER_QUERY) {
            let response: DeathsQuery = self.query(&query, json!({ "code": code, "fightIDs": chunk })).await?;
            let report = response.report_data.report.ok_or("That report doesn't exist, or isn't public.")?;
            deaths.extend(report.table.data.entries);
        }
        Ok(deaths)
    }

    /// The damage each player took from each of `abilities` in `fight_ids`, up to the cutoff in
    /// every pull, in the order of `abilities`.
    async fn damage_taken(
        &self,
        code: &str,
        fight_ids: &[u32],
        abilities: &[TrackedAbility],
        ignored: &HashSet<String>,
    ) -> Result<Vec<AbilityDamage>, Error> {
        let ability_ids: Vec<u32> = abilities.iter().map(|ability| ability.ability_id).collect();
        let totals = self.ability_totals(code, fight_ids, "DamageTaken", &ability_ids, true, ignored).await?;

        Ok(abilities.iter().zip(totals).map(|(ability, players)| AbilityDamage::build(ability, players)).collect())
    }

    /// For each of `ability_ids`, in order, what each player has in the `data_type` table (damage
    /// taken, or casts) over `fight_ids`: `cut_off` limits it to the first deaths in each pull, as for
    /// damage taken. One request covers all the abilities (a table each, under an alias); pulls
    /// beyond `FIGHTS_PER_QUERY` take further requests, whose totals are added.
    async fn ability_totals(
        &self,
        code: &str,
        fight_ids: &[u32],
        data_type: &str,
        ability_ids: &[u32],
        cut_off: bool,
        ignored: &HashSet<String>,
    ) -> Result<Vec<HashMap<String, (String, u64)>>, Error> {
        if ability_ids.is_empty() {
            return Ok(Vec::new());
        }

        let query = ability_tables_query(data_type, ability_ids, cut_off);
        let mut totals: Vec<HashMap<String, (String, u64)>> = vec![HashMap::new(); ability_ids.len()];
        for chunk in fight_ids.chunks(FIGHTS_PER_QUERY) {
            let response: TablesQuery = self.query(&query, json!({ "code": code, "fightIDs": chunk })).await?;
            let tables = response.report_data.report.ok_or("That report doesn't exist, or isn't public.")?;
            for (index, players) in totals.iter_mut().enumerate() {
                if let Some(table) = tables.get(&alias(index)) {
                    add_totals(players, &table.data.entries, ignored);
                }
            }
        }

        Ok(totals)
    }

    /// Sends one GraphQL query and returns its data, or the API's own error messages.
    async fn query<T: DeserializeOwned>(&self, query: &str, variables: Value) -> Result<T, Error> {
        // Twice: a token the API turns down is dropped and a fresh one is tried once.
        for attempt in 0..2 {
            let token = self.access_token().await?;
            let response = self
                .http
                .post(&self.api_url)
                .timeout(self.timeout)
                .bearer_auth(&token)
                .json(&json!({ "query": query, "variables": variables }))
                .send()
                .await
                .map_err(request_error)?;

            if response.status() == StatusCode::UNAUTHORIZED && attempt == 0 {
                *self.token.lock().await = None;
                continue;
            }
            if !response.status().is_success() {
                let status = response.status();
                let body = response.text().await.unwrap_or_default();
                return Err(format!("Warcraft Logs API returned {status}: {body}").into());
            }
            return parse_response(response.json().await.map_err(request_error)?);
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
            .post(&self.token_url)
            .timeout(self.timeout)
            .basic_auth(&self.client_id, Some(&self.client_secret))
            .form(&[("grant_type", "client_credentials")])
            .send()
            .await
            .map_err(request_error)?;
        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(format!("Warcraft Logs refused the client credentials ({status}): {body}").into());
        }

        let issued: TokenResponse = response.json().await.map_err(request_error)?;
        let value = issued.access_token.clone();
        *held = Some(AccessToken { value: issued.access_token, expires_at: Instant::now() + Duration::from_secs(issued.expires_in) });
        Ok(value)
    }
}

/// A failed request as an error an officer can act on: a timeout says so, with no URL in it.
fn request_error(error: reqwest::Error) -> Error {
    if error.is_timeout() {
        "Warcraft Logs took too long to answer. Try again in a moment.".into()
    } else {
        error.into()
    }
}

#[derive(Deserialize)]
struct TokenResponse {
    access_token: String,
    expires_in: u64,
}

// The queries take their values as GraphQL variables, never spliced into the text, so a report code
// typed into a command can't change what is asked. The one thing built into a query is the list of
// ability IDs, which are numbers.
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

/// The alias of the table for the ability at `index` in a damage query.
fn alias(index: usize) -> String {
    format!("a{index}")
}

/// One table of `data_type` per ability, each filtered to that ability and so listing the players
/// who have it (took the damage, or cast it). `cut_off` asks for the cutoff to be applied. (Not
/// `viewBy: Ability`, whose per-ability player lists hold only the top five.)
fn ability_tables_query(data_type: &str, ability_ids: &[u32], cut_off: bool) -> String {
    let cut = if cut_off { format!(", wipeCutoff: {WIPE_CUTOFF}") } else { String::new() };
    let tables: String = ability_ids
        .iter()
        .enumerate()
        .map(|(index, ability_id)| format!(" {}: table(dataType: {data_type}, fightIDs: $fightIDs, abilityID: {ability_id}{cut})", alias(index)))
        .collect();

    format!("query($code: String!, $fightIDs: [Int]) {{ reportData {{ report(code: $code) {{{tables} }} }} }}")
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

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Report {
    code: String,
    title: String,
    /// Milliseconds since the Unix epoch.
    start_time: f64,
    fights: Vec<Fight>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Fight {
    id: u32,
    name: String,
    kill: Option<bool>,
    in_progress: Option<bool>,
}

impl Fight {
    /// A pull that is still being uploaded is left out everywhere: its events aren't all there yet.
    fn is_finished(&self) -> bool {
        !self.in_progress.unwrap_or(false)
    }
}

fn finished_fight_ids<'a>(fights: impl Iterator<Item = &'a Fight>) -> Vec<u32> {
    fights.filter(|fight| fight.is_finished()).map(|fight| fight.id).collect()
}

/// The finished fights of `fights`, narrowed to those of the boss whose name contains `boss`
/// (ignoring case) when one is given. An error says which bosses the report does have.
fn select_fights<'a>(fights: &'a [Fight], boss: Option<&str>) -> Result<Vec<&'a Fight>, String> {
    let finished: Vec<&Fight> = fights.iter().filter(|fight| fight.is_finished()).collect();
    let Some(boss) = boss.map(str::trim).filter(|boss| !boss.is_empty()) else { return Ok(finished) };

    let needle = boss.to_lowercase();
    let matching: Vec<&Fight> = finished.iter().copied().filter(|fight| fight.name.to_lowercase().contains(&needle)).collect();
    if !matching.is_empty() {
        return Ok(matching);
    }

    let mut names: Vec<&str> = finished.iter().map(|fight| fight.name.as_str()).collect();
    names.sort();
    names.dedup();
    Err(if names.is_empty() {
        "This report has no Mythic boss pulls.".to_string()
    } else {
        format!("No Mythic pulls of a boss matching `{boss}`. The bosses in this report: {}.", names.join(", "))
    })
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

/// The `table` field is untyped JSON; for the tables used here it is `{ "data": { "entries": [...] } }`.
#[derive(Deserialize)]
struct Table<E> {
    data: TableData<E>,
}

#[derive(Deserialize)]
struct TableData<E> {
    entries: Vec<E>,
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
    table: Table<DeathEntry>,
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

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct TablesQuery {
    report_data: TablesReportData,
}

/// The report's tables, by the aliases `ability_tables_query` gave them.
#[derive(Deserialize)]
struct TablesReportData {
    report: Option<HashMap<String, Table<PlayerEntry>>>,
}

/// One player's total for one ability: damage taken from it, or how often they cast it.
#[derive(Deserialize)]
struct PlayerEntry {
    name: String,
    #[serde(rename = "type", default)]
    class: String,
    total: f64,
}

/// Player names in the form they are compared in: lowercase, so capitalisation never matters.
fn ignored_set(names: &[String]) -> HashSet<String> {
    names.iter().map(|name| name.to_lowercase()).collect()
}

fn is_ignored(ignored: &HashSet<String>, name: &str) -> bool {
    ignored.contains(&name.to_lowercase())
}

/// Adds `entries`, except those of ignored players, to the running totals per player. Pulls are
/// queried in chunks, and a player's total over the night is the sum over the chunks.
fn add_totals(players: &mut HashMap<String, (String, u64)>, entries: &[PlayerEntry], ignored: &HashSet<String>) {
    for entry in entries.iter().filter(|entry| !is_ignored(ignored, &entry.name)) {
        let player = players.entry(entry.name.clone()).or_insert_with(|| (entry.class.clone(), 0));
        player.1 += entry.total.max(0.0) as u64;
    }
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
    /// Damage taken from each tracked ability.
    damage: Vec<AbilityDamage>,
    /// How many players were left out of all of it, for the footer.
    ignored: usize,
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

#[derive(Debug, Clone, PartialEq, Eq)]
struct PlayerTotal {
    name: String,
    class: String,
    total: u64,
}

/// The damage taken from one ability: in all, and by each player who took any.
#[derive(Debug, Clone, PartialEq, Eq)]
struct AbilityDamage {
    name: String,
    total: u64,
    /// Most damage first (ties in name order).
    players: Vec<PlayerTotal>,
}

impl AbilityDamage {
    fn build(ability: &TrackedAbility, players: HashMap<String, (String, u64)>) -> Self {
        let players = ranked_players(players);
        Self { name: ability.name.clone(), total: players.iter().map(|player| player.total).sum(), players }
    }
}

/// Players with their totals, most first (ties in name order).
fn ranked_players(players: HashMap<String, (String, u64)>) -> Vec<PlayerTotal> {
    let mut ranking: Vec<PlayerTotal> = players.into_iter().map(|(name, (class, total))| PlayerTotal { name, class, total }).collect();
    ranking.sort_by(|a, b| b.total.cmp(&a.total).then_with(|| a.name.cmp(&b.name)));
    ranking
}

impl Overview {
    fn build(report: &Report, deaths: &[DeathEntry], damage: Vec<AbilityDamage>, ignored: usize) -> Self {
        let mut encounters: Vec<EncounterSummary> = Vec::new();
        for fight in report.fights.iter().filter(|fight| fight.is_finished()) {
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
            damage,
            ignored,
        }
    }
}

/// What `/wcl damage_taken` shows: one ability's damage over some of a report's pulls.
#[derive(Debug)]
pub struct DamageReport {
    code: String,
    title: String,
    /// Unix timestamp, in seconds, of the start of the report.
    started_at: i64,
    /// Which bosses the pulls are of.
    scope: String,
    pulls: usize,
    ability: AbilityDamage,
    /// How many players were left out, for the footer.
    ignored: usize,
}

impl DamageReport {
    fn build(report: &Report, fights: &[&Fight], ability: AbilityDamage, boss: Option<&str>, ignored: usize) -> Self {
        Self {
            code: report.code.clone(),
            title: report.title.clone(),
            started_at: (report.start_time / 1000.0) as i64,
            scope: scope_text(fights, boss),
            pulls: fights.len(),
            ability,
            ignored,
        }
    }
}

/// Which bosses a set of pulls is of, for an embed: the boss names when the pulls were narrowed to a
/// boss, otherwise all of them.
fn scope_text(fights: &[&Fight], boss: Option<&str>) -> String {
    if boss.is_some_and(|boss| !boss.trim().is_empty()) {
        let mut names: Vec<&str> = fights.iter().map(|fight| fight.name.as_str()).collect();
        names.dedup();
        names.join(", ")
    } else {
        "All Mythic bosses".to_string()
    }
}

/// What `/wcl consumables` shows: how many healthstones and health potions were used.
#[derive(Debug)]
pub struct ConsumablesReport {
    code: String,
    title: String,
    /// Unix timestamp, in seconds, of the start of the report.
    started_at: i64,
    /// Which bosses the pulls are of.
    scope: String,
    pulls: usize,
    /// One entry for each kind, healthstones first.
    uses: Vec<ConsumableUse>,
    /// How many players were left out, for the footer.
    ignored: usize,
}

/// The uses of one kind of consumable: in all, and by each player who used any.
#[derive(Debug, PartialEq, Eq)]
struct ConsumableUse {
    kind: Kind,
    /// Whether any ability is registered for this kind; if not, there is nothing to count.
    registered: bool,
    total: u64,
    /// Most uses first (ties in name order).
    players: Vec<PlayerTotal>,
}

impl ConsumablesReport {
    /// `totals` holds each registered ability's players, in the order of `registered`; the
    /// abilities of one kind are added together (a plain and a demonic healthstone are both
    /// healthstones).
    fn build(
        report: &Report,
        fights: &[&Fight],
        boss: Option<&str>,
        registered: &[Registered],
        totals: Vec<HashMap<String, (String, u64)>>,
        ignored: usize,
    ) -> Self {
        let uses = Kind::ALL
            .into_iter()
            .map(|kind| {
                let mut players: HashMap<String, (String, u64)> = HashMap::new();
                let mut any_registered = false;
                for (_, per_ability) in registered.iter().zip(&totals).filter(|(entry, _)| entry.kind == kind) {
                    any_registered = true;
                    for (name, (class, total)) in per_ability {
                        players.entry(name.clone()).or_insert_with(|| (class.clone(), 0)).1 += total;
                    }
                }

                let players = ranked_players(players);
                ConsumableUse { kind, registered: any_registered, total: players.iter().map(|player| player.total).sum(), players }
            })
            .collect();

        Self {
            code: report.code.clone(),
            title: report.title.clone(),
            started_at: (report.start_time / 1000.0) as i64,
            scope: scope_text(fights, boss),
            pulls: fights.len(),
            uses,
            ignored,
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

/// The footer of the overview and damage embeds: what is counted, and how many players are left out.
fn footer_text(ignored: usize) -> String {
    format!("Only the first {WIPE_CUTOFF} deaths in each pull count, and damage taken up to the last of them.{}", ignored_note(ignored))
}

/// The footer of the consumables embed.
fn consumables_footer(ignored: usize) -> String {
    format!("Every use in every Mythic pull counts, including once the raid had started wiping.{}", ignored_note(ignored))
}

/// A sentence for a footer saying how many players are left out, or nothing if none are.
fn ignored_note(ignored: usize) -> String {
    match ignored {
        0 => String::new(),
        1 => " 1 ignored player is left out.".to_string(),
        count => format!(" {count} ignored players are left out."),
    }
}

/// The characters of an embed that count towards Discord's limit on its total size.
fn embed_chars(title: &str, description: &str, footer: &str, fields: &[(String, String)]) -> usize {
    let fields: usize = fields.iter().map(|(name, value)| name.chars().count() + value.chars().count()).sum();
    title.chars().count() + description.chars().count() + footer.chars().count() + fields
}

pub fn overview_embed(overview: &Overview) -> CreateEmbed {
    let title = truncate(&overview.title, MAX_TITLE_CHARS);
    let description = overview_description(overview);

    let mut fields: Vec<(String, String)> = Vec::new();
    if !overview.deaths.is_empty() {
        fields.push(("Most deaths".to_string(), ranked_list(&death_rows(&overview.deaths), MOST_DEATHS_SHOWN, MAX_FIELD_CHARS)));
        fields.push(("First to die in a pull".to_string(), ranked_list(&death_rows(&overview.first_deaths), FIRST_DEATHS_SHOWN, MAX_FIELD_CHARS)));
    }

    // An embed holds at most 6000 characters in all, so tracked abilities are added while they fit.
    let mut used = embed_chars(&title, &description, &footer_text(overview.ignored), &fields) + FOOTER_NOTE_RESERVE;
    let mut left_out = 0;
    let pulls: u32 = overview.encounters.iter().map(|encounter| encounter.pulls).sum();
    for ability in &overview.damage {
        let field = damage_field(ability, DAMAGE_SHOWN_IN_OVERVIEW, pulls);
        let size = field.0.chars().count() + field.1.chars().count();
        if used + size > MAX_EMBED_CHARS {
            left_out += 1;
            continue;
        }
        used += size;
        fields.push(field);
    }

    let footer = if left_out > 0 {
        format!("{} {left_out} more tracked abilities didn't fit; see /wcl damage_taken.", footer_text(overview.ignored))
    } else {
        footer_text(overview.ignored)
    };

    CreateEmbed::new()
        .title(title)
        .url(report_url(&overview.code))
        .colour(Colour::from(EMBED_COLOUR))
        .description(description)
        .fields(fields.into_iter().map(|(name, value)| (name, value, false)))
        .footer(CreateEmbedFooter::new(footer))
}

/// The field for one tracked ability: its title, the total damage taken from it over all `pulls`
/// of the night, and the players who took the most of it.
fn damage_field(ability: &AbilityDamage, shown: usize, pulls: u32) -> (String, String) {
    let name = format!("{} — damage taken", truncate(&ability.name, MAX_ABILITY_NAME_CHARS));
    let value = if ability.players.is_empty() {
        "Nobody took damage from this. If that's unexpected, check its ability ID.".to_string()
    } else {
        let total = format!("Total: **{}** over {}\n", format_damage(ability.total), plural(pulls, "pull"));
        format!("{total}{}", ranked_list(&damage_rows(ability), shown, MAX_FIELD_CHARS - total.len()))
    };
    (name, value)
}

/// "1 pull", "34 pulls".
fn plural(count: u32, noun: &str) -> String {
    if count == 1 {
        format!("1 {noun}")
    } else {
        format!("{count} {noun}s")
    }
}

pub fn damage_embed(report: &DamageReport) -> CreateEmbed {
    let ability = &report.ability;
    let embed = CreateEmbed::new()
        .title(format!("{} — damage taken", truncate(&ability.name, MAX_ABILITY_NAME_CHARS)))
        .url(report_url(&report.code))
        .colour(Colour::from(EMBED_COLOUR))
        .footer(CreateEmbedFooter::new(footer_text(report.ignored)));

    let report_title = truncate(&report.title, MAX_ABILITY_NAME_CHARS);
    if report.pulls == 0 {
        return embed.description(format!("<t:{}:F> · {report_title}\nNo Mythic boss pulls in this report.", report.started_at));
    }

    let description = format!(
        "<t:{}:F> · {report_title}\n**{}**, {} pulls\nTotal damage taken: **{}**",
        report.started_at,
        report.scope,
        report.pulls,
        format_damage(ability.total),
    );
    // Cut so that however many bosses the scope names, the description stays within Discord's limit.
    let embed = embed.description(truncate(&description, MAX_DESCRIPTION_CHARS));

    if ability.players.is_empty() {
        return embed.field(
            "Who took it",
            "Nobody took damage from this in those pulls. If that's unexpected, check the ability ID: Warcraft Logs can log an ability's damage under a different ID than its spell page shows.",
            false,
        );
    }
    embed.field("Who took it", ranked_list(&damage_rows(ability), DAMAGE_SHOWN, MAX_FIELD_CHARS), false)
}

pub fn consumables_embed(report: &ConsumablesReport) -> CreateEmbed {
    let embed = CreateEmbed::new()
        .title("Consumables used")
        .url(report_url(&report.code))
        .colour(Colour::from(EMBED_COLOUR))
        .footer(CreateEmbedFooter::new(consumables_footer(report.ignored)));

    let report_title = truncate(&report.title, MAX_ABILITY_NAME_CHARS);
    if report.pulls == 0 {
        return embed.description(format!("<t:{}:F> · {report_title}\nNo Mythic boss pulls in this report.", report.started_at));
    }

    // Cut so that however many bosses the scope names, the description stays within Discord's limit.
    let description = format!("<t:{}:F> · {report_title}\n**{}**, {}", report.started_at, report.scope, plural(report.pulls as u32, "pull"));
    embed.description(truncate(&description, MAX_DESCRIPTION_CHARS)).fields(report.uses.iter().map(consumable_field))
}

/// The field for one kind of consumable: how many were used in all, and by whom. Always has a
/// value, since Discord turns down an empty one.
fn consumable_field(used: &ConsumableUse) -> (String, String, bool) {
    if !used.registered {
        return (used.kind.plural().to_string(), "None registered. Add one with `/settings wcl consumable_add`.".to_string(), false);
    }

    let name = format!("{} — {} used", used.kind.plural(), used.total);
    let value = if used.players.is_empty() {
        "None used.".to_string()
    } else {
        ranked_list(&count_rows(&used.players), CONSUMABLE_PLAYERS_SHOWN, MAX_FIELD_CHARS)
    };
    (name, value, false)
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

/// One line of a ranking: a player, their class, and what is being counted.
struct Row {
    name: String,
    class: String,
    value: String,
}

fn death_rows(players: &[PlayerCount]) -> Vec<Row> {
    players.iter().map(|player| Row { name: player.name.clone(), class: player.class.clone(), value: player.count.to_string() }).collect()
}

/// Each player's count, for a ranking of how many they used.
fn count_rows(players: &[PlayerTotal]) -> Vec<Row> {
    players.iter().map(|player| Row { name: player.name.clone(), class: player.class.clone(), value: player.total.to_string() }).collect()
}

/// Each player's damage and their share of everything taken from the ability.
fn damage_rows(ability: &AbilityDamage) -> Vec<Row> {
    ability
        .players
        .iter()
        .map(|player| {
            let share = if ability.total == 0 { 0.0 } else { player.total as f64 * 100.0 / ability.total as f64 };
            Row { name: player.name.clone(), class: player.class.clone(), value: format!("{} ({share:.1}%)", format_damage(player.total)) }
        })
        .collect()
}

/// The top `max_lines` of a ranking, one numbered line each, in at most `max_chars`. If players are
/// left off, the list ends with a line saying how many.
fn ranked_list(rows: &[Row], max_lines: usize, max_chars: usize) -> String {
    let mut list = String::new();
    for (index, row) in rows.iter().enumerate() {
        let class = if row.class.is_empty() { String::new() } else { format!(" ({})", display_class(&row.class)) };
        let line = format!("{}. **{}**{class} — {}", index + 1, row.name, row.value);
        let separator = if index == 0 { "" } else { "\n" };
        let overflow_note = format!("\n…and {} more.", rows.len() - index);

        if index >= max_lines || list.len() + separator.len() + line.len() + overflow_note.len() > max_chars {
            list.push_str(&overflow_note);
            break;
        }
        list.push_str(separator);
        list.push_str(&line);
    }
    list
}

/// Damage as a short number: 3.5B, 200.3M, 12.3K.
fn format_damage(amount: u64) -> String {
    let (value, suffix) = match amount {
        1_000_000_000.. => (amount as f64 / 1e9, "B"),
        1_000_000.. => (amount as f64 / 1e6, "M"),
        1_000.. => (amount as f64 / 1e3, "K"),
        _ => return amount.to_string(),
    };
    format!("{value:.1}{suffix}")
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

    fn tracked(ability_id: u32, name: &str) -> TrackedAbility {
        TrackedAbility { ability_id, name: name.to_string() }
    }

    #[test]
    fn a_guilds_client_is_kept_until_its_credentials_change() {
        let credentials = |secret: &str| WclCredentials { client_id: "id".to_string(), client_secret: secret.to_string() };
        let clients = WarcraftLogsClients::new(reqwest::Client::new());
        let (guild_a, guild_b) = (GuildId::new(1), GuildId::new(2));

        let first = clients.for_guild(guild_a, &credentials("one"));
        assert!(Arc::ptr_eq(&first, &clients.for_guild(guild_a, &credentials("one"))));
        // Another guild gets a client of its own even with the same credentials.
        assert!(!Arc::ptr_eq(&first, &clients.for_guild(guild_b, &credentials("one"))));

        let replaced = clients.for_guild(guild_a, &credentials("two"));
        assert!(!Arc::ptr_eq(&first, &replaced));
        assert_eq!(replaced.client_secret, "two");
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
    fn the_ability_tables_query_has_a_table_for_each_ability_with_or_without_the_cutoff() {
        // Damage taken is cut off at the second death in each pull.
        assert_eq!(
            ability_tables_query("DamageTaken", &[1282408, 1299838], true),
            "query($code: String!, $fightIDs: [Int]) { reportData { report(code: $code) { \
             a0: table(dataType: DamageTaken, fightIDs: $fightIDs, abilityID: 1282408, wipeCutoff: 2) \
             a1: table(dataType: DamageTaken, fightIDs: $fightIDs, abilityID: 1299838, wipeCutoff: 2) } } }"
        );
        // Consumables are every cast, so there is no cutoff. Only numbers go into the query.
        assert_eq!(
            ability_tables_query("Casts", &[6262], false),
            "query($code: String!, $fightIDs: [Int]) { reportData { report(code: $code) { \
             a0: table(dataType: Casts, fightIDs: $fightIDs, abilityID: 6262) } } }"
        );
    }

    fn registered(kind: Kind, ability_id: u32) -> Registered {
        Registered { kind, ability_id }
    }

    fn players_of(players: &[(&str, &str, u64)]) -> HashMap<String, (String, u64)> {
        players.iter().map(|(name, class, total)| (name.to_string(), (class.to_string(), *total))).collect()
    }

    #[test]
    fn the_abilities_of_one_kind_are_added_together() {
        let night = report(&[("Zul'jan", false, false), ("Zul'jan", false, false)]);
        let fights: Vec<&Fight> = night.fights.iter().collect();
        let registered = [registered(Kind::Healthstone, 6262), registered(Kind::Healthstone, 452930), registered(Kind::HealthPotion, 1234768)];
        // In the order of `registered`: the plain healthstone, the demonic one, then the potion.
        let totals = vec![
            players_of(&[("Saven", "Paladin", 24), ("Scrublock", "Warlock", 2)]),
            players_of(&[("Scrublock", "Warlock", 35)]),
            players_of(&[("Aâyo", "Hunter", 8)]),
        ];

        let report = ConsumablesReport::build(&night, &fights, None, &registered, totals, 0);

        let stones = &report.uses[0];
        assert_eq!((stones.kind, stones.registered, stones.total), (Kind::Healthstone, true, 61));
        // Scrublock's two kinds of healthstone are one person's total.
        let ranking: Vec<(&str, u64)> = stones.players.iter().map(|player| (player.name.as_str(), player.total)).collect();
        assert_eq!(ranking, vec![("Scrublock", 37), ("Saven", 24)]);
        let potions = &report.uses[1];
        assert_eq!((potions.kind, potions.registered, potions.total), (Kind::HealthPotion, true, 8));
        assert_eq!(report.pulls, 2);
        assert_eq!(report.scope, "All Mythic bosses");
    }

    #[test]
    fn a_kind_with_nothing_registered_or_nothing_used_says_so() {
        let night = report(&[("Zul'jan", false, false)]);
        let fights: Vec<&Fight> = night.fights.iter().collect();
        // Only healthstones are registered, and nobody used one.
        let report = ConsumablesReport::build(&night, &fights, None, &[registered(Kind::Healthstone, 6262)], vec![HashMap::new()], 0);

        let value = serde_json::to_value(consumables_embed(&report)).unwrap();
        let fields = value["fields"].as_array().unwrap();
        assert_eq!(fields.len(), 2);
        assert_eq!((fields[0]["name"].as_str().unwrap(), fields[0]["value"].as_str().unwrap()), ("Healthstones — 0 used", "None used."));
        assert_eq!(fields[1]["name"], "Health potions");
        assert!(fields[1]["value"].as_str().unwrap().contains("/settings wcl consumable_add"));

        // No pulls at all: no fields, and nothing Discord would turn down as empty.
        let empty = ConsumablesReport::build(&night, &[], None, &[], Vec::new(), 0);
        let value = serde_json::to_value(consumables_embed(&empty)).unwrap();
        assert!(value["description"].as_str().unwrap().contains("No Mythic boss pulls") && value["fields"].is_null());
    }

    #[test]
    fn the_consumables_embed_lists_the_top_users_and_stays_within_limits() {
        let night = report(&[("Zul'jan", false, false), ("Zul'jan", false, false), ("Zul'jan", true, false)]);
        let fights: Vec<&Fight> = night.fights.iter().collect();
        let players: HashMap<String, (String, u64)> = (0..40).map(|n| (format!("Player{n:06}"), ("DemonHunter".to_string(), 100 - n as u64))).collect();
        let registered = [registered(Kind::Healthstone, 1), registered(Kind::HealthPotion, 2)];

        let report = ConsumablesReport::build(&night, &fights, Some("zul"), &registered, vec![players.clone(), players], 2);
        let embed = consumables_embed(&report);
        let value = serde_json::to_value(&embed).unwrap();

        assert_eq!(value["title"], "Consumables used");
        let description = value["description"].as_str().unwrap();
        assert!(description.contains("**Zul'jan**, 3 pulls"), "{description}");
        // The top ten, then a note for the rest, and the total is every player's uses added up.
        let stones = &value["fields"][0];
        assert_eq!(stones["name"], format!("Healthstones — {} used", (0..40u64).map(|n| 100 - n).sum::<u64>()));
        assert_eq!(stones["value"].as_str().unwrap().lines().count(), CONSUMABLE_PLAYERS_SHOWN + 1);
        assert!(stones["value"].as_str().unwrap().starts_with("1. **Player000000** (Demon Hunter) — 100"));
        assert!(embed_text_size(&embed) <= MAX_EMBED_CHARS);

        let footer = value["footer"]["text"].as_str().unwrap();
        assert!(footer.starts_with("Every use in every Mythic pull counts") && footer.ends_with("2 ignored players are left out."), "{footer}");
    }

    /// A stand-in for Warcraft Logs on this machine, returning its address: it answers a token
    /// request, and never answers anything else.
    async fn silent_api() -> String {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(async move {
            while let Ok((mut stream, _)) = listener.accept().await {
                tokio::spawn(async move {
                    let mut request = vec![0u8; 4096];
                    let read = stream.read(&mut request).await.unwrap_or(0);
                    if request[..read].starts_with(b"POST /oauth") {
                        let body = r#"{"access_token":"test","expires_in":3600}"#;
                        let response = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
                        let _ = stream.write_all(response.as_bytes()).await;
                    } else {
                        // Hold the connection open, saying nothing, until the client gives up.
                        std::future::pending::<()>().await;
                    }
                });
            }
        });
        format!("http://{address}")
    }

    fn client_for(base: &str) -> WarcraftLogs {
        let mut client = WarcraftLogs::new(reqwest::Client::new(), "id".to_string(), "secret".to_string());
        client.token_url = format!("{base}/oauth/token");
        client.api_url = format!("{base}/api/v2/client");
        client.timeout = Duration::from_millis(300);
        client
    }

    #[tokio::test]
    async fn a_hung_warcraft_logs_is_given_up_on_with_a_clear_message() {
        let base = silent_api().await;

        // The token comes back fine, then the API itself never answers.
        let started = Instant::now();
        let error = client_for(&base).report("ABC").await.unwrap_err().to_string();
        assert_eq!(error, "Warcraft Logs took too long to answer. Try again in a moment.");
        assert!(started.elapsed() < Duration::from_secs(5), "took {:?}", started.elapsed());

        // A token request that hangs gives the same message, rather than waiting for ever.
        let mut stuck = client_for(&base);
        stuck.token_url = format!("{base}/api/v2/client");
        let error = stuck.report("ABC").await.unwrap_err().to_string();
        assert_eq!(error, "Warcraft Logs took too long to answer. Try again in a moment.");
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

    /// The shape of a real response to a damage query with two aliased tables, cut down to a few fields.
    const DAMAGE_RESPONSE: &str = r#"{"data":{"reportData":{"report":{
        "a0":{"data":{"entries":[
            {"name":"Rè","id":16,"guid":1,"type":"Rogue","icon":"x","total":200325787,"totalReduced":1,"activeTime":0},
            {"name":"Scrublock","id":1,"type":"Warlock","total":193309151},
            {"name":"Kail","id":2,"type":"Evoker","total":193309151}]}},
        "a1":{"data":{"entries":[]}}
    }}}}"#;

    #[test]
    fn damage_tables_are_read_by_alias_and_added_across_chunks() {
        let response: GraphQlResponse<TablesQuery> = serde_json::from_str(DAMAGE_RESPONSE).unwrap();
        let tables = parse_response(response).unwrap().report_data.report.unwrap();
        assert_eq!(tables.len(), 2);

        let mut players = HashMap::new();
        add_totals(&mut players, &tables[&alias(0)].data.entries, &HashSet::new());
        // A second chunk of pulls: the same players again, and a new one.
        add_totals(
            &mut players,
            &[PlayerEntry { name: "Rè".into(), class: "Rogue".into(), total: 100.0 }, PlayerEntry { name: "New".into(), class: String::new(), total: 7.0 }],
            &HashSet::new(),
        );

        let ability = AbilityDamage::build(&tracked(1, "Coalesced Venom"), players);
        let ranking: Vec<(&str, u64)> = ability.players.iter().map(|p| (p.name.as_str(), p.total)).collect();
        // Ties are in name order.
        assert_eq!(ranking, vec![("Rè", 200_325_887), ("Kail", 193_309_151), ("Scrublock", 193_309_151), ("New", 7)]);
        assert_eq!(ability.total, 200_325_887 + 193_309_151 * 2 + 7);

        let nobody = AbilityDamage::build(&tracked(2, "Nothing"), HashMap::new());
        assert!(nobody.players.is_empty() && nobody.total == 0);
        assert!(tables[&alias(1)].data.entries.is_empty());
    }

    #[test]
    fn damage_is_shown_short_with_each_players_share() {
        assert_eq!(format_damage(0), "0");
        assert_eq!(format_damage(999), "999");
        assert_eq!(format_damage(12_345), "12.3K");
        assert_eq!(format_damage(200_325_787), "200.3M");
        assert_eq!(format_damage(3_514_226_904), "3.5B");

        let mut players = HashMap::new();
        players.insert("A".to_string(), ("Rogue".to_string(), 750u64));
        players.insert("B".to_string(), ("DeathKnight".to_string(), 250u64));
        let rows = damage_rows(&AbilityDamage::build(&tracked(1, "Fire"), players));
        assert_eq!((rows[0].name.as_str(), rows[0].value.as_str()), ("A", "750 (75.0%)"));
        assert_eq!((rows[1].name.as_str(), rows[1].value.as_str()), ("B", "250 (25.0%)"));

        // An ability nobody took damage from has no shares to divide by zero.
        assert!(damage_rows(&AbilityDamage::build(&tracked(1, "Fire"), HashMap::new())).is_empty());
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
            Vec::new(),
            0,
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
        let overview = Overview::build(&report(&[]), &[], Vec::new(), 0);
        assert!(overview_description(&overview).contains("No Mythic boss pulls"));
    }

    #[test]
    fn a_long_encounter_list_fits_in_a_description() {
        let fights: Vec<(String, bool, bool)> = (0..400).map(|n| (format!("Boss number {n} with a rather long name"), false, false)).collect();
        let fights: Vec<(&str, bool, bool)> = fights.iter().map(|(name, kill, progress)| (name.as_str(), *kill, *progress)).collect();
        let description = overview_description(&Overview::build(&report(&fights), &[], Vec::new(), 0));

        assert!(description.len() <= MAX_DESCRIPTION_CHARS);
        assert!(description.contains("more."));
    }

    #[test]
    fn a_boss_filter_picks_the_pulls_of_matching_bosses() {
        let report = report(&[("The Coiled Altar", false, false), ("Other Boss", false, false), ("The Coiled Altar", true, false), ("The Coiled Altar", false, true)]);

        // No filter: every finished pull.
        assert_eq!(select_fights(&report.fights, None).unwrap().len(), 3);
        assert_eq!(select_fights(&report.fights, Some("  ")).unwrap().len(), 3);
        // A part of the name, in any case; the pull still being uploaded is left out.
        let coiled = select_fights(&report.fights, Some("coiled")).unwrap();
        assert_eq!(coiled.iter().map(|fight| fight.id).collect::<Vec<_>>(), vec![1, 3]);

        let problem = select_fights(&report.fights, Some("nope")).unwrap_err();
        assert!(problem.contains("`nope`") && problem.contains("Other Boss, The Coiled Altar."), "{problem}");
        assert!(select_fights(&[], Some("nope")).unwrap_err().contains("no Mythic boss pulls"));
    }

    #[test]
    fn rankings_are_cut_to_the_lines_and_characters_allowed() {
        let players: Vec<PlayerCount> = (1..=30).map(|n| player(&format!("Player{n}"), 31 - n)).collect();

        let list = ranked_list(&death_rows(&players), 3, MAX_FIELD_CHARS);
        assert_eq!(list.lines().count(), 4);
        assert!(list.starts_with("1. **Player1** (Rogue) — 30\n2. **Player2**"));
        assert!(list.ends_with("…and 27 more."));

        // A list that fits is left whole.
        let list = ranked_list(&death_rows(&players[..2]), 10, MAX_FIELD_CHARS);
        assert_eq!(list.lines().count(), 2);
        assert!(!list.contains("more."));

        // Long names can't push a field over Discord's limit.
        let wide: Vec<PlayerCount> = (1..=40).map(|n| player(&"x".repeat(60 + n), 1)).collect();
        let list = ranked_list(&death_rows(&wide), 40, MAX_FIELD_CHARS);
        assert!(list.len() <= MAX_FIELD_CHARS);
        assert!(list.ends_with(" more."));
    }

    /// The text of an embed that counts towards Discord's size limit, read back from what would be sent.
    fn embed_text_size(embed: &CreateEmbed) -> usize {
        let value = serde_json::to_value(embed).unwrap();
        let text = |v: &Value| v.as_str().map_or(0, |s| s.chars().count());
        let fields: usize = value["fields"].as_array().map_or(0, |fields| fields.iter().map(|f| text(&f["name"]) + text(&f["value"])).sum());
        text(&value["title"]) + text(&value["description"]) + text(&value["footer"]["text"]) + fields
    }

    fn crowded_ability(name: &str, players: usize) -> AbilityDamage {
        let ability = TrackedAbility { ability_id: 1, name: name.to_string() };
        let totals = (0..players).map(|n| (format!("A rather long player name {n}"), ("DemonHunter".to_string(), 1_000_000_000u64 - n as u64))).collect();
        AbilityDamage::build(&ability, totals)
    }

    #[test]
    fn an_overview_with_many_tracked_abilities_stays_within_discords_size_limit() {
        let fights: Vec<(String, bool, bool)> = (0..12).map(|n| (format!("Boss number {n} with a rather long name"), false, false)).collect();
        let fights: Vec<(&str, bool, bool)> = fights.iter().map(|(name, kill, progress)| (name.as_str(), *kill, *progress)).collect();
        let deaths: Vec<DeathEntry> = (0..60).map(|n| death(&format!("A rather long player name {n}"), "DemonHunter", n, n as f64)).collect();
        let damage: Vec<AbilityDamage> = (0..10).map(|n| crowded_ability(&"x".repeat(90 + n), 30)).collect();

        let overview = Overview::build(&report(&fights), &deaths, damage, 3);
        let embed = overview_embed(&overview);
        let size = embed_text_size(&embed);
        assert!(size <= MAX_EMBED_CHARS, "embed text is {size} characters");

        // Not everything fit, and the footer says so.
        let value = serde_json::to_value(&embed).unwrap();
        let shown = value["fields"].as_array().unwrap().len() - 2;
        assert!(shown > 0 && shown < 10, "{shown} abilities shown");
        assert!(value["footer"]["text"].as_str().unwrap().contains(&format!("{} more tracked abilities", 10 - shown)));
    }

    #[test]
    fn an_overview_shows_every_tracked_ability_when_there_is_room() {
        let damage = vec![crowded_ability("Coalesced Venom", 20), AbilityDamage::build(&tracked(2, "Venom Rupture"), HashMap::new())];
        let embed = overview_embed(&Overview::build(&report(&[("Zul'jan", false, false)]), &[death("Rè", "Rogue", 1, 1.0)], damage, 0));
        let value = serde_json::to_value(&embed).unwrap();
        let fields = value["fields"].as_array().unwrap();

        assert_eq!(fields.len(), 4);
        assert_eq!(fields[2]["name"], "Coalesced Venom — damage taken");
        // The ability's total for the night first (over the one pull this report has), then only the
        // top few players, with a note for the rest.
        let field = fields[2]["value"].as_str().unwrap();
        assert!(field.starts_with("Total: **"), "{field}");
        assert!(field.lines().next().unwrap().ends_with("over 1 pull"), "{field}");
        assert_eq!(field.lines().count(), 1 + DAMAGE_SHOWN_IN_OVERVIEW + 1);
        // An ability nobody took damage from still gets a field, and Discord rejects an empty one.
        assert!(fields[3]["value"].as_str().unwrap().starts_with("Nobody took damage from this."));
        assert!(!value["footer"]["text"].as_str().unwrap().contains("didn't fit"));
    }

    #[test]
    fn a_damage_report_names_the_boss_and_lists_the_players() {
        let report = report(&[("The Coiled Altar", false, false), ("The Coiled Altar", true, false)]);
        let fights = select_fights(&report.fights, Some("coiled")).unwrap();
        // Names as long as a character name can be (12 letters), so the line limit is what cuts the list.
        let totals = (0..40).map(|n| (format!("Player{n:06}"), ("Rogue".to_string(), 1_000_000_000u64 - n as u64))).collect();
        let ability = AbilityDamage::build(&tracked(1, "Coalesced Venom"), totals);
        let damage = DamageReport::build(&report, &fights, ability, Some("coiled"), 0);

        assert_eq!(damage.scope, "The Coiled Altar");
        assert_eq!(damage.pulls, 2);
        let value = serde_json::to_value(damage_embed(&damage)).unwrap();
        assert_eq!(value["title"], "Coalesced Venom — damage taken");
        let description = value["description"].as_str().unwrap();
        assert!(description.contains("**The Coiled Altar**, 2 pulls") && description.contains("Total damage taken: **"), "{description}");
        assert_eq!(value["fields"][0]["value"].as_str().unwrap().lines().count(), DAMAGE_SHOWN + 1);

        // Far longer names than a character can have are cut by the character limit instead, never over it.
        let long = serde_json::to_value(damage_embed(&DamageReport::build(&report, &fights, crowded_ability("Coalesced Venom", 40), None, 0))).unwrap();
        assert!(long["fields"][0]["value"].as_str().unwrap().chars().count() <= MAX_FIELD_CHARS);

        // With no filter it covers every boss; with no pulls or no damage it says so rather than send an empty field.
        let all = DamageReport::build(&report, &fights, AbilityDamage::build(&tracked(1, "Fire"), HashMap::new()), None, 0);
        assert_eq!(all.scope, "All Mythic bosses");
        let value = serde_json::to_value(damage_embed(&all)).unwrap();
        let nobody = value["fields"][0]["value"].as_str().unwrap();
        assert!(nobody.starts_with("Nobody took damage from this in those pulls.") && nobody.contains("check the ability ID") && nobody.len() <= MAX_FIELD_CHARS);
        let empty = DamageReport::build(&report, &[], AbilityDamage::build(&tracked(1, "Fire"), HashMap::new()), None, 0);
        let value = serde_json::to_value(damage_embed(&empty)).unwrap();
        assert!(value["description"].as_str().unwrap().contains("No Mythic boss pulls") && value["fields"].is_null());
    }

    /// Calls the real API with a public report:
    /// `WCL_CLIENT_ID=... WCL_CLIENT_SECRET=... WCL_TEST_REPORT=<code> WCL_TEST_GUILD=<id> WCL_TEST_UPLOADER=<id>
    /// WCL_TEST_ABILITY=<id> cargo test -- --ignored --nocapture`.
    #[tokio::test]
    #[ignore = "needs WCL_CLIENT_ID, WCL_CLIENT_SECRET, WCL_TEST_REPORT, WCL_TEST_GUILD, WCL_TEST_UPLOADER and WCL_TEST_ABILITY, and calls the real Warcraft Logs API"]
    async fn the_real_api_returns_an_overview_and_finds_the_newest_report() {
        let var = |name: &str| std::env::var(name).unwrap_or_else(|_| panic!("{name} must be set"));
        let client = WarcraftLogs::new(reqwest::Client::new(), var("WCL_CLIENT_ID"), var("WCL_CLIENT_SECRET"));
        let ability = tracked(var("WCL_TEST_ABILITY").parse().unwrap(), "Tracked ability");

        let overview = client.overview(&var("WCL_TEST_REPORT"), std::slice::from_ref(&ability), &[]).await.unwrap();
        let pulls: u32 = overview.encounters.iter().map(|encounter| encounter.pulls).sum();
        let counted: u32 = overview.deaths.iter().map(|player| player.count).sum();
        println!("{}\n{}", overview_description(&overview), ranked_list(&death_rows(&overview.deaths), MOST_DEATHS_SHOWN, MAX_FIELD_CHARS));
        println!("{}", ranked_list(&death_rows(&overview.first_deaths), FIRST_DEATHS_SHOWN, MAX_FIELD_CHARS));

        assert!(pulls > 0 && counted > 0);
        // Two deaths per pull, or a few more where several died at the very moment of the cutoff.
        assert!(counted <= pulls * 3, "{counted} deaths counted in {pulls} pulls: the cutoff wasn't applied");

        // The overview's damage for the ability is the same as asking for it alone, over every pull.
        let (name, value) = damage_field(&overview.damage[0], DAMAGE_SHOWN_IN_OVERVIEW, pulls);
        println!("{name}\n{value}");
        assert!(!overview.damage[0].players.is_empty());
        let alone = client.damage_report(&var("WCL_TEST_REPORT"), None, &ability, &[]).await.unwrap();
        assert_eq!(alone.ability.players, overview.damage[0].players);
        assert_eq!(alone.pulls as u32, pulls);
        println!("{}", ranked_list(&damage_rows(&alone.ability), DAMAGE_SHOWN, MAX_FIELD_CHARS));

        // One boss only, and a boss the report doesn't have.
        let boss = overview.encounters[0].name.clone();
        let one = client.damage_report(&var("WCL_TEST_REPORT"), Some(&boss.to_uppercase()), &ability, &[]).await.unwrap();
        assert!(one.ability.total <= alone.ability.total);
        assert!(client.damage_report(&var("WCL_TEST_REPORT"), Some("no such boss"), &ability, &[]).await.is_err());

        // Ignoring the two players who took the most (one typed in capitals) removes them everywhere,
        // and their damage from the total.
        let (first, second) = (alone.ability.players[0].clone(), alone.ability.players[1].clone());
        let ignored = [first.name.to_uppercase(), second.name.clone()];
        let hidden = client.damage_report(&var("WCL_TEST_REPORT"), None, &ability, &ignored).await.unwrap();
        assert!(hidden.ability.players.iter().all(|player| player.name != first.name && player.name != second.name));
        assert_eq!(hidden.ability.total, alone.ability.total - first.total - second.total);
        assert_eq!(hidden.ignored, 2);
        let without = client.overview(&var("WCL_TEST_REPORT"), std::slice::from_ref(&ability), &ignored).await.unwrap();
        assert!(without.deaths.iter().chain(&without.first_deaths).all(|player| player.name != first.name && player.name != second.name));
        assert!(footer_text(without.ignored).contains("2 ignored players are left out."));
        println!("with {} and {} ignored:\n{}", first.name, second.name, damage_field(&without.damage[0], DAMAGE_SHOWN_IN_OVERVIEW, pulls).1);

        let source = WclSource { guild_id: var("WCL_TEST_GUILD").parse().unwrap(), uploader_id: var("WCL_TEST_UPLOADER").parse().unwrap() };
        let newest = client.latest_report_code(&source).await.unwrap();
        println!("newest report from the uploader: {newest:?}");
        assert!(newest.is_some());

        let nobody = WclSource { uploader_id: 1, ..source };
        assert_eq!(client.latest_report_code(&nobody).await.unwrap(), None);
        assert!(client.overview("doesnotexist1234", &[], &[]).await.is_err());
    }

    #[test]
    fn ignored_players_are_matched_ignoring_case_and_left_out_of_damage_and_totals() {
        let ignored = ignored_set(&["TRITONIAN".to_string(), "aâyo".to_string()]);
        assert!(is_ignored(&ignored, "Tritonian"));
        assert!(is_ignored(&ignored, "Aâyo"));
        assert!(!is_ignored(&ignored, "Tritonia"));
        assert!(!is_ignored(&HashSet::new(), "Tritonian"));

        let entry = |name: &str, total: f64| PlayerEntry { name: name.to_string(), class: "Rogue".to_string(), total };
        let mut players = HashMap::new();
        add_totals(&mut players, &[entry("Tritonian", 900.0), entry("Aâyo", 50.0), entry("Rè", 100.0)], &ignored);

        let ability = AbilityDamage::build(&tracked(1, "Fire"), players);
        // Only Rè is left, and the total is theirs alone, so their share is all of it.
        assert_eq!(ability.players.len(), 1);
        assert_eq!(ability.total, 100);
        assert_eq!(damage_rows(&ability)[0].value, "100 (100.0%)");
    }

    #[test]
    fn the_footer_says_how_many_players_are_left_out() {
        assert!(!footer_text(0).contains("left out"));
        assert!(footer_text(1).ends_with(" 1 ignored player is left out."));
        assert!(footer_text(3).ends_with(" 3 ignored players are left out."));

        // An overview and a damage report both carry it.
        let overview = Overview::build(&report(&[("Zul'jan", false, false)]), &[], Vec::new(), 2);
        let value = serde_json::to_value(overview_embed(&overview)).unwrap();
        assert!(value["footer"]["text"].as_str().unwrap().ends_with("2 ignored players are left out."));
        let night = report(&[("Zul'jan", false, false)]);
        let damage = DamageReport::build(&night, &[&night.fights[0]], AbilityDamage::build(&tracked(1, "Fire"), HashMap::new()), None, 1);
        let value = serde_json::to_value(damage_embed(&damage)).unwrap();
        assert!(value["footer"]["text"].as_str().unwrap().ends_with("1 ignored player is left out."));
    }

    /// Calls the real API with a public report, and healthstone and health potion ability IDs (comma
    /// separated): `WCL_CLIENT_ID=... WCL_CLIENT_SECRET=... WCL_TEST_REPORT=<code>
    /// WCL_TEST_HEALTHSTONES=6262,452930 WCL_TEST_POTIONS=1234768,1295247 cargo test -- --ignored --nocapture`.
    #[tokio::test]
    #[ignore = "needs WCL_CLIENT_ID, WCL_CLIENT_SECRET, WCL_TEST_REPORT, WCL_TEST_HEALTHSTONES and WCL_TEST_POTIONS, and calls the real Warcraft Logs API"]
    async fn the_real_api_counts_consumables() {
        let var = |name: &str| std::env::var(name).unwrap_or_else(|_| panic!("{name} must be set"));
        let ids = |name: &str| -> Vec<u32> { var(name).split(',').map(|id| id.trim().parse().unwrap()).collect() };
        let mut registered: Vec<Registered> = ids("WCL_TEST_HEALTHSTONES").into_iter().map(|id| Registered { kind: Kind::Healthstone, ability_id: id }).collect();
        registered.extend(ids("WCL_TEST_POTIONS").into_iter().map(|id| Registered { kind: Kind::HealthPotion, ability_id: id }));
        let client = WarcraftLogs::new(reqwest::Client::new(), var("WCL_CLIENT_ID"), var("WCL_CLIENT_SECRET"));
        let code = var("WCL_TEST_REPORT");

        let report = client.consumables(&code, None, &registered, &[]).await.unwrap();
        for used in &report.uses {
            let (name, value, _) = consumable_field(used);
            println!("{name}\n{value}");
            assert!(used.registered && used.total > 0);
            assert_eq!(used.total, used.players.iter().map(|player| player.total).sum::<u64>());
        }

        // Ignoring the player who used the most healthstones (typed in capitals) removes them and exactly their uses.
        let top = report.uses[0].players[0].clone();
        let hidden = client.consumables(&code, None, &registered, &[top.name.to_uppercase()]).await.unwrap();
        assert!(hidden.uses[0].players.iter().all(|player| player.name != top.name));
        assert_eq!(hidden.uses[0].total, report.uses[0].total - top.total);
        assert_eq!(hidden.ignored, 1);
        println!("with {} ignored: {} healthstones used", top.name, hidden.uses[0].total);

        // One boss only, and a boss the report doesn't have.
        let boss = client.overview(&code, &[], &[]).await.unwrap().encounters[0].name.to_uppercase();
        let one = client.consumables(&code, Some(&boss), &registered, &[]).await.unwrap();
        assert!(one.uses[0].total <= report.uses[0].total && one.pulls > 0);
        assert!(client.consumables(&code, Some("no such boss"), &registered, &[]).await.is_err());
    }

    #[test]
    fn the_total_of_an_ability_is_the_sum_over_the_night() {
        // Two players' damage added up is the total shown, however many pulls it came from.
        let mut players = HashMap::new();
        players.insert("A".to_string(), ("Rogue".to_string(), 600_000_000u64));
        players.insert("B".to_string(), ("Druid".to_string(), 97_716_688u64));
        let ability = AbilityDamage::build(&tracked(1285017, "Axegrinder"), players);

        let (name, value) = damage_field(&ability, DAMAGE_SHOWN_IN_OVERVIEW, 34);
        assert_eq!(name, "Axegrinder — damage taken");
        assert_eq!(value.lines().next().unwrap(), "Total: **697.7M** over 34 pulls");
        assert_eq!(plural(1, "pull"), "1 pull");
        assert_eq!(plural(0, "pull"), "0 pulls");
        assert!(value.chars().count() <= MAX_FIELD_CHARS);
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
