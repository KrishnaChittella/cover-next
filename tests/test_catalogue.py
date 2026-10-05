import pytest

from metaspike.catalogue.seed import CatalogueError, build_catalogue
from metaspike.domain.games import (
    CanonicalGame,
    Catalogue,
    GameRelationship,
    LifecycleState,
    RelationshipType,
)
from metaspike.sources.wikimedia import SOURCE_ID
from tests.conftest import make_id


def game(number: int, slug: str, parent: int | None = None) -> CanonicalGame:
    relationships: tuple[GameRelationship, ...] = ()
    if parent is not None:
        relationships = (
            GameRelationship(
                parent_game_id=make_id(parent), relationship_type=RelationshipType.SEQUEL
            ),
        )
    return CanonicalGame(
        canonical_game_id=make_id(number),
        slug=slug,
        name=slug.title(),
        relationships=relationships,
        lifecycle_state=LifecycleState.RELEASED,
    )


def by_slug(catalogue: Catalogue, slug: str) -> CanonicalGame:
    return next(g for g in catalogue.games if g.slug == slug)


def test_seed_catalogue_is_valid_and_has_required_games(catalogue: Catalogue) -> None:
    slugs = {g.slug for g in catalogue.games}
    required = {
        "gta-v",
        "gta-online",
        "fivem",
        "gta-vi",
        "escape-from-tarkov",
        "star-citizen",
        "no-mans-sky",
    }
    assert required <= slugs
    assert 10 <= len(catalogue.games) <= 15


def test_gta_family_stays_three_distinct_entities(catalogue: Catalogue) -> None:
    gta_v, online, fivem = (by_slug(catalogue, s) for s in ("gta-v", "gta-online", "fivem"))
    assert len({gta_v.canonical_game_id, online.canonical_game_id, fivem.canonical_game_id}) == 3
    assert gta_v.relationships == ()

    assert [(r.parent_game_id, r.relationship_type) for r in fivem.relationships] == [
        (gta_v.canonical_game_id, RelationshipType.MOD_ECOSYSTEM)
    ]
    assert [(r.parent_game_id, r.relationship_type) for r in online.relationships] == [
        (gta_v.canonical_game_id, RelationshipType.ONLINE_COMPONENT)
    ]


def test_fivem_is_explicitly_unmapped_rather_than_sharing_a_page(catalogue: Catalogue) -> None:
    assert by_slug(catalogue, "fivem").identifier_for(SOURCE_ID) is None
    articles = [
        identifier.value
        for g in catalogue.games
        if (identifier := g.identifier_for(SOURCE_ID)) is not None
    ]
    assert len(articles) == len(set(articles)) == len(catalogue.games) - 1


def test_no_mans_sky_maps_to_an_article_title_distinct_from_its_slug(catalogue: Catalogue) -> None:
    identifier = by_slug(catalogue, "no-mans-sky").identifier_for(SOURCE_ID)
    assert identifier is not None and identifier.value == "No_Man's_Sky"


@pytest.mark.parametrize(
    ("games", "message"),
    [
        ((game(1, "a"), game(1, "b")), "duplicate canonical_game_id"),
        ((game(1, "a"), game(2, "a")), "duplicate slug"),
        ((game(1, "a", parent=9),), "unknown parent_game_id"),
        ((game(1, "a", parent=2), game(2, "b", parent=1)), "relationship cycle"),
    ],
)
def test_invalid_catalogues_are_rejected(games: tuple[CanonicalGame, ...], message: str) -> None:
    with pytest.raises(CatalogueError, match=message):
        build_catalogue(games)


def test_a_game_cannot_be_its_own_parent() -> None:
    with pytest.raises(ValueError, match="own parent"):
        game(1, "a", parent=1)


def test_identifiers_must_be_well_formed() -> None:
    with pytest.raises(ValueError):
        CanonicalGame(
            canonical_game_id="gta-v",
            slug="gta-v",
            name="GTA V",
            lifecycle_state=LifecycleState.RELEASED,
        )
    with pytest.raises(ValueError):
        CanonicalGame(
            canonical_game_id=make_id(1),
            slug="GTA V",
            name="GTA V",
            lifecycle_state=LifecycleState.RELEASED,
        )
