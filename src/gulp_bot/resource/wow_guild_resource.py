"""The WoW guild commands: `/get_timeline_reminders`, `/class_discords` and `/submit_droptimizer`."""

from __future__ import annotations

from discord import app_commands
from discord.ext import commands

from ..core.discord_context import GulpContext, require_guild
from ..core.embed import add_field, command_embed, to_discord_embed
from ..core.permission_checks import check_is_raider, prefix_check
from ..exception.wowutils_exception import WowUtilsError
from .resource_support import meta

CLASS_DISCORDS = (
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

TIMELINE_REMINDERS_INSTALL = (
    "- Install WowUp with CurseForge from https://wowup.io/"
    "\n- Open up the WowUp app, and navigate to Options > Addons"
    '\n- In the bottom right, where it says "Personal Access Token", input the above token.'
    "\n- Navigate to Get Addons (sidebar) > Install from URL (top right)"
    "\n- Paste https://github.com/bart-dev-wow/TimelineReminders and click Import"
    "\n- You should then see the addon, click install"
)


@commands.hybrid_command(name="get_timeline_reminders", description="Fetches Bart Timeline Reminders addon info if you have a raider role.")
@prefix_check(check_is_raider)
async def get_timeline_reminders(ctx: GulpContext) -> None:
    # `check_is_raider` has already established this is a guild.
    guild_id = require_guild(ctx)
    # Only ever the token this server registered, never another server's.
    bart_token = ctx.services.guild_settings.get(guild_id).bart_token
    if bart_token is None:
        await ctx.send("No Bart token is registered for this server. An administrator or moderator can set one with `/settings bart_token`.")
        return

    embed = command_embed(
        ctx.services.config.bot_name, title="Bart Timeline Reminders Addon Information", description="Please do not share this token publicly."
    )
    add_field(
        embed,
        "This is your tier 2 personal (permanent) access token. It is valid for the duration of our Patreon subscription.",
        f"```plaintext\n{bart_token}\n```",
        inline=True,
    )
    add_field(embed, "Install Instructions", TIMELINE_REMINDERS_INSTALL, inline=False)
    await ctx.send(embed=to_discord_embed(embed))


meta(get_timeline_reminders, category="WoW Guild", ephemeral=True)


@commands.hybrid_command(name="class_discords", description="Shows links to all the class Discord servers")
async def class_discords(ctx: GulpContext) -> None:
    await ctx.send(embed=to_discord_embed(command_embed(ctx.services.config.bot_name, title="Class Discords", description=CLASS_DISCORDS)))


meta(class_discords, category="WoW Guild", ephemeral=True)


@commands.hybrid_command(name="submit_droptimizer", description="Submits a Raidbots droptimizer report to the guild's WowUtils group.")
@prefix_check(check_is_raider)
@app_commands.describe(url="Raidbots simulation report URL", profile_key="Profile key, e.g. heroic-max")
async def submit_droptimizer(ctx: GulpContext, url: str, profile_key: str) -> None:
    await ctx.defer(ephemeral=True)

    # `check_is_raider` has already established this is a guild.
    guild_id = require_guild(ctx)
    # Reports only ever go to the group this server registered, never another server's.
    credentials = ctx.services.guild_settings.get(guild_id).wowutils
    if credentials is None:
        await ctx.send("WowUtils isn't set up for this server. An administrator or moderator can register it with `/settings wowutils`.")
        return

    try:
        await ctx.services.droptimizer.submit(credentials, url, profile_key)
    except WowUtilsError as e:
        raise commands.CommandError(str(e)) from None
    await ctx.send(
        embed=to_discord_embed(
            command_embed(ctx.services.config.bot_name, title="Droptimizer Submitted", description=f"Submitted report for profile `{profile_key}`.")
        )
    )


meta(submit_droptimizer, category="WoW Guild", ephemeral=True)

COMMANDS = [get_timeline_reminders, class_discords, submit_droptimizer]
