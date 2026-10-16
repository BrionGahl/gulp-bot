"""The WoW guild commands: the Bart Timeline Reminders token, class Discords and droptimizers."""

import logging

from discord import app_commands
from discord.ext import commands

from ..checks import check_is_raider, prefix_check
from ..context import GulpContext
from ..helper import base_embed
from ..web import RequestFailed, RequestTimeout, status_text
from .base import meta

log = logging.getLogger(__name__)

WOWUTILS_URL = "https://api.wowutils.com/v1/"

# How long WowUtils may take to take a report.
WOWUTILS_TIMEOUT = 30.0


@commands.hybrid_command(name="get_timeline_reminders", description="Fetches Bart Timeline Reminders addon info if you have a raider role.")
@prefix_check(check_is_raider)
async def get_timeline_reminders(ctx: GulpContext) -> None:
    # `check_is_raider` has already established this is a guild.
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    # Only ever the token this server registered, never another server's.
    bart_token = ctx.bot.guild_settings.get(guild_id).bart_token
    if bart_token is None:
        await ctx.send("No Bart token is registered for this server. An administrator or moderator can set one with `/settings bart_token`.")
        return

    embed = base_embed(ctx.bot.config.bot_name)
    embed.title = "Bart Timeline Reminders Addon Information"
    embed.description = "Please do not share this token publicly."
    embed.add_field(
        name="This is your tier 2 personal (permanent) access token. It is valid for the duration of our Patreon subscription.",
        value=f"```plaintext\n{bart_token}\n```",
        inline=True,
    )
    embed.add_field(
        name="Install Instructions",
        value=(
            "- Install WowUp with CurseForge from https://wowup.io/"
            "\n- Open up the WowUp app, and navigate to Options > Addons"
            '\n- In the bottom right, where it says "Personal Access Token", input the above token.'
            "\n- Navigate to Get Addons (sidebar) > Install from URL (top right)"
            "\n- Paste https://github.com/bart-dev-wow/TimelineReminders and click Import"
            "\n- You should then see the addon, click install"
        ),
        inline=False,
    )
    await ctx.send(embed=embed)


meta(get_timeline_reminders, category="WoW Guild", ephemeral=True)


@commands.hybrid_command(name="class_discords", description="Shows links to all the class Discord servers")
async def class_discords(ctx: GulpContext) -> None:
    embed = base_embed(ctx.bot.config.bot_name)
    embed.title = "Class Discords"
    embed.description = (
        "Death Knight - https://discord.gg/acherus\n"
        "Demon Hunter - https://discord.gg/felhammer\n"
        "Druid - https://discord.gg/dreamgrove\n"
        "Evoker - https://discord.gg/evoker\n"
        "Hunter - https://discord.gg/trueshot\n"
        "Mage - https://discord.gg/makGfZA\n"
        "Monk - https://discord.gg/peakofserenity\n"
        "Paladin - https://discord.gg/hammerofwrath\n"
        "Priest - https://discord.gg/WarcraftPriests\n"
        "Rogue - https://discord.gg/ravenholdt\n"
        "Shaman - https://discord.gg/earthshrine\n"
        "Warlock - https://discord.gg/BlackHarvest\n"
        "Warrior - https://discord.gg/SkyHold"
    )
    await ctx.send(embed=embed)


meta(class_discords, category="WoW Guild", ephemeral=True)


@commands.hybrid_command(name="submit_droptimizer", description="Submits a Raidbots droptimizer report to the guild's WowUtils group.")
@prefix_check(check_is_raider)
@app_commands.describe(url="Raidbots simulation report URL", profile_key="Profile key, e.g. heroic-max")
async def submit_droptimizer(ctx: GulpContext, url: str, profile_key: str) -> None:
    await ctx.defer(ephemeral=True)

    # `check_is_raider` has already established this is a guild.
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    # Reports only ever go to the group this server registered, never another server's.
    settings = ctx.bot.guild_settings.get(guild_id)
    if settings.wowutils_token is None or settings.wowutils_group_id is None:
        await ctx.send("WowUtils isn't set up for this server. An administrator or moderator can register it with `/settings wowutils`.")
        return

    await submit_droptimizer_report(ctx, settings.wowutils_token, settings.wowutils_group_id, url, profile_key)

    embed = base_embed(ctx.bot.config.bot_name)
    embed.title = "Droptimizer Submitted"
    embed.description = f"Submitted report for profile `{profile_key}`."
    await ctx.send(embed=embed)


meta(submit_droptimizer, category="WoW Guild", ephemeral=True)


async def submit_droptimizer_report(ctx: GulpContext, token: str, group_id: str, url: str, profile_key: str) -> None:
    """Submits a droptimizer report url and profile key to the given WowUtils group."""
    endpoint = f"{WOWUTILS_URL}groups/{group_id}/droptimizers"
    log.info("Submitting droptimizer report to WowUtils group %s", group_id)
    try:
        response = await ctx.bot.http_client.request(
            "POST",
            endpoint,
            timeout=WOWUTILS_TIMEOUT,
            headers={"Authorization": f"Bearer {token}"},
            json={"url": url, "profileKey": profile_key},
        )
    except (RequestTimeout, RequestFailed) as e:
        raise commands.CommandError(f"WowUtils couldn't be reached: {e}") from None
    if not response.ok:
        raise commands.CommandError(f"WowUtils API returned {status_text(response.status)}: {response.body}")
    log.info("Successfully submitted droptimizer report to WowUtils group %s", group_id)
