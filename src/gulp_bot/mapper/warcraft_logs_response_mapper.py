"""Warcraft Logs API JSON into the report schemas. Anything unexpected becomes a `WarcraftLogsError`."""

from __future__ import annotations

from typing import Any

from ..exception.warcraft_logs_exception import WarcraftLogsError
from ..schema.wcl_report_schema import Actor, DeathEntry, Fight, PlayerEntry, Report, ReportSummary

NOT_PUBLIC = "That report doesn't exist, or isn't public."


def _unexpected(error: Exception) -> WarcraftLogsError:
    return WarcraftLogsError(f"Warcraft Logs sent something unexpected: {error}")


def data_from_response(body: Any) -> dict[str, Any]:
    """The data of a response, or the API's error messages if it sent any (even alongside partial data)."""
    if not isinstance(body, dict):
        raise WarcraftLogsError("Warcraft Logs sent no data.")
    errors = body.get("errors")
    if errors:
        messages = [str(error.get("message")) if isinstance(error, dict) else str(error) for error in errors]
        raise WarcraftLogsError(f"Warcraft Logs reported: {'; '.join(messages)}")
    data = body.get("data")
    if data is None:
        raise WarcraftLogsError("Warcraft Logs sent no data.")
    return data


def report_object(data: dict[str, Any]) -> dict[str, Any]:
    """`reportData.report` of a response, which is null for a report that doesn't exist or is private."""
    report = (data.get("reportData") or {}).get("report")
    if report is None:
        raise WarcraftLogsError(NOT_PUBLIC)
    return report


def fight_from_json(data: dict[str, Any]) -> Fight:
    return Fight(
        id=int(data["id"]),
        name=str(data["name"]),
        kill=data.get("kill"),
        in_progress=data.get("inProgress"),
        start_time=float(data.get("startTime") or 0.0),
        end_time=float(data.get("endTime") or 0.0),
    )


def report_from_json(data: dict[str, Any]) -> Report:
    try:
        return Report(
            code=str(data["code"]),
            title=str(data["title"]),
            start_time=float(data["startTime"]),
            fights=tuple(fight_from_json(fight) for fight in data.get("fights") or []),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise _unexpected(e) from None


def report_summary_from_json(data: dict[str, Any]) -> ReportSummary:
    owner = data.get("owner")
    return ReportSummary(str(data["code"]), float(data["startTime"]), int(owner["id"]) if isinstance(owner, dict) else None)


def report_summaries_from_data(data: dict[str, Any]) -> list[ReportSummary]:
    """The guild's reports, from a `reportData.reports` response."""
    try:
        return [report_summary_from_json(entry) for entry in data["reportData"]["reports"]["data"]]
    except (KeyError, TypeError) as e:
        raise _unexpected(e) from None


def deaths_from_data(data: dict[str, Any]) -> list[DeathEntry]:
    """The entries of a deaths table."""
    report = report_object(data)
    try:
        entries = report["table"]["data"]["entries"]
        return [DeathEntry(str(entry["name"]), str(entry.get("type") or ""), int(entry["fight"]), float(entry["timestamp"])) for entry in entries]
    except (KeyError, TypeError, ValueError) as e:
        raise _unexpected(e) from None


def actors_from_data(data: dict[str, Any]) -> list[Actor]:
    """The enemies (NPCs) of a report, from its master data."""
    report = report_object(data)
    try:
        return [Actor(int(actor["id"]), str(actor["name"]), actor.get("subType") == "Boss") for actor in (report["masterData"] or {}).get("actors") or []]
    except (KeyError, TypeError, ValueError) as e:
        raise _unexpected(e) from None


def player_entries_from_table(table: Any) -> list[PlayerEntry]:
    """The entries of a damage taken, damage done or casts table."""
    try:
        return [PlayerEntry(str(entry["name"]), str(entry.get("type") or ""), float(entry["total"])) for entry in table["data"]["entries"]]
    except (KeyError, TypeError, ValueError) as e:
        raise _unexpected(e) from None
