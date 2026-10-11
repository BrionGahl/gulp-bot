use crate::types::bot::{Context, Data, Error};

/// Links to the bot GitHub repo
#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "Utilities",
)]
pub async fn source(ctx: Context<'_>) -> Result<(), Error> {
    ctx.say("https://github.com/BrionGahl/gulp-bot")
        .await?;
    Ok(())
}

/// List the bot's commands, or show details for one of them
///
/// Give a command name to see its full description, parameters and subcommands, e.g.
/// `/help settings` or `/help settings raid_voice`.
#[poise::command(
    prefix_command,
    slash_command,
    ephemeral,
    category = "Utilities",
)]
pub async fn help(
    ctx: Context<'_>,
    #[description = "Command to show details for, e.g. settings"]
    #[autocomplete = "poise::builtins::autocomplete_command"]
    #[rest]
    command: Option<String>,
) -> Result<(), Error> {
    let Some(query) = command.filter(|query| !query.trim().is_empty()) else {
        // The overview is poise's generated list of top-level commands by category, built from
        // the registered commands' own descriptions so it can't drift out of date.
        let config = poise::builtins::HelpConfiguration {
            extra_text_at_bottom: HELP_FOOTER,
            ephemeral: true,
            ..Default::default()
        };
        poise::builtins::help(ctx, None, config).await?;
        return Ok(());
    };

    // One command's page is rendered here rather than by poise: poise sends it as a plain
    // message, and `/settings` with all of its subcommands is over the 2000-character limit for
    // those. An embed description has room for twice that.
    let Some((path, found)) = find_command(&ctx.framework().options().commands, &query) else {
        ctx.say(format!("There's no command called `{}`. Use `/help` to see them all.", query.trim())).await?;
        return Ok(());
    };

    let embed = crate::helper::create_base_embed(&ctx)
        .title(format!("/{path}"))
        .description(render_command_help(&path, found));
    ctx.send(poise::CreateReply::default().embed(embed)).await?;
    Ok(())
}

/// Shown under the command list. Says who can use what, which the generated list doesn't.
const HELP_FOOTER: &str = "\
Use /help <command> for details and subcommands, e.g. /help settings.
Settings and Warcraft Logs: administrators and moderators. Announcements and Debug: moderators.
get_timeline_reminders and submit_droptimizer: raiders, trials and moderators.";

/// Resolves a query like `settings raid_voice` (a leading `/` or `?` is ignored) to that command,
/// descending into subcommands. Returns the command's full name alongside it.
fn find_command<'a>(commands: &'a [poise::Command<Data, Error>], query: &str) -> Option<(String, &'a poise::Command<Data, Error>)> {
    let mut candidates = commands;
    let mut found = None;
    let mut path = Vec::new();

    for word in query.trim().trim_start_matches(['/', '?']).split_whitespace() {
        let command = candidates.iter().find(|command| command.name.eq_ignore_ascii_case(word) && !command.hide_in_help)?;
        path.push(command.name.as_str());
        candidates = &command.subcommands;
        found = Some(command);
    }

    found.map(|command| (path.join(" "), command))
}

/// The body of one command's help page: its description and longer help text, then its
/// parameters and subcommands.
fn render_command_help(path: &str, command: &poise::Command<Data, Error>) -> String {
    let mut text = command.description.clone().unwrap_or_else(|| "No description.".to_string());
    if let Some(help_text) = &command.help_text {
        text.push_str("\n\n");
        text.push_str(help_text);
    }

    if !command.parameters.is_empty() {
        text.push_str("\n\n**Parameters**");
        for parameter in &command.parameters {
            let required = if parameter.required { "required" } else { "optional" };
            let description = parameter.description.as_deref().unwrap_or("");
            text.push_str(&format!("\n`{}` ({required}) — {description}", parameter.name));
        }
    }

    if !command.subcommands.is_empty() {
        text.push_str("\n\n**Subcommands**");
        for subcommand in &command.subcommands {
            let description = subcommand.description.as_deref().unwrap_or("");
            text.push_str(&format!("\n`/{path} {}` — {description}", subcommand.name));
        }
    }

    text
}

/// Register slash commands in this guild or globally
#[poise::command(
    prefix_command,
    slash_command,
    category = "Utilities",
    hide_in_help,
    ephemeral,
    check = "crate::checks::check_is_moderator"
)]
pub async fn register(ctx: Context<'_>) -> Result<(), Error> {
    poise::builtins::register_application_commands_buttons(ctx).await?;
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;

    /// Discord's limit on a message's content. The generated overview is sent as one message
    /// and is not split, so an overview over this fails to send at all.
    const MAX_MESSAGE_CHARS: usize = 2000;

    /// Discord's limit on an embed description, which is where one command's page goes.
    const MAX_EMBED_DESCRIPTION_CHARS: usize = 4096;

    fn all_commands() -> Vec<poise::Command<Data, Error>> {
        vec![
            crate::commands::wow_guild::get_timeline_reminders(),
            crate::commands::wow_guild::class_discords(),
            crate::commands::wow_utils::submit_droptimizer(),
            source(),
            help(),
            register(),
            crate::commands::mod_debug::test_raid_notes(),
            crate::commands::mod_debug::test_raid_attendance(),
            crate::commands::mod_debug::test_replay(),
            crate::commands::settings::settings(),
            crate::commands::announcement::announcement(),
            crate::commands::wcl::wcl(),
        ]
    }

    #[test]
    fn help_overview_fits_in_one_message() {
        let commands = all_commands();
        let listed: Vec<_> = commands.iter().filter(|command| !command.hide_in_help).collect();
        // Upper bound on the block as poise renders it: every command name padded to the widest,
        // a space, the description, a newline; plus a blank line and a "Name:" heading per
        // category.
        let widest = listed.iter().map(|command| "  /".len() + command.name.len()).max().unwrap_or(0);
        let rows: usize = listed.iter().map(|command| widest + 1 + command.description.as_ref().map_or(0, String::len) + 1).sum();
        let categories: std::collections::HashSet<_> = commands.iter().filter_map(|command| command.category.clone()).collect();
        let headings: usize = categories.iter().map(|category| category.len() + 3).sum();

        let total = "```\n\n\n```".len() + rows + headings + HELP_FOOTER.len();
        assert!(total <= MAX_MESSAGE_CHARS, "help overview is about {total} characters");
    }

    /// Every command with its full name, at every depth: `settings`, `settings wcl`,
    /// `settings wcl source`, and so on.
    fn with_paths<'a>(commands: &'a [poise::Command<Data, Error>], prefix: &str, found: &mut Vec<(String, &'a poise::Command<Data, Error>)>) {
        for command in commands {
            let path = if prefix.is_empty() { command.name.clone() } else { format!("{prefix} {}", command.name) };
            with_paths(&command.subcommands, &path, found);
            found.push((path, command));
        }
    }

    #[test]
    fn help_for_every_command_and_subcommand_fits_in_an_embed() {
        let commands = all_commands();
        let mut all = Vec::new();
        with_paths(&commands, "", &mut all);

        for (path, command) in all {
            // `register` is hidden from help, so it has no page.
            if command.hide_in_help {
                continue;
            }
            let (found_path, found) = find_command(&commands, &path).expect("command is findable by its full name");
            assert_eq!(found_path, path);
            let page = render_command_help(&found_path, found);
            assert!(page.chars().count() <= MAX_EMBED_DESCRIPTION_CHARS, "/help {path} is {} characters", page.chars().count());
        }
    }

    /// Discord rejects the whole registration, and the bot never finishes starting, if any
    /// command or group has more than 25 subcommands or parameters, if groups nest deeper than
    /// command, group, subcommand, or if a description is over 100 characters.
    #[test]
    fn commands_fit_discords_registration_limits() {
        const MAX_OPTIONS: usize = 25;
        const MAX_DEPTH: usize = 3;
        const MAX_DESCRIPTION_CHARS: usize = 100;

        let commands = all_commands();
        let mut all = Vec::new();
        with_paths(&commands, "", &mut all);

        for (path, command) in all {
            assert!(command.subcommands.len() <= MAX_OPTIONS, "/{path} has {} subcommands", command.subcommands.len());
            assert!(command.parameters.len() <= MAX_OPTIONS, "/{path} has {} parameters", command.parameters.len());
            assert!(path.split(' ').count() <= MAX_DEPTH, "/{path} is nested too deeply");

            let descriptions = command.description.iter().chain(command.parameters.iter().filter_map(|parameter| parameter.description.as_ref()));
            for description in descriptions {
                assert!(description.chars().count() <= MAX_DESCRIPTION_CHARS, "/{path} has a description over the limit: {description}");
            }
        }
    }

    #[test]
    fn find_command_resolves_nested_names_and_ignores_prefixes() {
        let commands = all_commands();

        assert_eq!(find_command(&commands, "settings").unwrap().0, "settings");
        assert_eq!(find_command(&commands, " /Settings  raid_voice ").unwrap().0, "settings raid_voice");
        assert_eq!(find_command(&commands, "?announcement add").unwrap().0, "announcement add");

        assert_eq!(find_command(&commands, "settings wcl mechanic_add").unwrap().0, "settings wcl mechanic_add");

        assert!(find_command(&commands, "settings nope").is_none());
        assert!(find_command(&commands, "settings wcl_source").is_none());
        assert!(find_command(&commands, "nope").is_none());
        // `register` is hidden from help, so it isn't offered here either.
        assert!(find_command(&commands, "register").is_none());

        let page = render_command_help("settings", find_command(&commands, "settings").unwrap().1);
        assert!(page.contains("**Subcommands**\n`/settings show` — "));
        assert!(page.contains("`/settings raid_voice` — "));
        assert!(page.contains("`/settings wcl` — "));
        let page = render_command_help("settings wcl", find_command(&commands, "settings wcl").unwrap().1);
        assert!(page.contains("**Subcommands**\n`/settings wcl client` — "));
        assert!(page.contains("`/settings wcl source` — "));
        let page = render_command_help("settings raid_voice", find_command(&commands, "settings raid_voice").unwrap().1);
        assert!(page.contains("**Parameters**\n`channel` (required) — "));
    }
}
