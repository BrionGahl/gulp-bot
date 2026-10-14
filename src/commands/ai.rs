use log::info;
use poise::serenity_prelude::{CreateAllowedMentions, GuildId, UserId};
use poise::CreateReply;

use crate::ai::{describe_usage, normalize_model, price_per_million, split_message, Ai, ApiCheck, DISCORD_MAX_CHARS, KNOWN_MODELS};
use crate::checks::is_developer;
use crate::guild_settings::AiSettings;
use crate::types::bot::{Context, Error};

/// Control the AI assistant: switch it off, change its model, see what it costs. Developers only.
///
/// Only members with the developer role (`/settings dev_role`) can use these commands, and every
/// use is written to the bot's log with who ran it. What is switched here applies only to the
/// server the command is used in, and is kept when the bot restarts.
//
// The check and `guild_only` are set once here: poise applies a parent command's checks to all of
// its subcommands. Each subcommand still checks the role for itself in `begin`, since these are
// the commands that can silence the assistant and change what it costs. (Kept out of the doc
// comment, which poise shows to users in `/help ai`.)
#[poise::command(
    slash_command,
    guild_only,
    category = "AI",
    check = "crate::checks::check_is_developer",
    subcommands(
        "ai_status",
        "ai_on",
        "ai_off",
        "ai_usage",
        "ai_model",
        "ai_effort",
        "ai_maintenance",
        "ai_limits",
        "ai_reset",
        "ai_check",
    ),
    subcommand_required
)]
pub async fn ai(_ctx: Context<'_>) -> Result<(), Error> {
    Ok(())
}

/// Show the assistant's state, model, effort, today's use and latest API failures
#[poise::command(slash_command, ephemeral, rename = "status")]
async fn ai_status(ctx: Context<'_>) -> Result<(), Error> {
    let Some((guild_id, ai)) = begin(ctx, "status").await? else { return Ok(()) };
    let settings = ctx.data().guild_settings.get(guild_id).ai;
    let (model, effort) = ai.resolve(&settings);

    let state = if settings.disabled {
        "**off**: it answers nobody"
    } else if settings.maintenance {
        "**on, in maintenance mode**: it answers only Officers and Developers"
    } else {
        "**on**"
    };
    let model_note = match settings.model {
        Some(_) => format!("set with `/ai model`; the bot was configured with `{}`", ai.configured_model()),
        None => "as configured".to_string(),
    };
    let effort_note = match settings.effort {
        Some(_) => format!("set with `/ai effort`; the bot was configured with {}", describe_effort(ai.configured_effort())),
        None => "as configured".to_string(),
    };
    let (answered, limit) = ai.daily_usage();
    let (failure_total, failures) = ai.failures();

    let mut text = format!(
        "**AI assistant**\nState: {state}\nModel: `{model}` ({model_note})\nEffort: {} ({effort_note})\nQuestions answered in the last 24 hours: {answered} of {limit}\n",
        describe_effort(effort),
    );
    if failures.is_empty() {
        text.push_str("No API failures since the bot started.");
    } else {
        text.push_str(&format!("API failures since the bot started: {failure_total}. The latest:"));
        for failure in &failures {
            text.push_str(&format!("\n• <t:{}:R>: {}", failure.at_unix, failure.detail));
        }
    }
    reply(ctx, text).await
}

/// Turn the AI assistant on
#[poise::command(slash_command, ephemeral, rename = "on")]
async fn ai_on(ctx: Context<'_>) -> Result<(), Error> {
    let Some((guild_id, _)) = begin(ctx, "on").await? else { return Ok(()) };
    let settings = change(ctx, guild_id, "switched on", |ai| ai.disabled = false).await?;

    let note = if settings.maintenance {
        " It is still in maintenance mode, so it answers only Officers and Developers (`/ai maintenance`)."
    } else {
        ""
    };
    reply(ctx, format!("The AI assistant is **on**.{note}")).await
}

/// Turn the AI assistant off for everybody
#[poise::command(slash_command, ephemeral, rename = "off")]
async fn ai_off(ctx: Context<'_>) -> Result<(), Error> {
    let Some((guild_id, _)) = begin(ctx, "off").await? else { return Ok(()) };
    change(ctx, guild_id, "switched off", |ai| ai.disabled = true).await?;

    reply(ctx, "The AI assistant is **off**: it answers nobody, whatever their role. `/ai on` turns it back on.").await
}

/// Show the tokens used and what they cost, roughly
#[poise::command(slash_command, ephemeral, rename = "usage")]
async fn ai_usage(ctx: Context<'_>) -> Result<(), Error> {
    let Some((_, ai)) = begin(ctx, "usage").await? else { return Ok(()) };
    let report = ai.usage_report();

    reply(
        ctx,
        format!(
            "**Last 24 hours**\n{}\n\n**Since the bot started**\n{}\n\n\
             The cost is an estimate from the tokens the API reported, at Anthropic's list prices. \
             The counts start again from zero when the bot restarts; the Anthropic Console has the real bill.",
            describe_usage(&report.last_24h),
            describe_usage(&report.since_start),
        ),
    )
    .await
}

/// Change the model the assistant uses, or go back to the configured one
#[poise::command(slash_command, ephemeral, rename = "model")]
async fn ai_model(
    ctx: Context<'_>,
    #[description = "A model name, or `default` for the one the bot was configured with"]
    #[autocomplete = "autocomplete_model"]
    name: String,
) -> Result<(), Error> {
    let Some((guild_id, ai)) = begin(ctx, "model").await? else { return Ok(()) };

    let wanted = match normalize_model(&name) {
        Ok(wanted) => wanted,
        Err(problem) => return reply(ctx, problem).await,
    };
    let Some(model) = wanted else {
        change(ctx, guild_id, "model back to the configured one", |ai| ai.model = None).await?;
        return reply(ctx, format!("The model is back to the configured `{}`.", ai.configured_model())).await;
    };

    // The API has to know the model, or every answer would fail until somebody noticed.
    ctx.defer_ephemeral().await?;
    match ai.check_api(&model).await {
        ApiCheck::Ok { model_listed: true } => {}
        ApiCheck::Ok { model_listed: false } => {
            let known = KNOWN_MODELS.iter().map(|known| format!("`{known}`")).collect::<Vec<_>>().join(", ");
            return reply(ctx, format!("The API doesn't list a model called `{model}`, so nothing was changed. Models with a known price: {known}.")).await;
        }
        ApiCheck::KeyRejected(reason) => {
            return reply(ctx, format!("The API refused the key ({reason}), so the model couldn't be checked and nothing was changed.")).await;
        }
        ApiCheck::Unreachable(problem) => {
            return reply(ctx, format!("The API couldn't be reached to check the model ({problem}), so nothing was changed. Try again in a moment.")).await;
        }
    }

    change(ctx, guild_id, &format!("model set to `{model}`"), |ai| ai.model = Some(model.clone())).await?;
    let price = match price_per_million(&model) {
        Some((input, output)) => format!("Its list price is ${input} per million tokens in and ${output} out."),
        None => "The bot doesn't know its price, so `/ai usage` can't estimate what it costs.".to_string(),
    };
    reply(ctx, format!("The model is now `{model}`, from the next question on. {price}")).await
}

/// The models offered while a developer types one. Poise doesn't run a command's checks for
/// autocomplete, so developers are checked here.
pub async fn autocomplete_model(ctx: Context<'_>, partial: &str) -> impl Iterator<Item = String> {
    let models: Vec<&'static str> = match ctx.guild_id() {
        Some(guild_id) if is_developer(ctx, guild_id) => KNOWN_MODELS.to_vec(),
        _ => Vec::new(),
    };

    let partial = partial.to_lowercase();
    models.into_iter().filter(move |model| model.contains(&partial)).map(str::to_string).take(25)
}

/// How much effort the model puts into an answer.
#[derive(Debug, Clone, Copy, poise::ChoiceParameter)]
enum Effort {
    #[name = "As configured"]
    Configured,
    #[name = "Off (send none)"]
    Off,
    #[name = "Low"]
    Low,
    #[name = "Medium"]
    Medium,
    #[name = "High"]
    High,
}

impl Effort {
    /// How the choice is stored: `None` for the configured effort, `off` for none at all.
    fn stored(self) -> Option<&'static str> {
        match self {
            Effort::Configured => None,
            Effort::Off => Some("off"),
            Effort::Low => Some("low"),
            Effort::Medium => Some("medium"),
            Effort::High => Some("high"),
        }
    }
}

/// Change how much effort the model puts into an answer. Lower is quicker and cheaper
#[poise::command(slash_command, ephemeral, rename = "effort")]
async fn ai_effort(ctx: Context<'_>, #[description = "The effort, or as configured"] level: Effort) -> Result<(), Error> {
    let Some((guild_id, ai)) = begin(ctx, "effort").await? else { return Ok(()) };

    let stored = level.stored();
    change(ctx, guild_id, &format!("effort set to {stored:?}"), |ai| ai.effort = stored.map(str::to_string)).await?;

    let message = match stored {
        None => format!("The effort is back to the configured one: {}.", describe_effort(ai.configured_effort())),
        Some("off") => "No effort is sent now.".to_string(),
        Some(level) => format!("The effort is now `{level}`."),
    };
    reply(
        ctx,
        format!("{message} It applies from the next question on. If the API rejects it, answers fail until you choose another; `/ai status` shows the latest failure."),
    )
    .await
}

/// Turn maintenance mode on or off: only Officers and Developers get answers
#[poise::command(slash_command, ephemeral, rename = "maintenance")]
async fn ai_maintenance(ctx: Context<'_>, #[description = "True to turn maintenance mode on, false to turn it off"] enabled: bool) -> Result<(), Error> {
    let Some((guild_id, _)) = begin(ctx, "maintenance").await? else { return Ok(()) };
    let settings = change(ctx, guild_id, if enabled { "maintenance mode on" } else { "maintenance mode off" }, |ai| ai.maintenance = enabled).await?;

    let message = match (enabled, settings.disabled) {
        (true, false) => "Maintenance mode is **on**: the assistant answers only Officers and Developers.",
        (true, true) => "Maintenance mode is **on**, but the assistant is off, so it answers nobody. `/ai on` turns it on.",
        (false, false) => "Maintenance mode is **off**: the assistant answers all raiders again.",
        (false, true) => "Maintenance mode is **off**, but the assistant is still off. `/ai on` turns it on.",
    };
    reply(ctx, message).await
}

/// Show how many questions a member has asked in the last 10 minutes
#[poise::command(slash_command, ephemeral, rename = "limits")]
async fn ai_limits(ctx: Context<'_>, #[description = "The member to look up"] user: UserId) -> Result<(), Error> {
    let Some((_, ai)) = begin(ctx, "limits").await? else { return Ok(()) };
    let (asked, max, window) = ai.user_usage(user.get());

    let note = if asked >= max { " They have to wait for the oldest to leave the window, or `/ai reset` gives the allowance back." } else { "" };
    reply(ctx, format!("<@{user}> has asked {asked} of {max} questions in the last {} minutes.{note}", window.as_secs() / 60)).await
}

/// Give a member their whole allowance of questions back
#[poise::command(slash_command, ephemeral, rename = "reset")]
async fn ai_reset(ctx: Context<'_>, #[description = "The member whose limit to reset"] user: UserId) -> Result<(), Error> {
    let Some((guild_id, ai)) = begin(ctx, "reset").await? else { return Ok(()) };
    let cleared = ai.reset_user(user.get());
    let (_, max, _) = ai.user_usage(user.get());

    info!("audit: {} ({}) reset the AI limit of {user} in guild {guild_id}, forgetting {cleared} question(s)", ctx.author().id, ctx.author().name);
    reply(ctx, format!("<@{user}>'s limit is reset: {cleared} counted question(s) forgotten, so they may ask {max} again.")).await
}

/// Check the registered roles, the API key and the model
#[poise::command(slash_command, ephemeral, rename = "check")]
async fn ai_check(ctx: Context<'_>) -> Result<(), Error> {
    let Some((guild_id, ai)) = begin(ctx, "check").await? else { return Ok(()) };
    ctx.defer_ephemeral().await?;

    let settings = ctx.data().guild_settings.get(guild_id);
    let (model, _) = ai.resolve(&settings.ai);
    let mut findings: Vec<(Finding, String)> = Vec::new();

    // The registered roles have to exist in this server, or they grant nobody anything.
    let existing = guild_id.roles(ctx.http()).await?;
    let roles = [
        ("Raider role", settings.raider_role_id, true),
        ("Moderator (Officer) role", settings.mod_role_id, true),
        ("Developer role", settings.dev_role_id, true),
        ("Trial role", settings.trial_role_id, false),
    ];
    for (label, role, needed) in roles {
        match role {
            Some(role_id) if role_id == guild_id.everyone_role() => {
                findings.push((Finding::Bad, format!("{label}: `@everyone` is registered, which would be everybody")));
            }
            Some(role_id) if existing.contains_key(&role_id) => findings.push((Finding::Good, format!("{label}: <@&{role_id}> exists"))),
            Some(role_id) => findings.push((Finding::Bad, format!("{label}: <@&{role_id}> is registered but no longer exists in this server"))),
            None if needed => findings.push((Finding::Warning, format!("{label}: not registered"))),
            None => {}
        }
    }
    if let Some(dev_role) = settings.dev_role_id {
        if [settings.mod_role_id, settings.raider_role_id, settings.trial_role_id].contains(&Some(dev_role)) {
            findings.push((Finding::Bad, "The developer role is also registered as another role, so its holders are not only developers".to_string()));
        }
    }

    match ai.check_api(model).await {
        ApiCheck::Ok { model_listed: true } => findings.push((Finding::Good, format!("The API key works and the API lists `{model}`"))),
        ApiCheck::Ok { model_listed: false } => {
            findings.push((Finding::Bad, format!("The API key works, but the API doesn't list `{model}`: answers will fail. Change it with `/ai model`")))
        }
        ApiCheck::KeyRejected(reason) => findings.push((Finding::Bad, format!("The API refused the key: {reason}"))),
        ApiCheck::Unreachable(problem) => findings.push((Finding::Warning, format!("The key couldn't be checked: {problem}"))),
    }

    if settings.ai.disabled {
        findings.push((Finding::Warning, "The assistant is off (`/ai on`)".to_string()));
    }
    if settings.ai.maintenance {
        findings.push((Finding::Warning, "The assistant is in maintenance mode (`/ai maintenance`)".to_string()));
    }
    let (answered, limit) = ai.daily_usage();
    if answered >= limit {
        findings.push((Finding::Bad, format!("The limit for the last 24 hours is reached: {answered} of {limit}")));
    } else {
        findings.push((Finding::Good, format!("Questions answered in the last 24 hours: {answered} of {limit}")));
    }
    let (failure_total, failures) = ai.failures();
    if let Some(latest) = failures.first() {
        findings.push((Finding::Warning, format!("{failure_total} API failure(s) since the bot started; the latest <t:{}:R>: {}", latest.at_unix, latest.detail)));
    }

    let problems = findings.iter().filter(|(finding, _)| *finding != Finding::Good).count();
    let lines = findings.iter().map(|(finding, text)| format!("{} {text}", finding.mark())).collect::<Vec<_>>().join("\n");
    let summary = if problems == 0 { "**Nothing to fix.**".to_string() } else { format!("**{problems} thing(s) to look at.**") };
    reply(ctx, format!("**AI assistant check**\n{lines}\n\n{summary}")).await
}

/// How a line of `/ai check` reads.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Finding {
    Good,
    Warning,
    Bad,
}

impl Finding {
    fn mark(self) -> &'static str {
        match self {
            Finding::Good => "✅",
            Finding::Warning => "⚠️",
            Finding::Bad => "❌",
        }
    }
}

/// An effort as a developer reads it.
fn describe_effort(effort: Option<&str>) -> String {
    effort.map_or("none sent".to_string(), |effort| format!("`{effort}`"))
}

/// Starts a `/ai` subcommand: confirms the invoker holds the developer role (the parent's check
/// has already said so, but these commands are too powerful to rely on that alone), writes the use
/// to the log, and hands back the guild and the assistant. Tells the invoker and returns `None`
/// if there is nothing to do.
async fn begin<'a>(ctx: Context<'a>, action: &str) -> Result<Option<(GuildId, &'a Ai)>, Error> {
    let Some(guild_id) = ctx.guild_id() else { return Ok(None) };
    if !is_developer(ctx, guild_id) {
        reply(ctx, "This command is only available to developers.").await?;
        return Ok(None);
    }

    info!("audit: {} ({}) used /ai {action} in guild {guild_id}", ctx.author().id, ctx.author().name);
    let Some(ai) = ctx.data().ai.as_ref() else {
        reply(ctx, "The AI assistant isn't set up for this bot: there is no `ANTHROPIC_API_KEY`.").await?;
        return Ok(None);
    };
    Ok(Some((guild_id, ai)))
}

/// Changes how the assistant is switched in `guild_id`, keeping the rest as it was, and writes
/// what changed to the log. Returns the settings as they are now.
async fn change(ctx: Context<'_>, guild_id: GuildId, what: &str, edit: impl FnOnce(&mut AiSettings)) -> Result<AiSettings, Error> {
    let store = &ctx.data().guild_settings;
    let mut settings = store.get(guild_id).ai;
    edit(&mut settings);
    store.set_ai(guild_id, settings.clone()).await?;

    info!("audit: {} ({}) changed the AI assistant in guild {guild_id}: {what}", ctx.author().id, ctx.author().name);
    Ok(settings)
}

/// Tells the invoker, in as many messages as it takes. Nobody is pinged, whatever the text mentions.
async fn reply(ctx: Context<'_>, content: impl Into<String>) -> Result<(), Error> {
    for chunk in split_message(&content.into(), DISCORD_MAX_CHARS) {
        ctx.send(CreateReply::default().content(chunk).allowed_mentions(CreateAllowedMentions::new())).await?;
    }
    Ok(())
}
