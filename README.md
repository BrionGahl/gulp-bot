<h1 color="#000000" size="100px" align="center"> gulp-bot </h1>

## Goal / Purpose

The ideas behind the creation of this bot are listed below:
- consolidate bots in my Discord servers

The bot is written in Python 3.13 with [discord.py](https://github.com/Rapptz/discord.py), `asyncpg` for
Postgres and `aiohttp` for the other APIs. It was first written in Rust with `poise`; that version is in
the git history, up to commit `351747e`. Both use the same database, so one can replace the other.

## Features

- **WoW Guild** — Bart Timeline Reminders addon info, class Discord links, and droptimizer report submission via WowUtils
- **Clips channels** — deletes any message posted in a configured channel that doesn't contain a video (uploaded video file or an unfurled video link embed)
- **Warcraft Logs** — officers get an overview of a raid night's report (Mythic pulls, who died the most, who died first, damage taken from tracked abilities, healthstones and health potions used) with `/wcl report`, `/wcl damage_taken` and `/wcl consumables`, posted in that week's raid notes thread; the overview is also posted there by itself after each raid
- **AI assistant** — raiders ask Claude with `/ask` or by saying "Bot please" in a message; the guild's context and personality are in `gulp_bot/prompt/system_prompt.md`. Off until `ANTHROPIC_API_KEY` is set
- **Personal officer channels** — when a member is given the trial or raider role, creates a private text channel for them under a configured category, visible only to them and the officer (moderator) role

## Deployment

The bot is packaged as a Docker image and runs on a self-hosted Docker host next to a Postgres
container, managed together as a Portainer stack. There is no registry involved: the image is built locally, exported to
a tarball, and loaded onto the host.

### Building the image

The `Dockerfile` installs the pinned dependencies (`requirements.txt`) into the official `python:3.13-slim`
image and copies the bot and its migrations in.
`build-image.sh` builds it with `podman` (or `docker` if podman isn't installed) and saves it to a
tarball:

```shell
./build-image.sh
```

Every build is tagged twice: with a version derived from the current commit, in the same form as the
[release tags](#release-tags) (e.g. `v2026.10.09-c97a8ec`, with `-dirty` appended if there are
uncommitted changes), and with a moving channel tag. A build from `master` moves `latest`; a build
from any other branch moves `dev` instead, so loading it never changes what a stack running `latest`
gets. Set `CHANNEL` to pick the tag yourself, e.g. `CHANNEL=latest ./build-image.sh` to release from
another branch. The tarball is named `gulp-bot-<version>.tar` and contains both tags.

### Loading it onto the host

Copy the tarball to the Docker host and load it, or upload it through Portainer (`Images` → `Import`):

```shell
scp gulp-bot-<version>.tar <user>@<host>:/tmp/
ssh <user>@<host> docker load -i /tmp/gulp-bot-<version>.tar
```

Loading moves the build's channel tag (`latest` or `dev`) to the new build and leaves every
previously loaded version on the host under its own tag. An image built with podman is loaded as `localhost/gulp-bot:<tag>`, which is the name
`docker-compose.yml` expects. If it was built with docker instead, the name is `gulp-bot:<tag>` and
the `image:` line needs to match.

### Running it

`docker-compose.yml` defines the stack. In Portainer, create a stack from it (`Stacks` → `Add stack`)
and set the values listed under [Environment Variables](#environment-variables) in the stack's
environment variables section. Outside of Portainer, put the same values in a `.env` file next to the
compose file and run `docker compose up -d`.

The bot has no HTTP API of its own and only holds a persistent Discord gateway connection, so the
stack publishes no ports. Its per-server settings live in the Postgres container, whose data is kept
in the `db-data` volume; the bot creates and migrates its tables on startup (`migrations/`). Only one
instance should run per Discord token: every event would otherwise be handled twice, and the bot
caches settings in memory on the assumption that it is the only writer.

### Updating and rolling back

Build and load a new image as above, then redeploy the stack so the container is recreated from it.

The stack runs `latest` unless `IMAGE_TAG` is set in its environment variables. To roll back, set
`IMAGE_TAG` to an older version that is still loaded on the host (e.g. `v2026.10.09-c97a8ec`) and
redeploy; clear it to return to `latest`. Old versions take up disk space until removed with
`docker image rm`.

Database migrations only go forward. An image older than a migration that has already been applied
will refuse to start, so rolling back across a schema change also means restoring a database backup
from before it.

### Release tags

`.github/workflows/tag.yml` tags every push to `master` as `v<commit date, UTC>-<short sha>`, e.g.
`v2026.10.09-c97a8ec`. The tags only mark what was pushed; nothing is built or deployed by GitHub
Actions.

## Development

The bot needs Python 3.13. To run the tests and the linter:

```shell
python -m venv .venv && . .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
ruff check . && ruff format --check .
```

The database tests (`tests/test_database.py`) are skipped unless `TEST_DATABASE_URL` points at a Postgres
database they may empty, e.g. `TEST_DATABASE_URL=postgres://gulp:<password>@localhost:5432/gulp_test pytest`.

To run the bot outside Docker, set the environment variables below (with a `DATABASE_URL` your machine
can reach) and run `python -m gulp_bot`. Its log is one JSON object per line on standard output.

### Code layout

The code is split by responsibility, and a request goes through it in one direction: a **resource** (a
Discord command or event) calls a **service**, which reads and writes through **repositories** and
**clients**, and the resource answers with what a **mapper** makes of the result.

```
gulp_bot/
├── app.py          starts everything: configuration, database, Discord connection
├── bot.py          the Discord client: which event goes to which resource, command errors
├── container.py    builds every service once, with what it depends on
├── resource/       the controllers: one module per command group (*_resource.py) and per kind of event
├── service/        the guild's rules (*_service.py), independent of how they were asked for
├── repository/     the SQL of each table (*_repository.py), and the state kept only in memory
├── client/         the outside APIs: Warcraft Logs, Anthropic, Ollama, WowUtils
├── mapper/         rows and JSON into schemas, schemas into embeds and text
├── schema/         the data passed between the layers (DTOs): dataclasses and enums
├── exception/      the errors a service raises for a resource to show the member
├── job/            the scheduled work: announcements, raid notes, raid attendance
├── core/           shared plumbing: config, database, migrations, HTTP, cron, checks, text
└── prompt/         system_prompt.md, what the AI assistant knows about the guild
```

For example, `/announcement add` is `resource/announcement_resource.py`, which checks the schedule with
`core/schedule_parsing.py`, stores a `schema/announcement_schema.py` `NewAnnouncement` through
`service/announcement_service.py` and `repository/announcement_repository.py`, and
`job/announcement_job.py` posts it whenever it is due.

## Using the bot

### Environment Variables

```shell
# Discord
DISCORD_TOKEN=<bot token>
BOT_NAME=<display name used in embeds; defaults to gulp-bot>

# Database
DATABASE_URL=<Postgres connection URL, e.g. postgres://gulp:<password>@db:5432/gulp>

# AI assistant (optional; without a key `/ask` and "Bot please" are off)
ANTHROPIC_API_KEY=<API key from console.anthropic.com>
ANTHROPIC_MODEL=<Claude model; defaults to claude-sonnet-5-5>
ANTHROPIC_EFFORT=<low, medium, high...; defaults to low; off doesn't send the setting>
ANTHROPIC_DAILY_LIMIT=<most questions answered in 24 hours, all members together; defaults to 100>
AI_BACKEND=<anthropic or local; defaults to anthropic. local runs the model with Ollama and needs no key>
LOCAL_MODEL_URL=<Ollama server for AI_BACKEND=local; defaults to http://ollama:11434>
LOCAL_MODEL=<model Ollama runs; defaults to qwen3.5:9b>
LOCAL_MODEL_THINK=<false, true or default; defaults to false (no "thinking", quicker answers)>

# Other (optional)
LOG_LEVEL=<TRACE, DEBUG, INFO, WARN or ERROR; defaults to INFO>
```

With `docker-compose.yml`, `DATABASE_URL` is built for you from `POSTGRES_PASSWORD`; see `example.env`
for the variables the stack expects.

Role, channel and user IDs, the Bart, WowUtils and Warcraft Logs credentials, and the raid notes schedule are not
environment variables.
They are registered per server with the `/settings` commands below.

### AI assistant

Only members with the Raider role (the one registered with `/settings`) or are Officers (they hold the registered moderator role, or are server administrators, who are treated like moderators everywhere in the bot) get answers; trials, and everyone else, don't. `/ask` tells them so privately, and "Bot please" is simply ignored. Raiders can ask in two ways:

- `/ask <question>`
- any message that has the words "Bot please" in it (any case; "Bot, please ..." works too). The bot answers as a reply to that message.
- a reply to one of its answers (from `/ask` or "Bot please"), which carries the conversation on without saying "Bot please" again. A reply to anything else the bot posted doesn't count.

When the message calling it replies to another message ("Bot please, what does he mean?"), the assistant also gets that message's author and text (up to 500 characters), as context. If an answer runs out of room, it ends with a note saying it was cut short; replying "continue" to it gets the rest.

It answers with Claude through the Anthropic API, which is billed per use and separately from a Claude subscription. Set a monthly spending limit in the Anthropic Console. It remembers the last 8 turns of each channel (in memory only, forgotten after an hour without questions or when the bot restarts), each member may ask up to 10 times in 10 minutes, and the assistant answers at most `ANTHROPIC_DAILY_LIMIT` questions (100 by default) in any 24 hours, from everybody together; a question that gets no answer because of an error doesn't count. What it was told in a channel is remembered with the access level of whoever asked and is shown only to members of that level or higher, so what an Officer was told isn't repeated to a raider. It never pings anyone.

Its rules (in `RULES` in `gulp_bot/service/ai_prompt_service.py`, after the guild text so editing that can't remove them): it only speaks English, stays away from politics, doesn't violate human rights or demean people, only an Officer may ask it to do anything involving Warcraft Logs, and only an Officer may ask it to change any file, setting or data. It can't change anything today, so that last rule is only the model's own behaviour; any tool added later that changes something must check `Access.OFFICER` in code, not trust the prompt.

#### `/tldr`

`/tldr` sums up the channel it is used in, with jokes: the last 4 hours of chat, stopping at the first pause of an hour or more between two messages, read back from Discord when it is asked (up to 500 messages; the oldest are left out if the chat is too long). Bots' messages are left out. It is for the same members as `/ask` and follows `/ai off` and maintenance mode. Each channel can have one every hour; until then, `/tldr` privately points to the last one. A summary counts towards `ANTHROPIC_DAILY_LIMIT`, not towards a member's own limit, and a reply to it carries on with the assistant. The bot needs the Read Message History permission in the channel. The cooldown is kept in memory, so a restart lets every channel be summed up again.

#### "bot get them"

An Officer (moderator role, administrator or Developer) who replies to a message with the words "bot get them" has the bot delete that message and answer "arf! arf!". From anybody else it does nothing. It works whether or not the AI assistant is set up or switched on, and a deleted message is never reposted by the delete-and-replay feature. The bot needs the Manage Messages permission in the channel; without it, it says so instead. Every use is written to the log (`audit: ...`) with who ran it and whose message was deleted.

#### Developers: `/ai`

The members with the developer role (registered by an administrator with `/settings dev_role`) rank above Officers for the assistant, and are the only ones who can use `/ai`. Not even administrators can until they are given the role. The replies are private to whoever ran the command, every use is written to the bot's log (`audit: ...`) with who ran it, and what is switched applies only to the server it was used in, kept in the database across restarts.

| Command | What it does |
| --- | --- |
| `/ai status` | Whether the assistant is on, off or in maintenance, the model and effort in use, the questions answered in the last 24 hours, and the latest API failures |
| `/ai on`, `/ai off` | Switch the assistant on or off for everybody (nobody is answered while it is off) |
| `/ai maintenance <true/false>` | Maintenance mode: only Officers and Developers get answers |
| `/ai usage` | Tokens used and an estimate of the cost, by model, for the last 24 hours and since the bot started |
| `/ai model <name>` | Use another model, for example a cheaper one if the spending rises, or `default` for the configured one. The API is asked first whether it knows the model |
| `/ai effort <level>` | Use another effort (as configured, off, low, medium, high) |
| `/ai limits <member>` | How many questions a member has asked in the last 10 minutes |
| `/ai reset <member>` | Give a member their whole allowance of questions back |
| `/ai check` | Check that the registered roles exist, that the API key works and lists the model, and the state of the assistant. It costs no tokens |

The token counts and the failures are kept in memory and start again when the bot restarts; the cost is estimated from Anthropic's list prices, and the Anthropic Console has the real bill.

#### Running the model locally

With `AI_BACKEND=local` the assistant asks a model on your own GPU instead of Claude, through [Ollama](https://ollama.com), and nothing it is told leaves the machine. No Anthropic key is needed. Everything else works the same (access, limits, memory, replies, `/ai`), except that `/ai effort` does nothing and `/ai usage` shows the tokens as free. A local model answers one question at a time; the others wait their turn.

The default model, `qwen3.5:9b`, fits a 12 GB GPU such as an RTX 3060. To run it next to the bot, add an `ollama` service to the stack on a network with no internet access, and put the bot on that network too:

```yaml
services:
  ollama:
    image: ollama/ollama:<pin a version>
    restart: unless-stopped
    volumes:
      - ollama:/root/.ollama
    environment:
      OLLAMA_KEEP_ALIVE: 24h   # keep the model loaded on the GPU
    networks: [ai]
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  gulp-bot:
    # ...as above, plus:
    environment:
      AI_BACKEND: local
    networks: [default, ai]   # the internet for Discord, and the model

networks:
  ai:
    internal: true            # no route out of it

volumes:
  ollama:
```

The GPU needs the NVIDIA driver and, on Linux, the NVIDIA Container Toolkit (Docker Desktop on Windows passes the GPU through WSL2). The `ai` network can't reach the internet, so download the model once before moving Ollama onto it: start the service on the default network, run `docker exec -it <ollama container> ollama pull qwen3.5:9b`, then switch it back. The model stays in the `ollama` volume. Then run `/ai check`: it says whether the bot reaches Ollama and whether the model is installed.

If Ollama runs on another computer, set `LOCAL_MODEL_URL` to it (e.g. `http://192.168.1.20:11434`) and only let the bot's host reach that port: Ollama has no password. Qwen thinks before it answers unless told not to; the bot tells it not to (`LOCAL_MODEL_THINK=false`), which makes answers quicker. If a model rejects that, set `LOCAL_MODEL_THINK=default`.

Privacy: what a member writes to the assistant, and the assistant's recent answers in that channel, are sent to Anthropic's API to get an answer (or to your own Ollama server, with `AI_BACKEND=local`). Tell the guild before turning it on.

Who the guild is, and how the assistant treats its members, is written in `gulp_bot/prompt/system_prompt.md`; change it there and rebuild the image. The answers are only as good as that text and the model: the assistant has no access to Warcraft Logs or the guild's settings yet.

### Server settings

Each Discord server registers its own IDs and credentials with the bot, and they are stored in Postgres. These
commands can be used by members with the **Administrator** permission and by members with the
server's registered moderator role, and only inside the server being configured. Until a moderator
role is registered, that means administrators only. Setting or clearing the moderator role itself is
always restricted to administrators:

| Command | Registers |
| --- | --- |
| `/settings show` | Lists everything registered for this server |
| `/settings mod_role <role>` | Moderator role, also the "officer" role for personal officer channels |
| `/settings dev_role <role>` | Developer role: above moderators for the AI assistant, and the only one that can use `/ai`. Administrators only |
| `/settings raider_role <role>` | Raider role |
| `/settings trial_role <role>` | Trial role |
| `/settings officer_category <category>` | Category that personal officer channels are created under |
| `/settings raid_notes_channel <channel>` | Forum channel for the weekly raid notes post |
| `/settings raid_notes_cron <expression>` | When the raid notes post is created (cron expression, Eastern time) |
| `/settings raid_voice <channel> <start> <end>` | Raid voice channel, and the cron schedules for the start and end of raid, for attendance |
| `/settings replay_add <user>` / `replay_remove <user>` | Members whose self-deleted messages are reposted |
| `/settings clips_add <channel>` / `clips_remove <channel>` | Channels restricted to video-only messages |
| `/settings bart_token <token>` | Bart Timeline Reminders token shown to this server's raiders |
| `/settings wowutils <token> <group_id>` | WowUtils API token and the group droptimizer reports are submitted to |
| `/settings wcl client <client_id> <client_secret>` | Warcraft Logs API client this server's `/wcl` commands use |
| `/settings wcl source <guild> <uploader>` | Warcraft Logs guild ID the reports are uploaded to, and the user ID of the account that uploads them |
| `/settings wcl mechanic_add <name> <ability_id>` / `wcl mechanic_remove <name>` / `wcl mechanic_list` | The abilities whose damage taken the Warcraft Logs overview shows (see [Warcraft Logs](#warcraft-logs)) |
| `/settings wcl ignore_add <name>` / `wcl ignore_remove <name>` / `wcl ignore_list` | Players left out of the Warcraft Logs overview |
| `/settings wcl consumable_add <kind> <ability_id>` / `wcl consumable_remove <ability_id>` / `wcl consumable_list` | The abilities counted as healthstones and health potions by `/wcl consumables` |
| `/settings clear <setting>` | Clears one of the settings above (clips channels and replay users have their own `_remove` commands) |

Before saving, the bot checks with Discord that the role, channel or member actually belongs to the
server the command was run in, and that a channel is the right type (a forum for raid notes, a
category for officer channels). Settings are always looked up by the server an event or command came
from, so an ID registered in one server is never applied in another.

The two credential commands are slash-only, so a token is never typed into a channel as a normal
message, and `/settings show` only reports whether they are set. They are stored unencrypted in the
database. A server's raiders are only ever shown, and only ever submit to, what that server
registered.

Every setting is optional. A feature whose IDs aren't registered is off for that server, and commands
gated on a role reply that the role hasn't been registered yet. Changes take effect immediately,
without a restart. See `gulp_bot/service/guild_settings_service.py` and `gulp_bot/resource/settings_resource.py`.

Note: the bot needs the **Manage Messages** permission in any clips channel to delete non-video messages.

### Delete-and-replay

A server can register any number of replay users (`/settings replay_add`, and `replay_remove` to
take one off). The bot keeps the last few messages those users post in that server in memory, and
when one of them is deleted it reposts the content in the same channel as an embed (author line
with their name/avatar, the message text as the description, the original timestamp, and the
first image attachment rendered inline — other attachments are listed as links). It **doesn't**
repost if the guild audit log shows a moderator deleted the message: a user deleting their own
message leaves no audit log entry, so any entry for the deletion is taken to mean someone else (a
moderator) removed it. Bulk deletes (channel purges) are always treated as moderator actions and
never replayed. Reposts suppress all mentions, so a deleted `@everyone` won't re-ping on replay.
The bot needs the **View Audit Log** permission for the moderator check; without it, every
deletion is replayed. Only messages seen since the bot last started are recoverable, and
attachment links may 404 shortly after deletion. See `gulp_bot/service/message_replay_service.py`.

### Personal officer channels

When a member has the registered trial or raider role, the bot creates a private text channel for
them under the registered officer category, named after their nickname. The channel is only visible
to that member and to anyone with the moderator role (treated as the officer role) — `@everyone` is
explicitly denied `View Channel`. Members who hold the moderator role themselves don't get one.

Which members already have a channel is tracked in the `personal_officer_channels` database table,
one row per member per server. Before creating anything, the bot inserts the member's row; it only
creates a channel if that insert added a row, and then records the new channel on it. A member who is
already in the table never gets a second channel, including when they gain the other role later (e.g.
trial promoted to raider).

Channels that already exist, such as ones made by hand, are made known to the bot by an administrator or moderator:

| Command | Does |
| --- | --- |
| `/settings officer_channel_add <user> <channel>` | Registers an existing channel as that member's, so the bot won't create one |
| `/settings officer_channel_remove <user>` | Forgets the member, so the bot will create a channel for them again |
| `/settings officer_channel_list` | Lists the registered members and their channels |

The feature is off for a server until both the officer category and the moderator role are registered
there. **Register every existing channel before registering the officer category**: the bot acts on
any update to a member with the trial or raider role, so a member who has a channel but isn't in the
table would get a second one.

Losing the role, or the channel being deleted, does **not** remove the member from the table — that's
left to an administrator or moderator with `officer_channel_remove`, and deleting a channel is left to officers.
The bot needs the **Manage Channels** permission and must be able to see the officer category. See
`gulp_bot/service/officer_channel_service.py`.

### Weekly raid notes

On a cron schedule (default: every **Friday at 9:00 AM Eastern**), the bot creates a new forum post
titled `Week of <Tue> <day> - <Mon> <day>` in each server's registered raid notes channel
(`/settings raid_notes_channel`), with a placeholder starter message. It must be a **forum channel** —
the bot needs permission to create posts there.

Each server sets its own schedule with `/settings raid_notes_cron <expression>`, a quartz-style cron
expression (`sec min hour day-of-month month day-of-week [year]`); a server that hasn't set one uses
`0 0 9 * * Fri *`, and `/settings clear` reverts to that. Fire times are evaluated in
`America/New_York`, so a given local time keeps holding across the EST/EDT switch. Examples:
`0 0 9 * * Tue *` (Tuesdays 9:00 AM), `0 30 17 * * Fri *` (Fridays 5:30 PM). Expressions that never
fire, or fire more than once an hour, are rejected. A changed schedule is picked up within a minute,
without a restart. See `gulp_bot/service/raid_notes_service.py`.

A week runs Tuesday (the weekly reset) to Monday: a post or snapshot on a Tuesday belongs
to the week starting that day, and one on a Monday belongs to the week that started six days earlier.
The bot records the thread it creates for each week in the `raid_notes_posts` table and never creates
a second one for the same week. If raid attendance (below) has already opened the week's thread, the
schedule finds it there and does nothing.

### Raid attendance

With `/settings raid_voice <channel> <start> <end>`, a server marks its raid voice channel and gives
two cron schedules, one firing at the start of each raid and one at the end, e.g.
`0 0 20 * * Tue,Thu *` and `0 0 23 * * Tue,Thu *` for 8 to 11 PM on Tuesdays and Thursdays. They use
the same format, timezone (`America/New_York`) and once-an-hour limit as the raid notes schedule.

Each time either schedule fires, the bot lists everyone connected to that voice channel at that
moment and posts the list as an embed, titled "start" or "end" with the time, in the raid notes thread for that
week (Tuesday to Monday). If the week has no thread yet, which is the usual case when raids happen
before the raid notes schedule fires, the bot creates it then. Members are shown as mentions but are
not pinged, and bot accounts are left out.

This needs a registered raid notes channel, and the bot must be able to see the voice channel.
`/test_raid_attendance` (moderators) takes a snapshot immediately, and `/settings clear` with "Raid
attendance" turns the feature off.

After the end snapshot the bot posts a third embed, a summary comparing it with that raid's start
snapshot: how many were there at both, who left before the end, and who joined after the start. The
start snapshot is kept in the `raid_attendance_starts` table until then, so a bot restart mid-raid
doesn't lose it. A start is only compared with an end in the same voice channel within 12 hours;
otherwise the summary says there was no start to compare against.

Only the two moments are recorded: someone who joins and leaves in between appears in neither list.
See `gulp_bot/service/raid_attendance_service.py`.

### Announcements

Officers (members with the registered moderator role) can schedule recurring announcements for their
server. Each announcement has its own channel, schedule and message, and is stored in the
`announcements` database table. These commands are slash-only:

| Command | Does |
| --- | --- |
| `/announcement add <channel> <cron>` | Schedules a new announcement; a pop-up then asks for the message |
| `/announcement list` | Lists the server's announcements with their number, channel, schedule and next post time |
| `/announcement delete <id>` | Deletes an announcement by its number |
| `/announcement post <id>` | Posts an announcement immediately, e.g. to check how it looks |

The schedule is a quartz-style cron expression, with the same format, timezone (`America/New_York`)
and once-an-hour limit as the raid notes schedule; `0 0 12 * * Tue *` is Tuesdays at noon. The message
can be up to 2000 characters over multiple lines, with Discord markdown. `@Raider` and `@Trial` in it
are replaced with pings of the server's registered raider and trial roles; any other mention has to be
written in Discord's raw form (e.g. `<@&ROLE_ID>`), and `@everyone` works if the bot is allowed to use
it in that channel.

An announcement can only be listed, posted or deleted from the server it was created in, and a server
can have at most 25. Additions and deletions take effect within a minute, without a restart. The bot
needs permission to send messages in the chosen channel. See `gulp_bot/service/announcement_service.py` and
`gulp_bot/resource/announcement_resource.py`.

### Warcraft Logs

Administrators and moderators (members with the registered moderator role) can get an overview of a
raid night from [Warcraft Logs](https://www.warcraftlogs.com). The commands are slash-only, and they
only work in the threads of the raid notes forum (`/settings raid_notes_channel`), where each week has
its own (see [Weekly raid notes](#weekly-raid-notes)); anywhere else they reply, privately, with where
to go. Results are visible to everyone who can see that forum.

The overview is also posted by itself: 10 minutes after a raid's scheduled end (the end schedule of
`/settings raid_voice`, see [Raid attendance](#raid-attendance)), the bot posts the overview of the
night's report to that week's thread, the same one `/wcl report` gives. The wait is for the last pulls
to finish uploading. The report used is the registered uploader's newest, and only if it was started
within the 12 hours before the raid's end; if there is none, the bot says so in the thread and
`/wcl report` can be run there once the log is up. This needs the API client, the source and the raid
notes channel to be registered, and raid attendance to be set up; without them nothing is posted.

| Command | Does |
| --- | --- |
| `/wcl report [report]` | Posts the overview of a report. `report` is a report link or code; without it, the newest report from the registered uploader is used |
| `/wcl consumables [boss] [report]` | Posts how many healthstones and health potions were used, and by whom. `boss` narrows it to the pulls of the boss whose name contains it |
| `/wcl damage_taken <ability> [boss] [report]` | Posts who took damage from one ability. `ability` is a tracked ability (autocompleted) or any spell ID; `boss` narrows it to the pulls of the boss whose name contains it |

The overview has the report's Mythic boss pulls (kills and wipes for each boss), who died the most,
who was the first to die in a pull, and for each tracked ability the players who took the most damage
from it. **Only the first two deaths in each pull are counted, and damage taken only up to the second
death**: after that the raid is wiping, and what happens then says nothing about anyone's play. Players
are shown by their character name as Warcraft Logs has it; nobody is linked to a Discord account.

Only the abilities you add are tracked, by the ID you give, up to 6 per server; the bot doesn't look
for abilities itself. The ID has to be the one Warcraft Logs logs the damage under, which you can see
by clicking the ability in a report's Damage Taken tab. It can differ from the spell's ID on Wowhead,
and one ability can be logged under more than one ID; if the overview says nobody took damage from an
ability you know hit people, the ID is the first thing to check. An overview that would be too big for one Discord embed leaves
out the last abilities and says how many, and `/wcl damage_taken` shows any of them in full.

| Command | Does |
| --- | --- |
| `/settings wcl mechanic_add <name> <ability_id>` | Tracks an ability under a name; adding one that is already tracked renames it |
| `/settings wcl mechanic_remove <name>` | Stops tracking an ability |
| `/settings wcl mechanic_list` | Lists the tracked abilities |

Players can be left out of the overview, for example someone who isn't part of the raid group, or a tank
who takes an ability on purpose. An ignored player appears in no ranking, and what they took doesn't
count towards any total or share, as if they weren't in the report. The footer says how many are left
out. The first-two-deaths cutoff is Warcraft Logs' own, so an ignored player's death among the first two
in a pull still uses up one of them. Names are character names; capitals don't matter. A server can
ignore 50 players.

| Command | Does |
| --- | --- |
| `/settings wcl ignore_add <name>` | Leaves a player out of the overview |
| `/settings wcl ignore_remove <name>` | Shows the player again (the name autocompletes) |
| `/settings wcl ignore_list` | Lists the ignored players |

`/wcl consumables` counts how many **healthstones and health potions** the raid used over the whole
night, and who used the most of each. Unlike deaths and damage taken it counts **every use in every
pull**, including once the raid had started wiping, because it is about what the raid consumed. As with
tracked abilities, only the IDs you register count, as Warcraft Logs logs them (click the ability in a
report's Casts tab). A kind can have several: a plain and a demonic healthstone, two potions, up to 6 of
each. Ignored players are left out here too, with their uses.

| Command | Does |
| --- | --- |
| `/settings wcl consumable_add <kind> <ability_id>` | Counts an ability as a healthstone or a health potion |
| `/settings wcl consumable_remove <ability_id>` | Stops counting an ability |
| `/settings wcl consumable_list` | Lists the registered IDs for each kind |

Setting it up:

1. Create an API client at warcraftlogs.com (Profile, then Manage Your Clients) and register its ID
   and secret with `/settings wcl client <client_id> <client_secret>`. Each server registers its
   own, so each has its own API allowance (720 points per hour; an overview costs a few points,
   plus one or two for each tracked ability).
   Only public and unlisted reports can be read; private ones can't.
2. Register the raid notes forum with `/settings raid_notes_channel`, if it isn't already.
3. Register where the reports are found with `/settings wcl source <guild> <uploader>`, the Warcraft
   Logs guild the reports are uploaded to and the user ID of the account that uploads them. Only
   reports from that account are picked when `/wcl report` is used without a link, and only among
   the guild's 25 newest.
4. For the overview after each raid, set up raid attendance with `/settings raid_voice`.

A report that is still being uploaded is shown without its unfinished pulls. See `gulp_bot/service/warcraft_logs_service.py`,
`gulp_bot/client/warcraft_logs_client.py`, `gulp_bot/mapper/warcraft_logs_embed_mapper.py`, `gulp_bot/service/raid_report_service.py`
and `gulp_bot/resource/warcraft_logs_resource.py`.
