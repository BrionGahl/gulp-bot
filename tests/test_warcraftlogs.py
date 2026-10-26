import asyncio
import json

import pytest

from gulp_bot.client.warcraft_logs_client import DEATHS_QUERY, REPORT_QUERY, WarcraftLogsClient, WarcraftLogsClientPool, ability_tables_query, table_alias
from gulp_bot.core.embed import MAX_DESCRIPTION_CHARS, MAX_EMBED_CHARS, MAX_FIELD_CHARS
from gulp_bot.core.http_client import RequestTimeout, Response
from gulp_bot.core.text import plural, truncate_with_ellipsis
from gulp_bot.exception.warcraft_logs_exception import WarcraftLogsError
from gulp_bot.mapper.warcraft_logs_embed_mapper import (
    CONSUMABLE_PLAYERS_SHOWN,
    DAMAGE_SHOWN,
    DAMAGE_SHOWN_IN_OVERVIEW,
    consumable_field,
    consumables_embed,
    damage_embed,
    damage_field,
    damage_rows,
    death_rows,
    display_class,
    footer_text,
    format_damage,
    overview_description,
    overview_embed,
    ranked_list,
    replay_url,
)
from gulp_bot.mapper.warcraft_logs_response_mapper import data_from_response, deaths_from_data, player_entries_from_table, report_summary_from_json
from gulp_bot.schema.guild_settings_schema import GuildSettings, WclCredentials, WclSource
from gulp_bot.schema.wcl_report_schema import DeathEntry, EncounterSummary, Fight, NewestReport, PlayerCount, PlayerEntry, Report, ReportCode
from gulp_bot.schema.wcl_settings_schema import ConsumableAbility, ConsumableKind, TrackedAbility
from gulp_bot.service.warcraft_logs_analysis_service import (
    add_totals,
    build_ability_damage,
    build_consumables_report,
    build_damage_report,
    build_last_wipe,
    build_overview,
    first_death_of_each_pull,
    ignored_set,
    is_ignored,
    newest_by_uploader,
    parse_report_code,
    parse_report_target,
    resolve_ability,
    select_fights,
    tally,
    wipe_moment,
)
from gulp_bot.service.wcl_consumable_service import ids_of_kind
from gulp_bot.service.wcl_ignored_player_service import same_name


def death(name, class_name, fight, timestamp):
    return DeathEntry(name, class_name, fight, timestamp)


def player(name, count):
    return PlayerCount(name, "Rogue", count)


def tracked(ability_id, name):
    return TrackedAbility(ability_id, name)


def report(fights):
    return Report(
        "ABC",
        "Raid night",
        1_791_506_660_309.0,
        [Fight(index + 1, name, kill, in_progress) for index, (name, kill, in_progress) in enumerate(fights)],
    )


def embed_text_size(embed):
    fields = sum(len(field["name"]) + len(field["value"]) for field in embed.get("fields", []))
    return len(embed.get("title", "")) + len(embed.get("description", "")) + len(embed.get("footer", {}).get("text", "")) + fields


def test_a_guilds_client_is_kept_until_its_credentials_change():
    clients = WarcraftLogsClientPool(object())  # type: ignore[arg-type]
    first = clients.client_for(1, WclCredentials("id", "one"))
    assert clients.client_for(1, WclCredentials("id", "one")) is first
    # Another guild gets a client of its own even with the same credentials.
    assert clients.client_for(2, WclCredentials("id", "one")) is not first
    replaced = clients.client_for(1, WclCredentials("id", "two"))
    assert replaced is not first and replaced._client_secret == "two"
    assert "two" not in repr(replaced) and "one" not in repr(WclCredentials("id", "one"))


def test_report_codes_are_read_from_links_and_bare_codes():
    code = "YRwPxnJ4MKpWFyrg"
    for given in [
        code,
        f"  {code} ",
        f"https://www.warcraftlogs.com/reports/{code}",
        f"https://www.warcraftlogs.com/reports/{code}#fight=3&type=deaths",
        f"https://www.warcraftlogs.com/reports/{code}?fight=3",
        f"https://www.warcraftlogs.com/reports/{code}/",
    ]:
        assert parse_report_code(given) == code
    for bad in ["", "   ", "not a code!", 'abc"){ evil }', "a" * 33]:
        assert parse_report_code(bad) is None


def test_the_queries_ask_for_mythic_and_the_cutoff():
    assert "fights(difficulty: 5, killType: Encounters)" in REPORT_QUERY
    assert "wipeCutoff: 2)" in DEATHS_QUERY


def test_the_ability_tables_query_has_a_table_for_each_ability_with_or_without_the_cutoff():
    assert ability_tables_query("DamageTaken", [1282408, 1299838], True) == (
        "query($code: String!, $fightIDs: [Int]) { reportData { report(code: $code) { "
        "a0: table(dataType: DamageTaken, fightIDs: $fightIDs, abilityID: 1282408, wipeCutoff: 2) "
        "a1: table(dataType: DamageTaken, fightIDs: $fightIDs, abilityID: 1299838, wipeCutoff: 2) } } }"
    )
    assert ability_tables_query("Casts", [6262], False) == (
        "query($code: String!, $fightIDs: [Int]) { reportData { report(code: $code) { a0: table(dataType: Casts, fightIDs: $fightIDs, abilityID: 6262) } } }"
    )


def test_api_errors_win_over_data():
    with pytest.raises(WarcraftLogsError, match="nope; really"):
        data_from_response({"errors": [{"message": "nope"}, {"message": "really"}], "data": 5})
    assert data_from_response({"data": 5}) == 5
    assert data_from_response({"errors": [], "data": 5}) == 5
    with pytest.raises(WarcraftLogsError):
        data_from_response({})


DEATHS_RESPONSE = """{"data":{"reportData":{"report":{"table":{"data":{"entries":[
    {"name":"Graveshrug","id":9,"guid":227378200,"type":"DeathKnight","icon":"DeathKnight-Blood","timestamp":700932,"fight":3,"damage":{"total":747747},"overkill":0},
    {"name":"Rè","id":16,"guid":265890028,"type":"Rogue","icon":"Rogue-Assassination","timestamp":700996,"fight":3,"overkill":0},
    {"name":"Scrublock","id":1,"type":"Warlock","timestamp":900100,"fight":4},
    {"name":"Rè","id":16,"type":"Rogue","timestamp":900050,"fight":4}
]}}}}}}"""


def test_a_deaths_table_is_read_and_ranked():
    entries = deaths_from_data(data_from_response(json.loads(DEATHS_RESPONSE)))
    assert len(entries) == 4
    ranking = tally(entries)
    assert [(p.name, p.count) for p in ranking] == [("Rè", 2), ("Graveshrug", 1), ("Scrublock", 1)]
    assert ranking[0].class_name == "Rogue"
    first = tally(first_death_of_each_pull(entries))
    assert [(p.name, p.count) for p in first] == [("Graveshrug", 1), ("Rè", 1)]


def test_deaths_at_the_same_moment_keep_their_listed_order():
    first = first_death_of_each_pull([death("First", "", 1, 500.0), death("Second", "", 1, 500.0)])
    assert [entry.name for entry in first] == ["First"]


DAMAGE_RESPONSE = """{"data":{"reportData":{"report":{
    "a0":{"data":{"entries":[
        {"name":"Rè","id":16,"guid":1,"type":"Rogue","icon":"x","total":200325787,"totalReduced":1,"activeTime":0},
        {"name":"Scrublock","id":1,"type":"Warlock","total":193309151},
        {"name":"Kail","id":2,"type":"Evoker","total":193309151}]}},
    "a1":{"data":{"entries":[]}}
}}}}"""


def test_damage_tables_are_read_by_alias_and_added_across_chunks():
    tables = data_from_response(json.loads(DAMAGE_RESPONSE))["reportData"]["report"]
    assert len(tables) == 2
    players = {}
    add_totals(players, player_entries_from_table(tables[table_alias(0)]), set())
    add_totals(players, [PlayerEntry("Rè", "Rogue", 100.0), PlayerEntry("New", "", 7.0)], set())
    ability = build_ability_damage(tracked(1, "Coalesced Venom"), players)
    assert [(p.name, p.total) for p in ability.players] == [("Rè", 200_325_887), ("Kail", 193_309_151), ("Scrublock", 193_309_151), ("New", 7)]
    assert ability.total == 200_325_887 + 193_309_151 * 2 + 7
    nobody = build_ability_damage(tracked(2, "Nothing"), {})
    assert nobody.players == [] and nobody.total == 0
    assert player_entries_from_table(tables[table_alias(1)]) == []


def test_damage_is_shown_short_with_each_players_share():
    assert format_damage(0) == "0"
    assert format_damage(999) == "999"
    assert format_damage(12_345) == "12.3K"
    assert format_damage(200_325_787) == "200.3M"
    assert format_damage(3_514_226_904) == "3.5B"
    rows = damage_rows(build_ability_damage(tracked(1, "Fire"), {"A": ("Rogue", 750), "B": ("DeathKnight", 250)}))
    assert (rows[0].name, rows[0].value) == ("A", "750 (75.0%)")
    assert (rows[1].name, rows[1].value) == ("B", "250 (25.0%)")
    assert damage_rows(build_ability_damage(tracked(1, "Fire"), {})) == []


def test_the_newest_report_from_the_uploader_is_picked():
    from datetime import UTC, datetime

    reports = [
        report_summary_from_json(entry)
        for entry in [
            {"code": "old", "startTime": 100, "owner": {"id": 7}},
            {"code": "other", "startTime": 900, "owner": {"id": 8}},
            {"code": "new", "startTime": 500, "owner": {"id": 7}},
            {"code": "nobody", "startTime": 950, "owner": None},
        ]
    ]
    newest = newest_by_uploader(reports, 7)
    assert newest is not None and newest.code == "new"
    other = newest_by_uploader(reports, 8)
    assert other is not None and other.code == "other"
    assert newest_by_uploader(reports, 9) is None and newest_by_uploader([], 7) is None

    def at(millis):
        return datetime.fromtimestamp(millis / 1000, UTC)

    assert newest.started_at_or_after(at(499)) and newest.started_at_or_after(at(500)) and not newest.started_at_or_after(at(501))


def test_the_overview_counts_pulls_and_kills_per_boss():
    overview = build_overview(
        report([("Zul'jan", False, False), ("Zul'jan", False, False), ("Zul'jan", True, False), ("Other", False, False), ("Zul'jan", False, True)]),
        [],
        [],
        0,
    )
    # The pull still being uploaded isn't counted.
    assert overview.encounters == [EncounterSummary("Zul'jan", 3, 1), EncounterSummary("Other", 1, 0)]
    assert overview.started_at == 1_791_506_660
    description = overview_description(overview)
    assert "**4** Mythic pulls: 1 kills, 3 wipes" in description
    assert "• Zul'jan — 3 pulls, 1 kills" in description


def test_a_report_without_mythic_pulls_says_so():
    assert "No Mythic boss pulls" in overview_description(build_overview(report([]), [], [], 0))


def test_a_long_encounter_list_fits_in_a_description():
    fights = [(f"Boss number {n} with a rather long name", False, False) for n in range(400)]
    description = overview_description(build_overview(report(fights), [], [], 0))
    assert len(description) <= MAX_DESCRIPTION_CHARS and "more." in description


def test_a_boss_filter_picks_the_pulls_of_matching_bosses():
    night = report([("The Coiled Altar", False, False), ("Other Boss", False, False), ("The Coiled Altar", True, False), ("The Coiled Altar", False, True)])
    assert len(select_fights(night.fights, None)) == 3
    assert len(select_fights(night.fights, "  ")) == 3
    assert [fight.id for fight in select_fights(night.fights, "coiled")] == [1, 3]
    with pytest.raises(WarcraftLogsError) as problem:
        select_fights(night.fights, "nope")
    assert "`nope`" in str(problem.value) and "Other Boss, The Coiled Altar." in str(problem.value)
    with pytest.raises(WarcraftLogsError, match="no Mythic boss pulls"):
        select_fights([], "nope")


def test_rankings_are_cut_to_the_lines_and_characters_allowed():
    players = [player(f"Player{n}", 31 - n) for n in range(1, 31)]
    text = ranked_list(death_rows(players), 3, MAX_FIELD_CHARS)
    assert len(text.splitlines()) == 4
    assert text.startswith("1. **Player1** (Rogue) — 30\n2. **Player2**")
    assert text.endswith("…and 27 more.")
    text = ranked_list(death_rows(players[:2]), 10, MAX_FIELD_CHARS)
    assert len(text.splitlines()) == 2 and "more." not in text
    wide = [player("x" * (60 + n), 1) for n in range(1, 41)]
    text = ranked_list(death_rows(wide), 40, MAX_FIELD_CHARS)
    assert len(text) <= MAX_FIELD_CHARS and text.endswith(" more.")


def crowded_ability(name, players):
    return build_ability_damage(TrackedAbility(1, name), {f"A rather long player name {n}": ("DemonHunter", 1_000_000_000 - n) for n in range(players)})


def test_an_overview_with_many_tracked_abilities_stays_within_discords_size_limit():
    fights = [(f"Boss number {n} with a rather long name", False, False) for n in range(12)]
    deaths = [death(f"A rather long player name {n}", "DemonHunter", n, float(n)) for n in range(60)]
    damage = [crowded_ability("x" * (90 + n), 30) for n in range(10)]
    embed = overview_embed(build_overview(report(fights), deaths, damage, 3))
    assert embed_text_size(embed) <= MAX_EMBED_CHARS
    shown = len(embed["fields"]) - 2
    assert 0 < shown < 10
    assert f"{10 - shown} more tracked abilities" in embed["footer"]["text"]


def test_an_overview_shows_every_tracked_ability_when_there_is_room():
    damage = [crowded_ability("Coalesced Venom", 20), build_ability_damage(tracked(2, "Venom Rupture"), {})]
    embed = overview_embed(build_overview(report([("Zul'jan", False, False)]), [death("Rè", "Rogue", 1, 1.0)], damage, 0))
    fields = embed["fields"]
    assert len(fields) == 4
    assert fields[2]["name"] == "Coalesced Venom — damage taken"
    field = fields[2]["value"]
    assert field.startswith("Total: **") and field.splitlines()[0].endswith("over 1 pull")
    assert len(field.splitlines()) == 1 + DAMAGE_SHOWN_IN_OVERVIEW + 1
    assert fields[3]["value"].startswith("Nobody took damage from this.")
    assert "didn't fit" not in embed["footer"]["text"]


def test_a_damage_report_names_the_boss_and_lists_the_players():
    night = report([("The Coiled Altar", False, False), ("The Coiled Altar", True, False)])
    fights = select_fights(night.fights, "coiled")
    ability = build_ability_damage(tracked(1, "Coalesced Venom"), {f"Player{n:06}": ("Rogue", 1_000_000_000 - n) for n in range(40)})
    damage = build_damage_report(night, fights, ability, "coiled", 0)
    assert damage.scope == "The Coiled Altar" and damage.pulls == 2
    embed = damage_embed(damage)
    assert embed["title"] == "Coalesced Venom — damage taken"
    assert "**The Coiled Altar**, 2 pulls" in embed["description"] and "Total damage taken: **" in embed["description"]
    assert len(embed["fields"][0]["value"].splitlines()) == DAMAGE_SHOWN + 1

    long = damage_embed(build_damage_report(night, fights, crowded_ability("Coalesced Venom", 40), None, 0))
    assert len(long["fields"][0]["value"]) <= MAX_FIELD_CHARS

    everything = build_damage_report(night, fights, build_ability_damage(tracked(1, "Fire"), {}), None, 0)
    assert everything.scope == "All Mythic bosses"
    nobody = damage_embed(everything)["fields"][0]["value"]
    assert nobody.startswith("Nobody took damage from this in those pulls.") and "check the ability ID" in nobody
    empty = damage_embed(build_damage_report(night, [], build_ability_damage(tracked(1, "Fire"), {}), None, 0))
    assert "No Mythic boss pulls" in empty["description"] and "fields" not in empty


def test_the_abilities_of_one_kind_are_added_together():
    night = report([("Zul'jan", False, False), ("Zul'jan", False, False)])
    registered = [
        ConsumableAbility(ConsumableKind.HEALTHSTONE, 6262),
        ConsumableAbility(ConsumableKind.HEALTHSTONE, 452930),
        ConsumableAbility(ConsumableKind.HEALTH_POTION, 1234768),
    ]
    totals = [{"Saven": ("Paladin", 24), "Scrublock": ("Warlock", 2)}, {"Scrublock": ("Warlock", 35)}, {"Aâyo": ("Hunter", 8)}]
    used = build_consumables_report(night, night.fights, None, registered, totals, 0)
    stones = used.uses[0]
    assert (stones.kind, stones.registered, stones.total) == (ConsumableKind.HEALTHSTONE, True, 61)
    assert [(p.name, p.total) for p in stones.players] == [("Scrublock", 37), ("Saven", 24)]
    potions = used.uses[1]
    assert (potions.kind, potions.registered, potions.total) == (ConsumableKind.HEALTH_POTION, True, 8)
    assert used.pulls == 2 and used.scope == "All Mythic bosses"


def test_a_kind_with_nothing_registered_or_nothing_used_says_so():
    night = report([("Zul'jan", False, False)])
    used = build_consumables_report(night, night.fights, None, [ConsumableAbility(ConsumableKind.HEALTHSTONE, 6262)], [{}], 0)
    fields = consumables_embed(used)["fields"]
    assert len(fields) == 2
    assert (fields[0]["name"], fields[0]["value"]) == ("Healthstones — 0 used", "None used.")
    assert fields[1]["name"] == "Health potions" and "/settings wcl consumable_add" in fields[1]["value"]
    empty = consumables_embed(build_consumables_report(night, [], None, [], [], 0))
    assert "No Mythic boss pulls" in empty["description"] and "fields" not in empty


def test_the_consumables_embed_lists_the_top_users_and_stays_within_limits():
    night = report([("Zul'jan", False, False), ("Zul'jan", False, False), ("Zul'jan", True, False)])
    players = {f"Player{n:06}": ("DemonHunter", 100 - n) for n in range(40)}
    registered = [ConsumableAbility(ConsumableKind.HEALTHSTONE, 1), ConsumableAbility(ConsumableKind.HEALTH_POTION, 2)]
    embed = consumables_embed(build_consumables_report(night, night.fights, "zul", registered, [players, dict(players)], 2))
    assert embed["title"] == "Consumables used"
    assert "**Zul'jan**, 3 pulls" in embed["description"]
    stones = embed["fields"][0]
    assert stones["name"] == f"Healthstones — {sum(100 - n for n in range(40))} used"
    assert len(stones["value"].splitlines()) == CONSUMABLE_PLAYERS_SHOWN + 1
    assert stones["value"].startswith("1. **Player000000** (Demon Hunter) — 100")
    assert embed_text_size(embed) <= MAX_EMBED_CHARS
    footer = embed["footer"]["text"]
    assert footer.startswith("Every use in every Mythic pull counts") and footer.endswith("2 ignored players are left out.")


def test_ignored_players_are_matched_ignoring_case_and_left_out_of_damage_and_totals():
    ignored = ignored_set(["TRITONIAN", "aâyo"])
    assert is_ignored(ignored, "Tritonian") and is_ignored(ignored, "Aâyo")
    assert not is_ignored(ignored, "Tritonia") and not is_ignored(set(), "Tritonian")
    players = {}
    add_totals(players, [PlayerEntry("Tritonian", "Rogue", 900.0), PlayerEntry("Aâyo", "Rogue", 50.0), PlayerEntry("Rè", "Rogue", 100.0)], ignored)
    ability = build_ability_damage(tracked(1, "Fire"), players)
    assert len(ability.players) == 1 and ability.total == 100
    assert damage_rows(ability)[0].value == "100 (100.0%)"


def test_names_match_ignoring_case_and_accents_case():
    assert same_name("Tritonian", "tritonian")
    assert same_name("Aâyo", "AÂYO")
    assert same_name("Rè", "rÈ")
    assert not same_name("Rè", "Re")
    assert not same_name("Tritonian", "Tritonia")


def test_the_footer_says_how_many_players_are_left_out():
    assert "left out" not in footer_text(0)
    assert footer_text(1).endswith(" 1 ignored player is left out.")
    assert footer_text(3).endswith(" 3 ignored players are left out.")
    assert overview_embed(build_overview(report([("Zul'jan", False, False)]), [], [], 2))["footer"]["text"].endswith("2 ignored players are left out.")
    night = report([("Zul'jan", False, False)])
    damage = build_damage_report(night, [night.fights[0]], build_ability_damage(tracked(1, "Fire"), {}), None, 1)
    assert damage_embed(damage)["footer"]["text"].endswith("1 ignored player is left out.")


def test_the_total_of_an_ability_is_the_sum_over_the_night():
    ability = build_ability_damage(tracked(1285017, "Axegrinder"), {"A": ("Rogue", 600_000_000), "B": ("Druid", 97_716_688)})
    name, value = damage_field(ability, DAMAGE_SHOWN_IN_OVERVIEW, 34)
    assert name == "Axegrinder — damage taken"
    assert value.splitlines()[0] == "Total: **697.7M** over 34 pulls"
    assert plural(1, "pull") == "1 pull" and plural(0, "pull") == "0 pulls"
    assert len(value) <= MAX_FIELD_CHARS


def test_classes_are_shown_with_spaces():
    assert display_class("DeathKnight") == "Death Knight"
    assert display_class("DemonHunter") == "Demon Hunter"
    assert display_class("Rogue") == "Rogue"


def test_long_titles_are_cut_with_an_ellipsis():
    assert truncate_with_ellipsis("short", 256) == "short"
    cut = truncate_with_ellipsis("ä" * 300, 256)
    assert len(cut) == 256 and cut.endswith("…")


def test_a_wipe_starts_when_three_players_die_within_two_seconds():
    assert wipe_moment([10_000.0, 40_000.0, 90_000.0, 90_700.0, 91_500.0]) == 90_000.0
    assert wipe_moment([5_000.0, 6_000.0, 7_000.0]) == 5_000.0
    assert wipe_moment([5_000.0, 6_000.0, 7_001.0]) is None
    assert wipe_moment([91_500.0, 90_000.0, 90_700.0]) == 90_000.0
    assert wipe_moment([1_000.0, 1_100.0]) is None
    assert wipe_moment([]) is None


def test_the_replay_starts_five_seconds_before_the_wipe_but_not_before_the_pull():
    fight = Fight(7, "Boss", False, False, 100_000.0, 200_000.0)
    wipe = build_last_wipe("ABC", fight, 3, [150_000.0, 150_500.0, 151_000.0])
    assert (wipe.position_ms, wipe.wipe_found, wipe.pull) == (45_000.0, True, 3)
    assert build_last_wipe("ABC", fight, 1, [102_000.0, 102_100.0, 102_200.0]).position_ms == 0.0
    none = build_last_wipe("ABC", fight, 1, [120_000.0])
    assert not none.wipe_found and none.position_ms == 95_000.0


def test_the_replay_link_has_the_fight_and_the_position_in_the_pull():
    fight = Fight(10, "Boss", False, False, 2_699_531.0, 2_845_683.0)
    wipe = build_last_wipe("YRwPxnJ4MKpWFyrg", fight, 1, [2_829_228.0, 2_829_500.0, 2_830_100.0])
    assert replay_url(wipe) == "https://www.warcraftlogs.com/reports/YRwPxnJ4MKpWFyrg?fight=10&view=replay&position=124697"


def test_an_ability_is_a_tracked_name_or_a_spell_id():
    tracked_abilities = [tracked(1282408, "Coalesced Venom"), tracked(1299838, "Venom Rupture")]
    assert resolve_ability(tracked_abilities, "  coalesced VENOM ") == tracked(1282408, "Coalesced Venom")
    assert resolve_ability(tracked_abilities, "1299838") == tracked(1299838, "Venom Rupture")
    assert resolve_ability(tracked_abilities, "5") == tracked(5, "Ability 5")
    assert resolve_ability([], "5") == tracked(5, "Ability 5")
    assert resolve_ability([tracked(9, "123")], "123") == tracked(9, "123")
    for bad in ["Nope", "0", "-4", ""]:
        assert resolve_ability(tracked_abilities, bad) is None


def test_the_report_asked_for_is_a_code_or_the_newest_from_the_uploader():
    source = WclSource(1, 2)
    with_source = GuildSettings(wcl_source=source)
    assert parse_report_target("https://www.warcraftlogs.com/reports/ABCdef123", GuildSettings()) == ReportCode("ABCdef123")
    assert parse_report_target(None, with_source) == NewestReport(source)
    assert parse_report_target("ABC", with_source) == ReportCode("ABC")
    with pytest.raises(ValueError, match="/settings wcl source"):
        parse_report_target(None, GuildSettings())
    with pytest.raises(ValueError, match="`not a code!`"):
        parse_report_target("not a code!", with_source)


def test_the_ids_of_one_kind_are_picked_out():
    registered = [
        ConsumableAbility(ConsumableKind.HEALTHSTONE, 6262),
        ConsumableAbility(ConsumableKind.HEALTH_POTION, 1234768),
        ConsumableAbility(ConsumableKind.HEALTHSTONE, 452930),
    ]
    assert ids_of_kind(registered, ConsumableKind.HEALTHSTONE) == [6262, 452930]
    assert ids_of_kind(registered, ConsumableKind.HEALTH_POTION) == [1234768]
    assert ids_of_kind([], ConsumableKind.HEALTHSTONE) == []
    assert ConsumableKind.from_column_value("potion") is None
    assert [ConsumableKind.from_column_value(kind.value) for kind in ConsumableKind] == list(ConsumableKind)
    assert (ConsumableKind.HEALTHSTONE.plural, ConsumableKind.HEALTH_POTION.plural) == ("Healthstones", "Health potions")
    assert consumable_field(build_consumables_report(report([]), [], None, [], [], 0).uses[0])[0] == "Healthstones"


class SlowApi:
    """A Warcraft Logs that hands out a token, then never answers in time."""

    def __init__(self):
        self.requests = []

    async def request(self, method, url, *, timeout, headers=None, json=None, form=None, basic_auth=None):
        self.requests.append((url, form, basic_auth, headers))
        if url.endswith("/oauth/token"):
            return Response(200, '{"access_token":"test","expires_in":3600}')
        raise RequestTimeout("slow")


def test_a_hung_warcraft_logs_is_given_up_on_with_a_clear_message():
    api = SlowApi()
    client = WarcraftLogsClient(api, "id", "secret")  # type: ignore[arg-type]
    with pytest.raises(WarcraftLogsError, match="^Warcraft Logs took too long to answer. Try again in a moment.$"):
        asyncio.run(client.fetch_report("ABC"))
    url, form, auth, _ = api.requests[0]
    assert form == {"grant_type": "client_credentials"} and auth == ("id", "secret")
    assert api.requests[1][3] == {"Authorization": "Bearer test"}


class ExpiredTokenApi:
    """Turns the first token down, then answers."""

    def __init__(self):
        self.tokens = 0
        self.queries = 0

    async def request(self, method, url, *, timeout, headers=None, json=None, form=None, basic_auth=None):
        if url.endswith("/oauth/token"):
            self.tokens += 1
            return Response(200, f'{{"access_token":"t{self.tokens}","expires_in":3600}}')
        self.queries += 1
        if headers == {"Authorization": "Bearer t1"}:
            return Response(401, "")
        return Response(200, '{"data":{"reportData":{"report":null}}}')


def test_a_token_that_is_turned_down_is_replaced_once():
    api = ExpiredTokenApi()
    client = WarcraftLogsClient(api, "id", "secret")  # type: ignore[arg-type]
    with pytest.raises(WarcraftLogsError, match="doesn't exist, or isn't public"):
        asyncio.run(client.fetch_report("ABC"))
    assert (api.tokens, api.queries) == (2, 2)
