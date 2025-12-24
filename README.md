<h1 color="#000000" size="100px" align="center"> yuh-bot </h1>

## Goal / Purpose

The ideas behind the creation of this bot are listed below:
- consolidate bots in my Discord servers
- learn and experiment with Rust

The bot is written with the `poise` crate and uses a few others for requests / logging sinks.



## Using the bot

### Environment Variables

These will likely change version to version quite frequently.
Eventually I would prefer to shift to storing the role information on some database for specific guilds to assign via commands.
```shell
DISCORD_TOKEN=<Bot token>
BART_TOKEN=<Personal access token to Bart\'s patreon> 
MOD_ROLE_ID=<Discord role ID>
RAIDER_ROLE_ID=<Discord role ID>
WOWAUDIT_TOKEN=<Personal guild WoW Audit API token>
```
