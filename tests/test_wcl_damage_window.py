"""`/wcl damage_window`: the damage done to each tracked enemy in a moment of a pull."""

import asyncio
import json
import re

import pytest

from gulp_bot.client.warcraft_logs_client import (
    ACTORS_QUERY,
    ALL_DAMAGE_DONE_QUERY,
    TARGETS_PER_QUERY,
    WarcraftLogsClient,
    damage_done_tables_query,
    target_alias,
)
from gulp_bot.core.embed import MAX_EMBED_CHARS, MAX_FIELD_CHARS
from gulp_bot.core.http_client import Response
from gulp_bot.core.text import pull_time
from gulp_bot.exception.warcraft_logs_exception import WarcraftLogsError
from gulp_bot.mapper.warcraft_logs_embed_mapper import WINDOW_DAMAGE_SHOWN, window_damage_embed, window_url
from gulp_bot.mapper.warcraft_logs_response_mapper import actors_from_data
from gulp_bot.schema.wcl_report_schema import Actor, BossRow, EnemyDamage, Fight, FocusRow, PlayerEntry, PlayerTotal, Report, WindowDamageReport
from gulp_bot.service.warcraft_logs_analysis_service import (
    boss_ids,
    build_boss_damage,
    build_enemy_damage,
    build_focus_damage,
    enemy_targets,
    parse_pull_time,
    pull_window,
    select_pull,
)
from gulp_bot.service.warcraft_logs_service import WarcraftLogsService
from gulp_bot.service.wcl_tracked_enemy_service import same_enemy


def fight(fight_id, name, start, end, kill=False, in_progress=False):
    return Fight(fight_id, name, kill, in_progress, float(start), float(end))


# Two bosses; the second pull of Nexus-King is still being uploaded.
FIGHTS = [
    fight(1, "Plexus Sentinel", 10_000, 250_000, kill=True),
    fight(2, "Nexus-King Salhadaar", 300_000, 420_000),
    fight(3, "Nexus-King Salhadaar", 500_000, 690_000),
    fight(4, "Nexus-King Salhadaar", 700_000, 760_000, in_progress=True),
]


def entry(name, total, class_name="Mage"):
    return PlayerEntry(name, class_name, total)


# --- Times into a pull ---


def test_times_are_seconds_or_minutes_and_seconds():
    assert [parse_pull_time(text) for text in ["40", "40s", " 40 S ", "0:40", "1:05", "12:00", "0"]] == [40, 40, 40, 40, 65, 720, 0]
    for wrong in ["", "-5", "1:5", "1:60", "1m", "4o", "١٢", "1:00:00", "99999", "0:40s"]:
        assert parse_pull_time(wrong) is None, wrong
    assert parse_pull_time("59:59") == 3599 and parse_pull_time("3600") is None


def test_times_are_written_as_minutes_and_seconds():
    assert [pull_time(ms) for ms in [0, 999, 40_000, 65_500, 600_000]] == ["0:00", "0:00", "0:40", "1:05", "10:00"]


# --- Which pull, and which moment of it ---


def test_the_last_finished_pull_of_the_boss_is_the_default():
    chosen, number = select_pull(FIGHTS, "nexus", None)
    assert (chosen.id, number) == (3, 2)
    chosen, number = select_pull(FIGHTS, "Nexus-King", 1)
    assert (chosen.id, number) == (2, 1)


def test_a_pull_that_isnt_there_or_a_boss_name_that_fits_two_bosses_is_refused():
    with pytest.raises(WarcraftLogsError, match="has 2 Mythic pulls of Nexus-King Salhadaar. Pick one from 1 to 2"):
        select_pull(FIGHTS, "nexus", 3)
    with pytest.raises(WarcraftLogsError, match="matches more than one boss: Nexus-King Salhadaar, Plexus Sentinel"):
        select_pull(FIGHTS, "xus", None)
    with pytest.raises(WarcraftLogsError, match="No Mythic pulls of a boss matching `Dimensius`"):
        select_pull(FIGHTS, "Dimensius", None)


def test_the_window_is_counted_from_the_start_of_the_pull_and_cut_at_its_end():
    window = pull_window(FIGHTS[1], 1, 40, 60)
    assert (window.start_ms, window.end_ms, window.cut_at_pull_end) == (40_000, 60_000, False)
    assert (window.report_start_ms, window.report_end_ms) == (340_000, 360_000)

    # The pull lasted 2:00.
    window = pull_window(FIGHTS[1], 1, 100, 200)
    assert (window.end_ms, window.cut_at_pull_end, window.report_end_ms) == (120_000, True, 420_000)


def test_an_empty_window_or_one_after_the_pull_is_refused():
    with pytest.raises(WarcraftLogsError, match="end of the window has to be after its start"):
        pull_window(FIGHTS[1], 1, 60, 60)
    with pytest.raises(WarcraftLogsError, match=r"Pull 1 of Nexus-King Salhadaar lasted 2:00, so nothing happened from 2:00 on\."):
        pull_window(FIGHTS[1], 1, 120, 130)


# --- Enemies by name ---


def test_enemies_with_the_same_name_count_as_one_and_names_ignore_case():
    actors = [Actor(10, "Sporecaller"), Actor(11, "Nexus-King Salhadaar"), Actor(12, "sporecaller"), Actor(13, "Sporecaller"), Actor(10, "Sporecaller")]
    assert enemy_targets(actors, ["SPORECALLER ", "Nexus-King Salhadaar", "Dimensius"]) == [[10, 12, 13], [11], []]
    assert same_enemy("Sporecaller", " sporecaller") and not same_enemy("Sporecaller", "Spore caller")


def test_the_damage_to_an_enemy_is_added_over_every_one_of_that_name():
    tables = {10: [entry("Aaron", 1_000), entry("Bea", 500, "Priest")], 12: [entry("Aaron", 250), entry("Ignored", 9_999)], 13: []}
    damage = build_enemy_damage("Sporecaller", [10, 12, 13], tables, {"ignored"})
    assert damage == EnemyDamage("Sporecaller", True, 1_750, [PlayerTotal("Aaron", "Mage", 1_250), PlayerTotal("Bea", "Priest", 500)])
    assert build_enemy_damage("Dimensius", [], tables, set()) == EnemyDamage("Dimensius", False, 0, [])


# --- The API ---


def test_the_damage_done_query_has_a_table_for_each_target_in_the_window():
    query = damage_done_tables_query([10, 12])
    assert "$startTime: Float, $endTime: Float" in query and "$fightIDs: [Int]" in query
    assert f"{target_alias(0)}: table(dataType: DamageDone, fightIDs: $fightIDs, startTime: $startTime, endTime: $endTime, targetID: 10)" in query
    assert f"{target_alias(1)}: table(" in query and "targetID: 12)" in query
    assert "wipeCutoff" not in query and target_alias(2) not in query


def test_a_reports_enemies_are_read_from_its_master_data():
    data = {"reportData": {"report": {"masterData": {"actors": [{"id": 5, "name": "Sporecaller", "gameID": 1}, {"id": 6, "name": "Boss"}]}}}}
    assert actors_from_data(data) == [Actor(5, "Sporecaller"), Actor(6, "Boss")]
    assert actors_from_data({"reportData": {"report": {"masterData": {"actors": None}}}}) == []
    with pytest.raises(WarcraftLogsError, match="something unexpected"):
        actors_from_data({"reportData": {"report": {"masterData": {"actors": [{"name": "No ID"}]}}}})


class DamageDoneApi:
    """Answers damage done queries with a table for each target alias, and records what was asked."""

    def __init__(self):
        self.queries = []

    async def request(self, method, url, *, timeout, headers=None, json=None, form=None, basic_auth=None):
        if url.endswith("/oauth/token"):
            return Response(200, '{"access_token":"test","expires_in":3600}')
        self.queries.append(json)
        aliases = re.findall(r"\bt([0-9]+): table", json["query"])
        tables = {f"t{alias}": {"data": {"entries": [{"name": "Aaron", "type": "Mage", "total": 100 + int(alias)}]}} for alias in aliases}
        return Response(200, _dumps({"data": {"reportData": {"report": tables}}}))


def _dumps(value):
    return json.dumps(value)


def test_damage_done_is_asked_for_one_pull_between_two_moments_in_chunks():
    api = DamageDoneApi()
    client = WarcraftLogsClient(api, "id", "secret")  # type: ignore[arg-type]
    target_ids = list(range(1, TARGETS_PER_QUERY + 3))
    tables = asyncio.run(client.fetch_damage_done_tables("ABC", 3, 540_000, 560_000, target_ids))

    assert len(api.queries) == 2
    assert api.queries[0]["variables"] == {"code": "ABC", "fightIDs": [3], "startTime": 540_000.0, "endTime": 560_000.0}
    assert sorted(tables) == target_ids
    # Each chunk's aliases start again from t0, and are matched back to their target.
    assert tables[1] == [PlayerEntry("Aaron", "Mage", 100.0)] and tables[TARGETS_PER_QUERY + 1] == [PlayerEntry("Aaron", "Mage", 100.0)]
    assert tables[TARGETS_PER_QUERY] == [PlayerEntry("Aaron", "Mage", 100.0 + TARGETS_PER_QUERY - 1)]


class FakeClient:
    def __init__(self, actors, tables):
        self.actors = actors
        self.tables = tables
        self.asked = None

    async def fetch_report(self, code):
        return Report(code, "Raid night", 1_791_506_660_309.0, FIGHTS)

    async def fetch_enemies(self, code):
        return self.actors

    async def fetch_damage_done_tables(self, code, fight_id, start_ms, end_ms, target_ids):
        self.asked = (fight_id, start_ms, end_ms, target_ids)
        return {target_id: self.tables.get(target_id, []) for target_id in target_ids}


def test_the_report_has_each_tracked_enemy_in_order_for_the_pull_asked_for():
    client = FakeClient(
        [Actor(10, "Sporecaller"), Actor(12, "Sporecaller"), Actor(11, "Nexus-King Salhadaar")], {10: [entry("Aaron", 300)], 12: [entry("Bea", 900)]}
    )
    service = WarcraftLogsService(None, None, None)  # type: ignore[arg-type]
    report = asyncio.run(service.window_damage_report(client, "ABC", "nexus", 2, 40, 60, ["Sporecaller", "Dimensius", "Nexus-King Salhadaar"], ["Cid"]))  # type: ignore[arg-type]

    assert client.asked == (3, 540_000, 560_000, [10, 12, 11])
    assert (report.boss, report.pull, report.fight_id, report.start_ms, report.end_ms, report.ignored) == ("Nexus-King Salhadaar", 2, 3, 40_000, 60_000, 1)
    assert [(enemy.name, enemy.in_report, enemy.total) for enemy in report.enemies] == [
        ("Sporecaller", True, 1_200),
        ("Dimensius", False, 0),
        ("Nexus-King Salhadaar", True, 0),
    ]
    assert [player.name for player in report.enemies[0].players] == ["Bea", "Aaron"]


def test_no_enemy_of_those_names_asks_nothing_more_of_the_api():
    client = FakeClient([Actor(11, "Nexus-King Salhadaar")], {})
    service = WarcraftLogsService(None, None, None)  # type: ignore[arg-type]
    report = asyncio.run(service.window_damage_report(client, "ABC", "nexus", None, 0, 30, ["Dimensius"], []))  # type: ignore[arg-type]
    assert client.asked is None and not report.enemies[0].in_report


# --- The embed ---


def window_report(enemies, cut=False, kill=False):
    return WindowDamageReport("ABC", "Raid night", 1_791_506_660, 3, "Nexus-King Salhadaar", 2, kill, 40_000, 60_000, cut, 540_000, 560_000, enemies, 0)


def test_the_embed_lists_each_enemy_with_the_total_each_player_did():
    players = [PlayerTotal("Aaron", "DeathKnight", 1_234_567), PlayerTotal("Bea", "Priest", 900)]
    embed = window_damage_embed(
        window_report([EnemyDamage("Sporecaller", True, 1_235_467, players), EnemyDamage("Dimensius", False, 0, []), EnemyDamage("Boss", True, 0, [])])
    )
    assert embed["title"] == "Nexus-King Salhadaar — pull 2, 0:40–1:00"
    assert embed["url"] == window_url(window_report([])) == "https://www.warcraftlogs.com/reports/ABC?fight=3&type=damage-done&start=540000&end=560000"
    assert "from **0:40** to **1:00** of pull 2 (wipe)" in embed["description"] and "stops there" not in embed["description"]
    fields = embed["fields"]
    assert fields[0]["name"] == "Sporecaller — 1.2M damage"
    assert fields[0]["value"] == "1. **Aaron** (Death Knight) — 1.2M\n2. **Bea** (Priest) — 900"
    # Only the damage: no share, and no damage per second.
    assert "%" not in fields[0]["value"] and "DPS" not in fields[0]["value"]
    assert fields[1] == {"name": "Dimensius", "value": "Not in this report. Check the name against the report's Damage Done tab, by target.", "inline": False}
    assert fields[2]["value"] == "Nobody damaged it in this window."


def test_a_window_cut_at_the_end_of_a_kill_says_so():
    embed = window_damage_embed(window_report([EnemyDamage("Boss", True, 0, [])], cut=True, kill=True))
    assert "of pull 2 (kill)\nThe pull ended at 1:00, so the window stops there." in embed["description"]


def test_six_enemies_with_a_whole_raid_each_stay_within_discords_limits():
    players = [PlayerTotal(f"Player{n:02}", "DemonHunter", 10_000_000 - n) for n in range(30)]
    enemies = [EnemyDamage(f"Enemy with a rather long name number {n}", True, 1, players) for n in range(6)]
    embed = window_damage_embed(window_report(enemies))
    fields = embed["fields"]
    assert len(fields) == 6
    assert all(len(field["value"]) <= MAX_FIELD_CHARS for field in fields)
    size = len(embed["title"]) + len(embed["description"]) + len(embed["footer"]["text"]) + sum(len(f["name"]) + len(f["value"]) for f in fields)
    assert size <= MAX_EMBED_CHARS
    assert fields[0]["value"].count("\n") <= WINDOW_DAMAGE_SHOWN and "more." in fields[0]["value"]


# --- One enemy set apart ---


def enemy(name, *players, in_report=True):
    ranking = [PlayerTotal(player, "Mage", total) for player, total in players]
    return EnemyDamage(name, in_report, sum(total for _, total in players), ranking)


def test_one_enemy_is_set_apart_and_the_other_tracked_ones_are_added_together():
    enemies = [
        enemy("Boss", ("Aaron", 100), ("Bea", 300)),
        enemy("Sporecaller", ("Aaron", 50), ("Cid", 10)),
        enemy("Add", ("Bea", 5), ("Dee", 70)),
        enemy("Dimensius", in_report=False),
    ]
    focus = build_focus_damage("sporecaller ", enemies)
    assert (focus.name, focus.in_report, focus.total, focus.rest_names, focus.rest_total) == ("Sporecaller", True, 60, ["Boss", "Add"], 475)
    # By the damage to the enemy set apart, then to the rest; a player who only hit the rest is still there.
    assert focus.players == [
        FocusRow("Aaron", "Mage", 50, 100),
        FocusRow("Cid", "Mage", 10, 0),
        FocusRow("Bea", "Mage", 0, 305),
        FocusRow("Dee", "Mage", 0, 70),
    ]


def test_an_enemy_set_apart_that_isnt_in_the_report_has_nothing_of_its_own():
    focus = build_focus_damage("Dimensius", [enemy("Boss", ("Aaron", 100)), enemy("Dimensius", in_report=False)])
    assert (focus.in_report, focus.total, focus.rest_names, focus.rest_total) == (False, 0, ["Boss"], 100)


def test_the_report_sets_the_enemy_apart_when_asked():
    client = FakeClient([Actor(10, "Sporecaller"), Actor(11, "Boss")], {10: [entry("Aaron", 300)], 11: [entry("Aaron", 1_000), entry("Bea", 50)]})
    service = WarcraftLogsService(None, None, None)  # type: ignore[arg-type]
    report = asyncio.run(service.window_damage_report(client, "ABC", "nexus", None, 0, 30, ["Boss", "Sporecaller"], [], "Sporecaller"))  # type: ignore[arg-type]
    assert report.focus is not None and report.focus.players == [FocusRow("Aaron", "Mage", 300, 1_000), FocusRow("Bea", "Mage", 0, 50)]
    without = asyncio.run(service.window_damage_report(client, "ABC", "nexus", None, 0, 30, ["Boss", "Sporecaller"], []))  # type: ignore[arg-type]
    assert without.focus is None


def focus_report(focus, enemies):
    return WindowDamageReport("ABC", "Raid night", 1_791_506_660, 3, "Boss", 2, False, 40_000, 60_000, False, 540_000, 560_000, enemies, 0, focus)


def test_the_embed_with_an_enemy_set_apart_has_one_ranking_with_both_columns():
    enemies = [enemy("Boss", ("Aaron", 2_500_000)), enemy("Sporecaller", ("Aaron", 1_234_567)), enemy("Add", ("Bea", 900)), enemy("Gone", in_report=False)]
    embed = window_damage_embed(focus_report(build_focus_damage("Sporecaller", enemies), enemies))
    assert "**Sporecaller** set apart; the rest is Boss and Add.\nNot in this report: Gone." in embed["description"]
    assert len(embed["fields"]) == 1
    field = embed["fields"][0]
    assert field["name"] == "Sporecaller — 1.2M · the rest — 2.5M"
    assert field["value"] == "1. **Aaron** (Mage) — 1.2M · rest 2.5M\n2. **Bea** (Mage) — 0 · rest 900"


def test_the_embed_says_when_the_enemy_set_apart_or_the_rest_is_missing():
    alone = [enemy("Sporecaller", ("Aaron", 10))]
    embed = window_damage_embed(focus_report(build_focus_damage("Sporecaller", alone), alone))
    assert "the rest is nothing: no other tracked enemy is in this report." in embed["description"]
    missing = [enemy("Sporecaller", in_report=False), enemy("Boss", ("Aaron", 10))]
    embed = window_damage_embed(focus_report(build_focus_damage("Sporecaller", missing), missing))
    assert embed["fields"][0]["value"].startswith("Not in this report.")
    nobody = [enemy("Sporecaller"), enemy("Boss")]
    embed = window_damage_embed(focus_report(build_focus_damage("Sporecaller", nobody), nobody))
    assert embed["fields"][0]["value"] == "Nobody damaged the tracked enemies in this window."


def test_a_whole_raid_with_an_enemy_set_apart_stays_within_discords_limits():
    players = [(f"Player{n:02}", 10_000_000 - n) for n in range(30)]
    enemies = [enemy(f"Enemy with a rather long name number {n}", *players) for n in range(6)]
    embed = window_damage_embed(focus_report(build_focus_damage(enemies[0].name, enemies), enemies))
    value = embed["fields"][0]["value"]
    assert len(value) <= MAX_FIELD_CHARS and "more." in value and len(embed["fields"][0]["name"]) <= 256


# --- Boss against non-boss damage ---


def test_bosses_are_the_enemies_warcraft_logs_marks_as_bosses():
    data = {"reportData": {"report": {"masterData": {"actors": [{"id": 5, "name": "Add", "subType": "NPC"}, {"id": 6, "name": "Boss", "subType": "Boss"}]}}}}
    actors = actors_from_data(data)
    assert actors == [Actor(5, "Add", False), Actor(6, "Boss", True)]
    assert boss_ids([*actors, Actor(6, "Boss", True), Actor(9, "Second boss", True)]) == [6, 9]
    assert "subType" in ACTORS_QUERY
    assert "targetID" not in ALL_DAMAGE_DONE_QUERY and "startTime: $startTime, endTime: $endTime" in ALL_DAMAGE_DONE_QUERY


def test_non_boss_damage_is_everything_less_the_damage_to_the_bosses():
    actors = [Actor(6, "Boss", True), Actor(7, "Twin", True), Actor(8, "Elsewhere", True)]
    boss_tables = {6: [entry("Aaron", 1_000), entry("Bea", 200)], 7: [entry("Aaron", 500)], 8: []}
    everything = [entry("Aaron", 1_800), entry("Bea", 900, "Priest"), entry("Cid", 300), entry("Ignored", 5_000)]
    split = build_boss_damage(actors, boss_tables, everything, {"ignored"})
    assert split.boss_names == ["Boss", "Twin"]
    assert (split.boss_total, split.other_total) == (1_700, 1_300)
    assert split.players == [BossRow("Aaron", "Mage", 1_500, 300), BossRow("Bea", "Mage", 200, 700), BossRow("Cid", "Mage", 0, 300)]


class BossClient(FakeClient):
    def __init__(self, actors, tables, everything):
        super().__init__(actors, tables)
        self.everything = everything
        self.asked_all = None

    async def fetch_all_damage_done(self, code, fight_id, start_ms, end_ms):
        self.asked_all = (fight_id, start_ms, end_ms)
        return self.everything


def test_the_boss_damage_report_asks_for_the_bosses_and_for_everything_in_the_window():
    client = BossClient([Actor(6, "Nexus-King Salhadaar", True), Actor(10, "Sporecaller")], {6: [entry("Aaron", 1_000)]}, [entry("Aaron", 1_250)])
    service = WarcraftLogsService(None, None, None)  # type: ignore[arg-type]
    report = asyncio.run(service.window_boss_damage_report(client, "ABC", "nexus", 2, 40, 60, []))  # type: ignore[arg-type]
    assert client.asked == (3, 540_000, 560_000, [6]) and client.asked_all == (3, 540_000, 560_000)
    assert report.enemies == [] and report.focus is None
    assert report.boss_damage is not None and report.boss_damage.players == [BossRow("Aaron", "Mage", 1_000, 250)]


def test_the_boss_damage_embed_has_one_ranking_with_both_columns():
    split = build_boss_damage([Actor(6, "Nexus-King Salhadaar", True)], {6: [entry("Aaron", 2_500_000)]}, [entry("Aaron", 3_000_000), entry("Bea", 900)], set())
    report = WindowDamageReport("ABC", "Raid night", 1_791_506_660, 3, "Boss", 2, False, 40_000, 60_000, False, 540_000, 560_000, [], 0, boss_damage=split)
    embed = window_damage_embed(report)
    assert "Damage done to the bosses and everything else from **0:40** to **1:00**" in embed["description"]
    assert "Bosses: Nexus-King Salhadaar." in embed["description"]
    assert embed["fields"] == [
        {
            "name": "Boss — 2.5M · non-boss — 500.9K",
            "value": "1. **Aaron** (Mage) — 2.5M · non-boss 500.0K\n2. **Bea** (Mage) — 0 · non-boss 900",
            "inline": False,
        }
    ]
    empty = WindowDamageReport("ABC", "Raid night", 1, 3, "Boss", 2, None, 0, 1_000, False, 0, 1_000, [], 0, boss_damage=build_boss_damage([], {}, [], set()))
    assert window_damage_embed(empty)["fields"][0]["value"] == "Nobody did any damage in this window."
