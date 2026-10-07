"""A deterministic, stratified synthetic catalogue for comparing resolution strategies.

No real catalogue of this size exists yet: broad discovery is off and no source may be
called. So the large sample is SYNTHETIC. It is built from "families" of records, one
family per situation the resolver has a rule for, in fixed proportions, so that every
candidate rule, every veto and every decision kind is exercised many times and the
counts are the same on every machine. Realistic shapes are kept where they matter:
Steam records carry a title and almost nothing else, IGDB records carry companies,
dates, websites and external ids, and ids from the two sources overlap numerically.

It proves that two resolution strategies agree. It says nothing about how well either
resolves real data; the golden set does that.
"""

import random
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime

from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_records import (
    DatePrecision,
    ExternalId,
    ProductType,
    ReleaseDate,
    SourceRecord,
)

FETCHED = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)

_FIRST = [
    "Ashen",
    "Brass",
    "Cinder",
    "Crimson",
    "Dawn",
    "Ember",
    "Feral",
    "Gilded",
    "Hollow",
    "Iron",
    "Jade",
    "Lunar",
    "Mossy",
    "Neon",
    "Obsidian",
    "Pale",
    "Quiet",
    "Rusted",
    "Silent",
    "Tidal",
    "Umbral",
    "Verdant",
    "Wicked",
    "Amber",
    "Bitter",
    "Cobalt",
    "Dire",
    "Elder",
    "Frozen",
    "Grim",
    "Hidden",
    "Ivory",
    "Jagged",
    "Lost",
    "Molten",
    "Northern",
    "Onyx",
    "Primal",
    "Radiant",
    "Shattered",
]
_SECOND = [
    "Kingdom",
    "Frontier",
    "Odyssey",
    "Legion",
    "Harbor",
    "Citadel",
    "Protocol",
    "Requiem",
    "Vanguard",
    "Wilds",
    "Archive",
    "Bastion",
    "Crusade",
    "Dominion",
    "Eclipse",
    "Forge",
    "Garden",
    "Horizon",
    "Isles",
    "Junction",
    "Keep",
    "Labyrinth",
    "Meridian",
    "Nexus",
    "Outpost",
    "Paradox",
    "Quarry",
    "Reckoning",
    "Sanctum",
    "Tempest",
    "Uprising",
    "Vault",
    "Wasteland",
    "Chronicle",
    "Descent",
    "Empire",
    "Fable",
    "Gauntlet",
    "Hollows",
]
_THIRD = [
    "Tactics",
    "Racing",
    "Online",
    "Legends",
    "Arena",
    "Stories",
    "Heroes",
    "Rising",
    "Origins",
    "Reborn",
    "Unbound",
    "Zero",
    "Infinite",
    "Chronicles",
    "Wars",
    "Tales",
    "Hunters",
    "Dreams",
    "Echoes",
    "Shadows",
]
_STUDIOS = [f"{a} {b}" for a in ("Kestrel", "Halcyon", "Marrow", "Tinder", "Lodestar", "Wren")
            for b in ("Interactive", "Games", "Studios", "Works", "Digital")]  # fmt: skip
_PLATFORMS = ("PC (Microsoft Windows)", "PlayStation 5", "Xbox Series X|S", "Nintendo Switch")


class Sample:
    """Records in the order they were generated, each labelled with its stratum."""

    def __init__(self) -> None:
        self.records: list[SourceRecord] = []
        self.stratum: dict[SourceRecordKey, str] = {}

    def add(self, stratum: str, record: SourceRecord) -> SourceRecord:
        if record.key in self.stratum:
            raise ValueError(f"duplicate synthetic record {record.key}")
        self.records.append(record)
        self.stratum[record.key] = stratum
        return record

    def counts(self) -> dict[str, int]:
        return dict(sorted(Counter(self.stratum.values()).items()))


class _Family:
    """One base game's worth of invented facts, and builders for records about it."""

    def __init__(self, number: int, chooser: random.Random, sample: Sample) -> None:
        self.number = number
        self.chooser = chooser
        self.sample = sample
        first = _FIRST[number % len(_FIRST)]
        second = _SECOND[(number // len(_FIRST)) % len(_SECOND)]
        third = _THIRD[(number // (len(_FIRST) * len(_SECOND))) % len(_THIRD)]
        self.title = f"{first} {second} {third}"
        self.studio = _STUDIOS[number % len(_STUDIOS)]
        self.publisher = _STUDIOS[(number * 7 + 3) % len(_STUDIOS)]
        self.year = 1998 + number % 27
        # Ids from both sources come from one numeric range, so a Steam app and an
        # IGDB game regularly share a number, as they do in reality.
        self._next_igdb = 500_000 + number * 10
        self._next_steam = 500_000 + number * 10

    def igdb_id(self) -> str:
        self._next_igdb += 1
        return str(self._next_igdb)

    def steam_id(self) -> str:
        self._next_steam += 1
        return str(self._next_steam)

    def igdb(
        self,
        stratum: str,
        title: str | None = None,
        *,
        kind: ProductType = ProductType.MAIN_GAME,
        year: int | None = -1,
        studio: str | None = "",
        steam: str | None = None,
        url: str | None = None,
        parent: SourceRecord | None = None,
        version_parent: SourceRecord | None = None,
        alternatives: tuple[str, ...] = (),
    ) -> SourceRecord:
        release_year = self.year if year == -1 else year
        developer = self.studio if studio == "" else studio
        identifier = self.igdb_id()
        external = [ExternalId(source="giantbomb", value=f"3030-{identifier}")]
        if steam is not None:
            external.append(ExternalId(source="steam", value=steam))
        return self.sample.add(
            stratum,
            SourceRecord(
                key=SourceRecordKey(source="igdb", source_id=identifier),
                title=self.title if title is None else title,
                alternative_titles=alternatives,
                product_type=kind,
                source_category=kind.value,
                release_dates=(
                    ()
                    if release_year is None
                    else (
                        ReleaseDate(
                            starts_on=date(release_year, 1 + self.number % 12, 1),
                            precision=DatePrecision.DAY,
                        ),
                    )
                ),
                platforms=(_PLATFORMS[self.number % 4], _PLATFORMS[(self.number + 1) % 4]),
                developers=() if developer is None else (developer,),
                publishers=() if developer is None else (self.publisher,),
                franchises=(self.title.split()[1],) if self.number % 3 == 0 else (),
                external_ids=tuple(external),
                websites=(
                    f"https://example.invalid/games/{identifier}",
                    *(() if url is None else (url,)),
                ),
                parent=None if parent is None else parent.key,
                version_parent=None if version_parent is None else version_parent.key,
                fetched_at=FETCHED,
            ),
        )

    def steam(
        self,
        stratum: str,
        title: str | None = None,
        *,
        identifier: str | None = None,
        kind: ProductType = ProductType.UNKNOWN,
        year: int | None = None,
        studio: str | None = None,
    ) -> SourceRecord:
        return self.sample.add(
            stratum,
            SourceRecord(
                key=SourceRecordKey(
                    source="steam_catalogue",
                    source_id=self.steam_id() if identifier is None else identifier,
                ),
                title=self.title if title is None else title,
                product_type=kind,
                source_category="games" if kind is ProductType.UNKNOWN else kind.value,
                release_dates=(
                    ()
                    if year is None
                    else (ReleaseDate(starts_on=date(year, 6, 1), precision=DatePrecision.DAY),)
                ),
                developers=() if studio is None else (studio,),
                fetched_at=FETCHED,
            ),
        )


def _store(app: str, slug: str = "") -> str:
    return f"https://store.steampowered.com/app/{app}/{slug}"


# Each shape is one situation. Its docstring is what the resolver is expected to face.


def _linked_pair(f: _Family) -> None:
    """An IGDB game that asserts its Steam app, and that app: an id link (path A)."""
    app = f.steam_id()
    f.igdb("igdb_main_with_steam_id", steam=app)
    f.steam("steam_app_id_linked", identifier=app)


def _linked_other_title(f: _Family) -> None:
    """An id link to an app whose title is not compatible: never merged on the id alone."""
    app = f.steam_id()
    f.igdb("igdb_main_with_steam_id", steam=app)
    f.steam("steam_app_linked_title_mismatch", f"Unrelated {f.title.split()[1]} Simulator",
            identifier=app)  # fmt: skip


def _title_only(f: _Family) -> None:
    """An IGDB game and a Steam app with the same title and nothing else in common."""
    f.igdb("igdb_main")
    f.steam("steam_app_title_only")


def _corroborated(f: _Family) -> None:
    """Two records with the same title, year and developer and no id link (path B)."""
    f.igdb("igdb_main")
    f.steam("second_record_title_year_company", year=f.year, studio=f.studio)


def _timing_conflict(f: _Family) -> None:
    """The same title and developer, released a decade apart: a different release."""
    f.igdb("igdb_main")
    f.steam("second_record_incompatible_timing", year=f.year + 11, studio=f.studio)


def _editions(f: _Family) -> None:
    """Packaging: an edition with a version parent, one without, and a Steam edition."""
    base = f.igdb("igdb_main")
    f.igdb("edition_with_version_parent", f"{f.title} Deluxe Edition",
           kind=ProductType.EXPANDED_GAME, version_parent=base)  # fmt: skip
    f.igdb("edition_without_parent", f"{f.title}: Game of the Year Edition",
           kind=ProductType.EXPANDED_GAME)  # fmt: skip
    f.steam("steam_packaging_variant", f"{f.title} - Definitive Edition")


def _sequels(f: _Family) -> None:
    """Numbered sequels in Arabic and Roman numerals: series siblings, never the same game."""
    f.igdb("igdb_main")
    f.igdb("sequel", f"{f.title} 2", year=f.year + 3)
    f.igdb("sequel", f"{f.title} III", year=f.year + 6)
    f.steam("steam_sequel_title", f"{f.title} II")


def _reworks(f: _Family) -> None:
    """A remaster and a remake of a game, and a Steam title carrying a rework marker."""
    base = f.igdb("igdb_main")
    f.igdb("remaster_with_parent", f"{f.title} Remastered", kind=ProductType.REMASTER,
           year=f.year + 9, parent=base)  # fmt: skip
    f.igdb("remake_without_parent", f"{f.title} Remake", kind=ProductType.REMAKE, year=f.year + 14)
    f.steam("steam_rework_title", f"{f.title} HD")


def _related_products(f: _Family) -> None:
    """DLC, a bundle of one game, a collection, a soundtrack and a demo: never entities."""
    base = f.igdb("igdb_main")
    f.igdb("dlc", f"{f.title}: The Lost Chapter", kind=ProductType.DLC, parent=base)
    f.igdb("bundle_of_one", f"{f.title} Gold Bundle", kind=ProductType.BUNDLE, version_parent=base)
    f.igdb("collection", f"{f.title} Collection", kind=ProductType.BUNDLE, parent=base)
    f.steam("steam_dlc", f"{f.title} Soundtrack", kind=ProductType.DLC)
    f.igdb("mod", f"{f.title} Overhaul", kind=ProductType.MOD, parent=base)


def _unknown_only(f: _Family) -> None:
    """Steam apps nothing else mentions: undetermined products with no candidate."""
    f.steam("steam_app_unmatched")
    f.steam("steam_app_unmatched", f"{f.title} Playtest")


def _url_claims(f: _Family) -> None:
    """A store URL naming a Steam app: alone, and confirmed by an edition's structured id."""
    app = f.steam_id()
    base = f.igdb("igdb_main_with_store_url", url=_store(app, f.title.replace(" ", "_")))
    f.steam("steam_app_url_claimed", identifier=app)
    if f.number % 2 == 0:
        f.igdb("edition_confirming_url_claim", f"{f.title} Complete Edition",
               kind=ProductType.EXPANDED_GAME, steam=app, version_parent=base)  # fmt: skip


def _competing_claims(f: _Family) -> None:
    """Two different games both claim one Steam app: no match is unique."""
    app = f.steam_id()
    f.igdb("igdb_main_with_steam_id", steam=app)
    f.igdb("igdb_main_competing_claim", f"{f.title} Chronicles", steam=app, year=f.year + 1,
           studio=_STUDIOS[(f.number + 5) % len(_STUDIOS)])  # fmt: skip
    f.steam("steam_app_claimed_twice", identifier=app)


def _year_qualified(f: _Family) -> None:
    """A title qualified by its own release year: the same game, differently labelled."""
    f.igdb("igdb_main")
    f.steam("steam_year_qualified_title", f"{f.title} ({f.year})")
    f.steam("steam_other_year_qualified_title", f"{f.title} ({f.year + 12})")


def _collisions(f: _Family) -> None:
    """Two unrelated games that share a title, and a Steam app with that title."""
    f.igdb("igdb_main")
    f.igdb("igdb_same_title_other_game", year=f.year + 12,
           studio=_STUDIOS[(f.number + 11) % len(_STUDIOS)])  # fmt: skip
    f.steam("steam_app_ambiguous_title")


def _ports(f: _Family) -> None:
    """Ports: one that names its parent, one that does not."""
    base = f.igdb("igdb_main")
    f.igdb("port_with_version_parent", kind=ProductType.PORT, year=f.year + 2, version_parent=base)
    f.igdb("port_without_parent", f"{f.title} Mobile", kind=ProductType.PORT, year=f.year + 4)


def _alternative_titles(f: _Family) -> None:
    """A record known by another name, and a record that uses that other name."""
    other = f"{f.title.split()[0]}: {f.title.split()[1]}"
    f.igdb("igdb_main_with_alternative_title", alternatives=(other, f"{f.title} JP"))
    f.steam("steam_alternative_title", other)
    f.igdb("second_record_alternative_title_corroborated", other, steam=None)


def _punctuation(f: _Family) -> None:
    """The same title spelt with symbols, case and punctuation that carry no identity."""
    one, two, three = f.title.split()
    f.igdb("igdb_main", f"{one} & {two}: {three}™")
    f.steam("steam_normalised_spelling", f"{one.upper()} and {two} - {three}")
    f.steam("steam_fullwidth_spelling", f"{one} and {two} {three}".translate(_FULLWIDTH))


def _many_numbers(f: _Family) -> None:
    """Titles that are mostly numbers: years, numerals and packaging together."""
    f.igdb("igdb_numeric_title", f"{f.title} 2 {f.year}", year=f.year)
    f.steam("steam_numeric_title", f"{f.title} 2")
    f.steam("steam_numeric_title", f"{f.title} 2 {f.year} Gold Edition")
    f.igdb("igdb_numeric_title", f"{f.title} 4 8 15 16 23 42", year=None, studio=None)


def _crowded_title(f: _Family) -> None:
    """One title used by many unrelated games over the years, and an app with that title."""
    crowd = f"{_SECOND[f.number % 7]} {_THIRD[0]}"  # seven titles, shared by every such family
    for offset in range(3):
        f.igdb("igdb_crowded_title", crowd, year=1990 + (f.number + offset * 9) % 35,
               studio=_STUDIOS[(f.number + offset * 3) % len(_STUDIOS)])  # fmt: skip
    f.steam("steam_app_crowded_title", crowd)


def _beyond_blocking(f: _Family) -> None:
    """A title with more numbers than the packaging key is enumerated for: assessed
    against every entity instead, which must give the same answer."""
    numbers = " ".join(str(n) for n in range(3, 15))
    f.igdb("igdb_title_beyond_blocking", f"{f.title} {numbers}", year=None, studio=None)
    f.steam("steam_title_beyond_blocking", f"{f.title} {numbers} Edition")


def _standalone(f: _Family) -> None:
    """Games nothing else mentions, with and without the facts that make them eligible."""
    f.igdb("igdb_main_alone")
    f.igdb("igdb_expansion_standalone", f"{f.title}: Frozen Shores",
           kind=ProductType.STANDALONE_EXPANSION, year=f.year + 1)  # fmt: skip
    f.igdb("igdb_undated_unattributed", f"{f.title} Prototype", year=None, studio=None)


_FULLWIDTH = {
    ord(c): ord(c) + 0xFEE0 for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
}

SHAPES: tuple[Callable[[_Family], None], ...] = (
    _linked_pair,
    _linked_other_title,
    _title_only,
    _corroborated,
    _timing_conflict,
    _editions,
    _sequels,
    _reworks,
    _related_products,
    _unknown_only,
    _url_claims,
    _competing_claims,
    _year_qualified,
    _collisions,
    _ports,
    _alternative_titles,
    _punctuation,
    _many_numbers,
    _crowded_title,
    _beyond_blocking,
    _standalone,
)


def stratified_sample(size: int, seed: int = 20261006) -> Sample:
    """At least `size` synthetic records, cut to exactly `size`, shuffled reproducibly.

    Families are generated shape by shape in rotation, so every shape has the same
    number of families (give or take one) whatever the size. The order is then
    shuffled, because nothing guarantees a real source lists a game before its DLC.
    """
    chooser = random.Random(seed)
    sample = Sample()
    number = 0
    while len(sample.records) < size:
        SHAPES[number % len(SHAPES)](_Family(number, chooser, sample))
        number += 1
    kept = sample.records[:size]
    chooser.shuffle(kept)
    trimmed = Sample()
    for record in kept:
        trimmed.add(sample.stratum[record.key], record)
    return trimmed
