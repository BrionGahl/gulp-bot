<h1 color="#000000" size="100px" align="center"> gulp-bot </h1>

## Goal / Purpose

The ideas behind the creation of this bot are listed below:
- consolidate bots in my Discord servers
- learn and experiment with Rust

The bot is written with the `poise` crate and uses a few others for requests / logging sinks.

## Features

- **WoW Guild** — Bart Timeline Reminders addon info, class Discord links, and droptimizer report submission via WowUtils
- **Gambling** — multiplayer roll sessions with a lobby and results embed
- **Clips channels** — deletes any message posted in a configured channel that doesn't contain a video (uploaded video file or an unfurled video link embed)

## Deployment

The bot is packaged as a Docker image and deployed via GitHub Actions to Cloud Run on GCP.

On every push to `master`, the workflow:
1. Builds the image using a multi-stage Rust build with `cargo-chef` for dependency caching
2. Pushes it to Google Artifact Registry
3. Deploys it to Cloud Run, pinned to exactly one always-on instance with CPU throttling disabled
4. Deletes old Cloud Run revisions, keeping only the one now serving traffic and the one before it
   (a manual rollback target)

The bot has no HTTP API of its own — it only holds a persistent Discord gateway connection — so it
runs a minimal HTTP listener (`src/health.rs`) purely to satisfy Cloud Run's container startup check.

See `.github/workflows/deploy.yml` for the full workflow, and the [Cloud Run setup](#one-time-cloud-run-setup)
section below for the one-time GCP configuration this requires.

### Tearing down

`.github/workflows/teardown.yml` is a manually-triggered workflow (`Actions` tab → `Teardown Cloud Run` →
`Run workflow`) that deletes the `gulp-bot` Cloud Run service. It requires typing `gulp-bot` into the
confirmation input, or the job fails before touching anything. It only deletes the Cloud Run service —
pushed images stay in Artifact Registry, and the next push to `master` will recreate the service.

### One-time Cloud Run setup

This is manual GCP configuration that only needs to happen once (or once per environment), not
something the workflow does for you:

1. Enable the Cloud Run API on the project: `gcloud services enable run.googleapis.com --project=<PROJECT_ID>`.
2. Grant the WIF deploy service account (`secrets.WIF_SERVICE_ACCOUNT`) the roles needed to deploy to Cloud Run:
   ```shell
   gcloud projects add-iam-policy-binding <PROJECT_ID> \
     --member="serviceAccount:<WIF_SERVICE_ACCOUNT>" --role="roles/run.admin"
   gcloud projects add-iam-policy-binding <PROJECT_ID> \
     --member="serviceAccount:<WIF_SERVICE_ACCOUNT>" --role="roles/iam.serviceAccountUser"
   ```
3. In the GitHub repo, set these Actions **variables** (`Settings > Secrets and variables > Actions > Variables`)
   alongside the existing `GCP_REGION` / `GCP_PROJECT_ID`: `BOT_NAME`, `MOD_ROLE_ID`, `RAIDER_ROLE_ID`,
   `CLIPS_CHANNEL_IDS`, `LOG_LEVEL`, `WOWUTILS_GROUP_ID`.
4. Set these Actions **secrets** (`Settings > Secrets and variables > Actions > Secrets`):
   `DISCORD_TOKEN`, `BART_TOKEN`, `WOWUTILS_TOKEN`. They're passed to the Cloud Run service as plain
   environment variables at deploy time — GitHub masks them in workflow logs, but anyone with viewer
   access to the Cloud Run service in the GCP console can read them back out of its revision config.
   If that's ever a concern, Secret Manager is the more locked-down alternative.
5. The old `GCE_INSTANCE` / `GCE_ZONE` variables and the Compute Engine instance itself are no longer
   used and can be decommissioned once the first Cloud Run deploy succeeds.

## Using the bot

### Environment Variables

```shell
# Discord
DISCORD_TOKEN=<bot token>
BOT_NAME=<display name used in embeds>
MOD_ROLE_ID=<Discord role ID>
RAIDER_ROLE_ID=<Discord role ID>

# WowUtils
WOWUTILS_TOKEN=<WowUtils API token, sent as `Authorization: Bearer <token>`>
WOWUTILS_GROUP_ID=<WowUtils group ID that droptimizer reports are submitted to>

# Clips channels
CLIPS_CHANNEL_IDS=<comma-separated list of Discord channel IDs to restrict to video-only messages, e.g. 123,456>
```

Note: the bot needs the **Manage Messages** permission in any channel listed in `CLIPS_CHANNEL_IDS` to delete non-video messages.
