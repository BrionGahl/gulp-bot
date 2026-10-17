"""gulp-bot: a Discord bot for a World of Warcraft raiding guild.

The package is split by responsibility, and a request travels through it in one direction:

    resource/    Discord commands and events (the controllers): read the input, check who is asking,
                 call a service, and answer with what a mapper made of the result.
    service/     What the bot does: the guild's rules, independent of how they were asked for.
    repository/  The SQL of each table (and the state kept in memory), and nothing else.
    client/      The outside HTTP APIs: Warcraft Logs, Anthropic, Ollama, WowUtils.
    mapper/      Turns data from one shape into another: rows and JSON into schemas, schemas into
                 embeds and message text.
    schema/      The data passed between the layers (DTOs): dataclasses and enums, no behaviour.
    exception/   The errors a service raises for a resource to show to the member.
    job/         The scheduled work: announcements, raid notes posts, attendance snapshots.
    core/        The plumbing every layer shares: configuration, database, HTTP, cron, Discord
                 context and checks.

`app.py` starts everything, `container.py` builds the services once, and `bot.py` is the connection to
Discord.
"""
