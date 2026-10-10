<h1 color="#000000" size="100px" align="center"> gulp-bot </h1>

## Goal / Purpose

The ideas behind the creation of this bot are listed below:
- consolidate bots in my Discord servers
- learn and experiment with Rust

The bot is written with the `poise` crate and uses a few others for requests / logging sinks.

## Features

- **WoW Guild** — Bart Timeline Reminders addon info, class Discord links, and droptimizer report submission via WowUtils
- **Clips channels** — deletes any message posted in a configured channel that doesn't contain a video (uploaded video file or an unfurled video link embed)
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
| `/settings replay_user <user>` | Member whose self-deleted messages are reposted |
| `/settings clips_add <channel>` / `clips_remove <channel>` | Channels restricted to video-only messages |
| `/settings bart_token <token>` | Bart Timeline Reminders token shown to this server's raiders |
| `/settings wowutils <token> <group_id>` | WowUtils API token and the group droptimizer reports are submitted to |
| `/settings clear <setting>` | Clears one of the settings above (clips channels use `clips_remove`) |

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

If a replay user is registered (`/settings replay_user`), the bot keeps the last few messages that
user posts in that server in memory, and
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
titled `Week of <Mon> <day> - <Sun> <day>` in each server's registered raid notes channel
(`/settings raid_notes_channel`), with a placeholder starter message. It must be a **forum channel** —
the bot needs permission to create posts there.

Each server sets its own schedule with `/settings raid_notes_cron <expression>`, a quartz-style cron
expression (`sec min hour day-of-month month day-of-week [year]`); a server that hasn't set one uses
`0 0 9 * * Fri *`, and `/settings clear` reverts to that. Fire times are evaluated in
`America/New_York`, so a given local time keeps holding across the EST/EDT switch. Examples:
`0 0 9 * * Tue *` (Tuesdays 9:00 AM), `0 30 17 * * Fri *` (Fridays 5:30 PM). Expressions that never
fire, or fire more than once an hour, are rejected. A changed schedule is picked up within a minute,
without a restart. See `src/raid_notes.rs`.

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
