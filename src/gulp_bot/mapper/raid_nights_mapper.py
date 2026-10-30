"""Raid nights as officers read them."""

from __future__ import annotations

from ..core.schedule_parsing import days_text, time_text
from ..schema.guild_settings_schema import RaidNights


def describe_raid_nights(nights: RaidNights) -> str:
    """`Tue, Thu, 8:00 PM – 11:00 PM Eastern`; a raid that ends before it starts runs past midnight."""
    past_midnight = " (the next day)" if nights.end < nights.start else ""
    return f"{days_text(nights.days)}, {time_text(nights.start)} – {time_text(nights.end)}{past_midnight} Eastern"
