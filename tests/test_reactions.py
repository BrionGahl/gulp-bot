"""The bot's spontaneous reactions to messages, and the limits that hold over all of them."""

import random

from gulp_bot.service.reactions_service import MIN_GAP_SECS, REACTIONS, Reaction, ReactionsService, describe, reaction_rows, sets_off

GUILD, OTHER_GUILD = 1, 2
TRIT, KAI, SOMEONE = 10, 11, 12
HOUR = 60 * 60
TRIT_NAMES = ["tritonian", "Trit", None]
KAI_NAMES = ["kailanton", "Kai", "The Horsefly"]
SOMEONE_NAMES = ["someone", None, None]


class FixedRandom(random.Random):
    """Always draws the same number, and always picks the first response."""

    def __init__(self, value):
        super().__init__()
        self.value = value

    def random(self):
        return self.value

    def choice(self, seq):
        return seq[0]


def by_name(name):
    return next(reaction for reaction in REACTIONS if reaction.name == name)


def service(draw=0.0, gap=MIN_GAP_SECS):
    return ReactionsService(min_gap_secs=gap, rng=FixedRandom(draw))


def text_of(response):
    return None if response is None else response.text


# --- The reactions themselves ---


def test_the_reactions_are_the_ones_asked_for_in_order():
    assert [reaction.name for reaction in REACTIONS] == ["tritonian_welcome_back", "onlydam", "yuki", "nartana", "sono", "getcha", "horsefly"]
    trit = by_name("tritonian_welcome_back")
    assert (trit.author, trit.away_secs, trit.reply, trit.ignores_gap) == ("Tritonian", 8 * HOUR, False, True)
    assert not any(reaction.ignores_gap for reaction in REACTIONS if reaction is not trit)
    assert trit.responses == (
        "Welcome back, hairless one! Missed you :3",
        "Heeeeeeeello my favorite baldie",
        "Trit!!!! How was the pushing yesterday, furless one?",
        "Haaaaaaaaai Trit! if it ain't the chrome dome himself!",
        "*Holds the giggle* hii Trit :)",
        "Welcome back Trit! Your head is still the best mirror in Azeroth",
        "Trit!!!! How many depleted keys did we bring back today?",
        "*Shields eyes* oh it's just Trit, thought the sun came in",
        "Hiiii Trit :) still the most aerodynamic tank in the guild",
        "Heeeello Trit! Wind resistance: zero. Key timer: crushed",
        "*Polishes the welcome mat and Trit's head* hiii!",
        "Heeey Trit! Shampoo budget still at zero I see",
        "Trit! Every guild needs a north star, ours just happens to be your head",
        "*Adjusts sunglasses* sup Trit",
        "*Plays dramatic entrance music* the Trit has arrived",
        "Trit is here! Who needs a world buff when we have the dome",
        "Trit returns! The guild's MVP: Most Visible Pate",
        "*Puts on welding goggles* okay, hi Trit",
    )
    onlydam = by_name("onlydam")
    assert (onlydam.word, onlydam.cooldown_secs, onlydam.chance, onlydam.reply) == ("Onlydam", 24 * HOUR, 1.0, False)
    assert onlydam.responses == ("I miss him :(", "I miss the jaguar warrior")
    getcha = by_name("getcha")
    assert (getcha.word, getcha.chance, getcha.reply, getcha.responses) == ("Getcha", 0.25, True, ("Did you mean Bitcha?",))
    yuki = by_name("yuki")
    assert (yuki.word, yuki.chance, yuki.cooldown_secs, yuki.reply, yuki.responses) == ("Yuki", 1.0, 24 * HOUR, False, ("Bring him back!!!!",))
    nartana = by_name("nartana")
    assert (nartana.word, nartana.chance, nartana.cooldown_secs, nartana.reply, nartana.responses) == ("Nartana", 1.0, 24 * HOUR, False, ("*Giggles*",))
    sono = by_name("sono")
    assert (sono.word, sono.chance, sono.cooldown_secs, sono.reply, sono.responses) == ("Sono", 1.0, 24 * HOUR, True, ("the goat",))
    horsefly = by_name("horsefly")
    assert (horsefly.author, horsefly.username_only, horsefly.chance, horsefly.reply, horsefly.responses) == (
        "Kailanton",
        True,
        0.02,
        False,
        ("thats right horsefly",),
    )
    assert MIN_GAP_SECS == 10 * 60


def test_authors_are_known_by_any_of_their_names_and_words_are_found_anywhere_in_any_case():
    trit, onlydam, getcha = by_name("tritonian_welcome_back"), by_name("onlydam"), by_name("getcha")
    assert sets_off(trit, TRIT_NAMES, "") and sets_off(trit, ["x", None, " TRITONIAN "], "")
    assert not sets_off(trit, ["tritonian2", "Tritonian Jr", None], "")
    for content in ["Onlydam", "ONLYDAMAGE was the best", "remember onlydam's pulls?"]:
        assert sets_off(onlydam, SOMEONE_NAMES, content), content
    assert not sets_off(onlydam, SOMEONE_NAMES, "only dam")
    assert sets_off(getcha, SOMEONE_NAMES, "gonna GETCHA") and not sets_off(getcha, SOMEONE_NAMES, "gotcha")


def test_kailanton_is_known_by_his_discord_username_only():
    horsefly = by_name("horsefly")
    assert sets_off(horsefly, KAI_NAMES, "") and sets_off(horsefly, ["KAILANTON", None, None], "")
    # A global name or a server nickname of Kailanton isn't him.
    assert not sets_off(horsefly, ["kai_99", "Kailanton", None], "")
    assert not sets_off(horsefly, ["kai_99", None, "Kailanton"], "")
    # Tritonian is still known by any of his names.
    assert sets_off(by_name("tritonian_welcome_back"), ["trit_01", None, "Tritonian"], "")


def test_tritonian_is_greeted_on_his_first_message_and_after_eight_hours_away():
    reactions = service(gap=0)
    assert text_of(reactions.response_for(GUILD, TRIT, TRIT_NAMES, "hi", 0)) == "Welcome back, hairless one! Missed you :3"
    assert reactions.response_for(GUILD, TRIT, TRIT_NAMES, "hi", 8 * HOUR - 1) is None
    # Every message of his restarts the clock.
    assert reactions.response_for(GUILD, TRIT, TRIT_NAMES, "hi", 16 * HOUR - 2) is None
    assert reactions.response_for(GUILD, TRIT, TRIT_NAMES, "hi", 24 * HOUR) is not None
    # Each server has its own clock.
    assert reactions.response_for(OTHER_GUILD, TRIT, TRIT_NAMES, "hi", 24 * HOUR + 1) is not None
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "hi", 0) is None


def test_onlydam_is_answered_once_a_day_per_server():
    reactions = service(gap=0)
    response = reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "where is onlydam", 0)
    assert response is not None and response.text == "I miss him :(" and not response.reply
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "Onlydamage!!", 24 * HOUR - 1) is None
    assert reactions.response_for(OTHER_GUILD, SOMEONE, SOMEONE_NAMES, "onlydam", 1) is not None
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "onlydam", 24 * HOUR) is not None


def test_yuki_is_answered_once_a_day_per_server():
    reactions = service(gap=0)
    for n, content in enumerate(["Yuki", "where is YUKI?", "miss yuki"]):
        assert text_of(service(gap=0).response_for(GUILD, SOMEONE, SOMEONE_NAMES, content, n)) == "Bring him back!!!!", content
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "yu ki", 0) is None
    assert text_of(reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "yuki", 0)) == "Bring him back!!!!"
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "yuki", 24 * HOUR - 1) is None
    assert reactions.response_for(OTHER_GUILD, SOMEONE, SOMEONE_NAMES, "yuki", 1) is not None
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "yuki", 24 * HOUR) is not None


def test_nartana_is_giggled_at_once_a_day_per_server():
    reactions = service(gap=0)
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "nar tana", 0) is None
    assert text_of(reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "hey NARTANA", 0)) == "*Giggles*"
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "nartana", 24 * HOUR - 1) is None
    assert reactions.response_for(OTHER_GUILD, SOMEONE, SOMEONE_NAMES, "Nartana", 1) is not None
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "nartana", 24 * HOUR) is not None


def test_sono_is_replied_to_once_a_day_per_server():
    for content in ["Sono", "is SONO on tonight?", "sono!"]:
        response = service(gap=0).response_for(GUILD, SOMEONE, SOMEONE_NAMES, content, 0)
        assert response is not None and response.text == "the goat" and response.reply, content
    reactions = service(gap=0)
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "so no", 0) is None
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "sono", 0) is not None
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "sono", 24 * HOUR - 1) is None
    assert reactions.response_for(OTHER_GUILD, SOMEONE, SOMEONE_NAMES, "sono", 1) is not None
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "sono", 24 * HOUR) is not None


def test_getcha_and_horsefly_go_off_by_chance():
    assert service(draw=0.2499).response_for(GUILD, SOMEONE, SOMEONE_NAMES, "getcha", 0) == service().response_for(GUILD, SOMEONE, SOMEONE_NAMES, "getcha", 0)
    response = service(draw=0.2499).response_for(GUILD, SOMEONE, SOMEONE_NAMES, "getcha", 0)
    assert response is not None and response.text == "Did you mean Bitcha?" and response.reply
    assert service(draw=0.25).response_for(GUILD, SOMEONE, SOMEONE_NAMES, "getcha", 0) is None
    assert text_of(service(draw=0.0199).response_for(GUILD, KAI, KAI_NAMES, "hello", 0)) == "thats right horsefly"
    assert service(draw=0.02).response_for(GUILD, KAI, KAI_NAMES, "hello", 0) is None


def test_the_chances_come_out_about_right():
    reactions = ReactionsService(min_gap_secs=0, rng=random.Random(42))
    horsefly = sum(reactions.response_for(GUILD, KAI, KAI_NAMES, "hello", n) is not None for n in range(100_000))
    assert 1_800 < horsefly < 2_200
    getcha = sum(reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "getcha", 100_000 + n) is not None for n in range(10_000))
    assert 2_300 < getcha < 2_700


# --- The limits over all of them ---


def test_at_most_one_reaction_to_a_message_and_the_first_in_the_list_wins():
    reactions = service(gap=0)
    # Tritonian back, saying onlydam and getcha: only the greeting.
    response = reactions.response_for(GUILD, TRIT, TRIT_NAMES, "onlydam getcha", 0)
    assert response is not None and response.reaction == "tritonian_welcome_back"
    # Onlydam's cooldown didn't start: it hadn't gone off.
    assert text_of(reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "onlydam getcha", 1)) == "I miss him :("
    # Kailanton saying getcha: getcha comes first.
    assert reactions.response_for(GUILD, KAI, KAI_NAMES, "getcha", 2).reaction == "getcha"  # type: ignore[union-attr]


def test_at_most_one_reaction_every_ten_minutes_per_server():
    reactions = service()
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "getcha", 0) is not None
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "onlydam", MIN_GAP_SECS - 1) is None
    assert reactions.response_for(OTHER_GUILD, SOMEONE, SOMEONE_NAMES, "onlydam", MIN_GAP_SECS - 1) is not None
    # Held back by the limit, onlydam hadn't gone off in the first server, so it can now.
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "onlydam", MIN_GAP_SECS) is not None


def test_tritonian_is_greeted_within_the_ten_minutes_and_doesnt_start_them():
    reactions = service()
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "onlydam", 0) is not None
    # He comes back during the ten minutes of another reaction: greeted all the same.
    assert reactions.response_for(GUILD, TRIT, TRIT_NAMES, "hi", 60) is not None
    # And his greeting doesn't hold the next reaction back: the gap still runs from onlydam.
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "getcha", MIN_GAP_SECS - 1) is None
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "getcha", MIN_GAP_SECS) is not None
    # Greeted first, then a reaction right after: it isn't held back.
    other = service()
    assert other.response_for(GUILD, TRIT, TRIT_NAMES, "hi", 0) is not None
    assert other.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "getcha", 1) is not None
    # Still one reaction per message, and his greeting still waits for him to have been away.
    assert other.response_for(GUILD, TRIT, TRIT_NAMES, "hi", 2) is None


def test_a_message_that_sets_nothing_off_changes_nothing():
    reactions = service()
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "hello", 0) is None
    assert reactions.response_for(GUILD, SOMEONE, SOMEONE_NAMES, "getcha", 1) is not None


# --- COMMANDS.md ---


def test_each_reaction_is_described_for_commands_md():
    assert describe(by_name("getcha")) == ('A message with **"Getcha"** in it (any case)', 'A 25% chance of a reply: "Did you mean Bitcha?"', "Everyone")
    assert describe(by_name("tritonian_welcome_back"))[0] == "A message from **Tritonian** after 8 hours without one in the server"
    assert describe(by_name("onlydam"))[1].endswith("At most once every 24 hours per server")
    assert describe(Reaction("x", ("a",), author="Someone", word="hey"))[0] == 'A message from **Someone** with **"hey"** in it (any case)'
    rows = reaction_rows()
    assert rows.count("\n") == len(REACTIONS) + 1 and "at most one every 10 minutes per server" in rows
