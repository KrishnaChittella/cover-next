import pytest

from metaspike.resolution.normalize import normalize_title


def key(title: str) -> str:
    return normalize_title(title).key


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("ELDEN RING", "Elden Ring"),  # case folding
        ("Straße", "STRASSE"),  # full Unicode case folding, not just lower()
        ("HELLDIVERS™ 2", "Helldivers 2"),  # trademark sign
        ("Overwatch®", "Overwatch"),  # registered sign
        ("BioShock©", "BioShock"),  # copyright sign
        ("Ｅｌｄｅｎ　Ｒｉｎｇ", "Elden Ring"),  # NFKC: full-width forms
        ("Command & Conquer", "Command and Conquer"),  # ampersand
        ("Baldur's Gate", "Baldurs Gate"),  # apostrophe dropped
        ("Baldur’s Gate", "Baldur's Gate"),  # typographic apostrophe
        ("Half-Life", "Half Life"),  # hyphen
        ("Vampire: The Masquerade - Bloodlines", "Vampire The Masquerade Bloodlines"),
        ("Watch_Dogs", "Watch Dogs"),  # underscore
        ("  Portal   2 ", "Portal 2"),  # whitespace
        ("Baldur's Gate III", "Baldur's Gate 3"),  # Roman numeral
        ("Final Fantasy VII", "Final Fantasy 7"),
        ("Grand Theft Auto V", "Grand Theft Auto 5"),
        ("Civilization VI", "Civilization 6"),
    ],
)
def test_differences_without_identity_meaning_are_removed(first: str, second: str) -> None:
    assert key(first) == key(second)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Portal", "Portal 2"),  # sequel numeral
        ("Mafia II", "Mafia III"),
        ("Doom (1993)", "Doom (2016)"),  # year
        ("The Last of Us", "The Last of Us Remastered"),  # rework marker
        ("Resident Evil 4", "Resident Evil 4 Remake"),
        ("Dragon Age II", "Dragon Age II: Ultimate Edition"),  # edition marker
        ("Brütal Legend", "Brutal Legend"),  # diacritics are not folded
        ("Star Wars Battlefront", "Star Wars Battlefront II"),
    ],
)
def test_tokens_that_carry_identity_are_never_deleted(first: str, second: str) -> None:
    assert key(first) != key(second)


def test_trademark_sign_is_removed_before_it_can_become_letters() -> None:
    # NFKC alone would turn the sign into the letters "TM" and corrupt the title.
    assert normalize_title("HELLDIVERS™ 2").tokens == ("helldivers", "2")


def test_roman_numerals_convert_only_where_they_are_numerals() -> None:
    assert normalize_title("Rocky V").tokens == ("rocky", "5")
    assert normalize_title("X-COM: UFO Defense").tokens[0] == "x"  # first word is a word
    assert normalize_title("I Am Alive").tokens[0] == "i"  # "I" is never a numeral
    assert normalize_title("Mega Man X").tokens == ("mega", "man", "10")
    assert normalize_title("Final Fantasy XVI").tokens[-1] == "16"
    assert normalize_title("Civilization").tokens == ("civilization",)


def test_identity_tokens_are_exposed_for_the_resolver() -> None:
    edition = normalize_title("Mass Effect 2 (2010) Edition")
    assert edition.numerals == ("2",) and edition.years == (2010,)
    assert edition.has_packaging_marker and edition.core_tokens == ("mass", "effect", "2", "2010")

    remaster = normalize_title("The Last of Us Remastered")
    assert remaster.rework_markers == {"remastered"} and not remaster.has_packaging_marker
    assert normalize_title("Halo: Combat Evolved").rework_markers == frozenset()


@pytest.mark.parametrize(
    ("title", "core"),
    [
        ("Jagged Alliance 2 Gold", "jagged alliance 2"),
        ("The Elder Scrolls IV: Oblivion Game of the Year Edition", "the elder scrolls 4 oblivion"),
        ("Dragon Age II: Ultimate Edition", "dragon age 2"),
        ("Grand Theft Auto IV: The Complete Edition", "grand theft auto 4"),
        ("Plants vs. Zombies GOTY Edition", "plants vs zombies"),
        ("Peggle Deluxe", "peggle"),
        ("Portal 2", "portal 2"),  # nothing to strip, numeral kept
    ],
)
def test_packaging_markers_are_set_aside_without_touching_the_title(title: str, core: str) -> None:
    assert normalize_title(title).core_key == core


def test_packaging_never_swallows_a_rework_marker_or_a_numeral() -> None:
    assert normalize_title("Doom 3: BFG Edition").core_tokens == ("doom", "3", "bfg")
    assert "remastered" in normalize_title("Dark Souls Remastered Deluxe Edition").core_tokens


def test_series_key_and_collections() -> None:
    assert normalize_title("BioShock 2").series_key == "bioshock"
    assert normalize_title("BioShock").series_key is None
    assert normalize_title("Left 4 Dead").series_key is None  # numeral is not final
    assert normalize_title("BioShock: The Collection").is_collection
    assert normalize_title("Mass Effect Trilogy").is_collection
    assert not normalize_title("Mass Effect 2").is_collection


def test_normalisation_is_deterministic_and_idempotent() -> None:
    title = "The Elder Scrolls® V: Skyrim – Special Edition"
    once = normalize_title(title)
    assert once == normalize_title(title)
    assert normalize_title(once.key).key == once.key
    assert once.original == title
