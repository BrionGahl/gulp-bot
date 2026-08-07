<h1 color="#000000" size="100px" align="center"> gulp-bot </h1>

## Goal / Purpose

The ideas behind the creation of this bot are listed below:
- consolidate bots in my Discord servers
- learn and experiment with Rust

The bot is written with the `poise` crate and uses a few others for requests / logging sinks.

## Features

- **WoW Guild** — fetches upcoming raids and absences via the WoWAudit API
- **Gambling** — multiplayer roll sessions with a lobby and results embed
- **Clips channels** — deletes any message posted in a configured channel that doesn't contain a video (uploaded video file or an unfurled video link embed)

## Deployment

The bot is packaged as a Docker image and deployed via GitHub Actions to a Compute Engine instance on GCP.

On every push to `master`, the workflow:
1. Builds the image using a multi-stage Rust build with `cargo-chef` for dependency caching
2. Pushes it to Google Artifact Registry
3. Runs `gcloud compute instances update-container` to redeploy the instance

See `.github/workflows/deploy.yml` for the full workflow.

## Using the bot

### Environment Variables

```shell
# Discord
DISCORD_TOKEN=<bot token>
BOT_NAME=<display name used in embeds>
MOD_ROLE_ID=<Discord role ID>
RAIDER_ROLE_ID=<Discord role ID>

# WoW Audit
WOWAUDIT_TOKEN=<WoWAudit API token>

# Clips channels
CLIPS_CHANNEL_IDS=<comma-separated list of Discord channel IDs to restrict to video-only messages, e.g. 123,456>
```

Note: the bot needs the **Manage Messages** permission in any channel listed in `CLIPS_CHANNEL_IDS` to delete non-video messages.
