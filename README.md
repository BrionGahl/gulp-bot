<h1 color="#000000" size="100px" align="center"> gulp-bot </h1>

## Goal / Purpose

The ideas behind the creation of this bot are listed below:
- consolidate bots in my Discord servers
- learn and experiment with Rust

The bot is written with the `poise` crate and uses a few others for requests / logging sinks.

## Features

- **WoW Guild** — Bart Timeline Reminders addon info, class Discord links, and droptimizer report submission via WowUtils
- **Clips channels** — deletes any message posted in a configured channel that doesn't contain a video (uploaded video file or an unfurled video link embed)
- **Warcraft Logs** — officers get an overview of a raid night's report (Mythic pulls, who died the most, who died first, damage taken from tracked abilities, healthstones and health potions used) with `/wcl report`, `/wcl damage_taken` and `/wcl consumables`, posted in a registered officer channel
- **Personal officer channels** — when a member is given the trial or raider role, creates a private text channel for them under a configured category, visible only to them and the officer (moderator) role

## Deployment

The bot is packaged as a Docker image and runs on a self-hosted Docker host next to a Postgres
container, managed together as a Portainer stack. There is no registry involved: the image is built locally, exported to
a tarball, and loaded onto the host.

### Building the image

The `Dockerfile` is a multi-stage Rust build that uses `cargo-chef` for dependency caching.
`build-image.sh` builds it with `podman` (or `docker` if podman isn't installed) and saves it to a
tarball:

```shell
./build-image.sh
```

Every build is tagged twice: with a version derived from the current commit, in the same form as the
[release tags](#release-tags) (e.g. `v2026.10.09-c97a8ec`, with `-dirty` appended if there are
uncommitted changes), and with `latest`. The tarball is named `gulp-bot-<version>.tar` and contains
both tags.

### Loading it onto the host

Copy the tarball to the Docker host and load it, or upload it through Portainer (`Images` → `Import`):

```shell
scp gulp-bot-<version>.tar <user>@<host>:/tmp/
ssh <user>@<host> docker load -i /tmp/gulp-bot-<version>.tar
```

Loading moves `latest` to the new build and leaves every previously loaded version on the host under
its own tag. An image built with podman is loaded as `localhost/gulp-bot:<tag>`, which is the name
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

## Using the bot

### Environment Variables

```shell
# Discord
DISCORD_TOKEN=<bot token>
BOT_NAME=<display name used in embeds; defaults to gulp-bot>

# Database
DATABASE_URL=<Postgres connection URL, e.g. postgres://gulp:<password>@db:5432/gulp>

# Other (optional)
LOG_LEVEL=<TRACE, DEBUG, INFO, WARN or ERROR; defaults to INFO>

# Warcraft Logs (optional, both or neither; the /wcl commands are off without them)
WCL_CLIENT_ID=<API client ID>
WCL_CLIENT_SECRET=<API client secret>
```

With `docker-compose.yml`, `DATABASE_URL` is built for you from `POSTGRES_PASSWORD`; see `example.env`
for the variables the stack expects.

Role, channel and user IDs, the Bart and WowUtils credentials, and the raid notes schedule are not
environment variables.
They are registered per server with the `/settings` commands below.

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
| `/settings wcl_channel <channel>` | The officer channel Warcraft Logs results are posted in, and the only one `/wcl` works in |
| `/settings wcl_source <guild> <uploader>` | Warcraft Logs guild ID the reports are uploaded to, and the user ID of the account that uploads them |
| `/settings wcl_mechanic_add <name> <ability_id>` / `wcl_mechanic_remove <name>` / `wcl_mechanic_list` | The abilities whose damage taken the Warcraft Logs overview shows (see [Warcraft Logs](#warcraft-logs)) |
| `/settings wcl_ignore_add <name>` / `wcl_ignore_remove <name>` / `wcl_ignore_list` | Players left out of the Warcraft Logs overview |
| `/settings wcl_consumable_add <kind> <ability_id>` / `wcl_consumable_remove <ability_id>` / `wcl_consumable_list` | The abilities counted as healthstones and health potions by `/wcl consumables` |
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
without a restart. See `src/guild_settings.rs` and `src/commands/settings.rs`.

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
attachment links may 404 shortly after deletion. See `src/message_replay.rs`.

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
`src/personal_officer_channels.rs`.

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
without a restart. See `src/raid_notes.rs`.

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
See `src/raid_attendance.rs`.

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
needs permission to send messages in the chosen channel. See `src/announcements.rs` and
`src/commands/announcement.rs`.

### Warcraft Logs

Administrators and moderators (members with the registered moderator role) can get an overview of a
raid night from [Warcraft Logs](https://www.warcraftlogs.com). The bot never posts one by itself: it
only happens when one of them runs a command. The commands are slash-only, and they only work in the
channel registered with `/settings wcl_channel`; anywhere else they reply, privately, with where to go.

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
| `/settings wcl_mechanic_add <name> <ability_id>` | Tracks an ability under a name; adding one that is already tracked renames it |
| `/settings wcl_mechanic_remove <name>` | Stops tracking an ability |
| `/settings wcl_mechanic_list` | Lists the tracked abilities |

Players can be left out of the overview, for example someone who isn't part of the raid group, or a tank
who takes an ability on purpose. An ignored player appears in no ranking, and what they took doesn't
count towards any total or share, as if they weren't in the report. The footer says how many are left
out. The first-two-deaths cutoff is Warcraft Logs' own, so an ignored player's death among the first two
in a pull still uses up one of them. Names are character names; capitals don't matter. A server can
ignore 50 players.

| Command | Does |
| --- | --- |
| `/settings wcl_ignore_add <name>` | Leaves a player out of the overview |
| `/settings wcl_ignore_remove <name>` | Shows the player again (the name autocompletes) |
| `/settings wcl_ignore_list` | Lists the ignored players |

`/wcl consumables` counts how many **healthstones and health potions** the raid used over the whole
night, and who used the most of each. Unlike deaths and damage taken it counts **every use in every
pull**, including once the raid had started wiping, because it is about what the raid consumed. As with
tracked abilities, only the IDs you register count, as Warcraft Logs logs them (click the ability in a
report's Casts tab). A kind can have several: a plain and a demonic healthstone, two potions, up to 6 of
each. Ignored players are left out here too, with their uses.

| Command | Does |
| --- | --- |
| `/settings wcl_consumable_add <kind> <ability_id>` | Counts an ability as a healthstone or a health potion |
| `/settings wcl_consumable_remove <ability_id>` | Stops counting an ability |
| `/settings wcl_consumable_list` | Lists the registered IDs for each kind |

Setting it up:

1. Create an API client at warcraftlogs.com (Profile, then Manage Your Clients) and give its ID and
   secret to the bot as `WCL_CLIENT_ID` and `WCL_CLIENT_SECRET`. They are shared by every server, so
   all servers draw on one API allowance (720 points per hour; an overview costs a few points, plus
   one or two for each tracked ability).
   Only public and unlisted reports can be read; private ones can't.
2. Register the officer channel with `/settings wcl_channel`.
3. Register where the reports are found with `/settings wcl_source <guild> <uploader>`, the Warcraft
   Logs guild the reports are uploaded to and the user ID of the account that uploads them. Only
   reports from that account are picked when `/wcl report` is used without a link, and only among
   the guild's 25 newest.

A report that is still being uploaded is shown without its unfinished pulls. See `src/warcraftlogs.rs`,
`src/wcl_mechanics.rs`, `src/wcl_ignored_players.rs`, `src/wcl_consumables.rs` and `src/commands/wcl.rs`.
