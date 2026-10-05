"""Deterministic title normalisation.

Normalisation removes differences that carry no identity (case, punctuation,
trademark symbols, "&" versus "and", Roman versus Arabic numerals) and nothing
else. Tokens that DO carry identity are kept and exposed separately so that the
resolver can reason about them instead of losing them:

    sequel numerals      "Portal 2" is not "Portal"
    years                "Doom (2016)" is not "Doom (1993)"
    rework markers       a remaster or remake is a different work
    packaging markers    a Deluxe or Game of the Year edition is the same game, repackaged

Steps, in order:
  1. remove the trademark symbols (TM), (R), (C)  (before step 2, which would
     otherwise turn the TM sign into the letters "TM")
  2. Unicode NFKC, so compatibility forms such as full-width letters compare equal
  3. case-fold
  4. "&" becomes "and"
  5. apostrophes are dropped; every other punctuation mark and "_" becomes a space
  6. a standalone Roman numeral II..XX that is not the first word becomes its
     Arabic number (so "III" and "3" compare equal; "I" is left alone, being a word)
  7. whitespace is collapsed
"""

import functools
import re
import unicodedata
from dataclasses import dataclass

_TRADEMARKS = re.compile("[\u2122\u00ae\u00a9]")
_APOSTROPHES = re.compile("['\u2019\u2018`]")
_PUNCTUATION = re.compile(r"[^\w\s]|_")
_ROMAN = {
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
    "xi": "11",
    "xii": "12",
    "xiii": "13",
    "xiv": "14",
    "xv": "15",
    "xvi": "16",
    "xvii": "17",
    "xviii": "18",
    "xix": "19",
    "xx": "20",
}
_YEAR = re.compile(r"^(?:19|20)\d\d$")
_NUMERAL = re.compile(r"^\d{1,2}$")

# The same game in different wrapping. Multi-word markers are matched as phrases.
PACKAGING_PHRASES = (
    ("game", "of", "the", "year"),
    ("the", "complete"),
)
PACKAGING_WORDS = frozenset(
    {
        "edition",
        "deluxe",
        "ultimate",
        "gold",
        "goty",
        "definitive",
        "complete",
        "enhanced",
        "special",
        "collectors",
        "collector",
        "anniversary",
        "legendary",
        "premium",
        "digital",
        "standard",
    }
)
# A different work derived from the original. Never treated as packaging.
REWORK_WORDS = frozenset({"remaster", "remastered", "remake", "hd", "redux", "reloaded"})
# Several games sold as one product.
COLLECTION_WORDS = frozenset(
    {"collection", "bundle", "trilogy", "anthology", "compilation", "pack", "box", "duology"}
)


@dataclass(frozen=True)
class NormalizedTitle:
    original: str
    tokens: tuple[str, ...]

    @property
    def key(self) -> str:
        """The normalised title. Two titles with the same key are the same title."""
        return " ".join(self.tokens)

    @property
    def numerals(self) -> tuple[str, ...]:
        """Sequel numerals: one- or two-digit number tokens, in order."""
        return tuple(token for token in self.tokens if _NUMERAL.match(token))

    @property
    def years(self) -> tuple[int, ...]:
        return tuple(int(token) for token in self.tokens if _YEAR.match(token))

    @property
    def rework_markers(self) -> frozenset[str]:
        return frozenset(self.tokens) & REWORK_WORDS

    @property
    def has_packaging_marker(self) -> bool:
        return self.core_tokens != self.tokens

    @property
    def is_collection(self) -> bool:
        return bool(frozenset(self.tokens) & COLLECTION_WORDS)

    @property
    def core_tokens(self) -> tuple[str, ...]:
        """The title with packaging markers removed. Everything else is kept."""
        tokens = list(self.tokens)
        for phrase in PACKAGING_PHRASES:
            size = len(phrase)
            index = 0
            while index + size <= len(tokens):
                if tuple(tokens[index : index + size]) == phrase:
                    del tokens[index : index + size]
                else:
                    index += 1
        return tuple(token for token in tokens if token not in PACKAGING_WORDS)

    @property
    def core_key(self) -> str:
        return " ".join(self.core_tokens)

    @property
    def series_key(self) -> str | None:
        """The title without its final sequel numeral, or None if it does not end in one."""
        if len(self.tokens) >= 2 and _NUMERAL.match(self.tokens[-1]):
            return " ".join(self.tokens[:-1])
        return None


@functools.lru_cache(maxsize=65536)
def normalize_title(title: str) -> NormalizedTitle:
    text = _TRADEMARKS.sub("", title)
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.replace("&", " and ")
    text = _APOSTROPHES.sub("", text)
    text = _PUNCTUATION.sub(" ", text)
    words = text.split()
    tokens = tuple(
        _ROMAN.get(word, word) if position > 0 else word for position, word in enumerate(words)
    )
    return NormalizedTitle(original=title, tokens=tokens)
