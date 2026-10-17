"""The curated synonym map of search_patterns (T14) and the query expansion that uses it.

`synonyms.yaml` next to this module maps everyday words to the words the Pattern template texts use
for the same thing (see its header for the rules and the provenance). `expand(query)` turns a query
into what search_patterns searches:

- `words`: the query's words, lower-cased and deduplicated, without stopwords (unless the query is
  nothing but stopwords), searched at full weight;
- `synonyms`: the map's words for them, searched at reduced weight, one level deep and capped at
  `MAX_SYNONYMS`; a synonym that is already a query word is not added again;
- `applied` and `ignored`: which word brought which synonyms and which stopwords were left out, so
  the Evidence can say exactly what was searched.

The expansion runs here rather than in a SereneDB synonym dictionary (`expand_solr_synonyms`) so it
is the same on every server version and visible in the Evidence; the stemming runs in SereneDB
(the `pattern_text` dictionary of sql/derive/20_patterns.sql). A word that is not a key is also
looked up without a regular English ending ('crashed', 'crashes' and 'crashing' find 'crash'), the
way the stemmer will treat it.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from types import MappingProxyType

import yaml

__all__ = [
    "MAX_SYNONYMS",
    "STOPWORDS",
    "SYNONYMS_FILE",
    "Expansion",
    "SynonymMapError",
    "expand",
    "load_synonyms",
    "parse_synonyms",
    "query_words",
]

SYNONYMS_FILE = "synonyms.yaml"
MAX_SYNONYMS = 40
"""Most synonym terms one query may add, so a long question cannot turn into a scan of every Pattern."""

# Function words and question words, left out of the query: with OR semantics each would match
# dozens of Patterns and say nothing about the problem.
# fmt: off
STOPWORDS = frozenset({
    "a", "about", "after", "all", "an", "and", "any", "are", "as", "at", "be", "been",
    "before", "being", "but", "by", "can", "could", "did", "do", "does", "during", "for", "from",
    "had", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "me",
    "my", "no", "not", "of", "on", "or", "our", "show", "so", "some", "than", "that",
    "the", "their", "them", "then", "there", "these", "they", "this", "those", "to", "was", "we",
    "were", "what", "when", "where", "which", "who", "why", "will", "with", "would", "you",
})
# fmt: on

_WORD_RE = re.compile(r"\w+(?:[.']\w+)*")
"""A word as the index's split_text sees one: dots and apostrophes between letters join, every
other punctuation mark splits ('node-card' is two words, 'java.io' one)."""
_ENTRY_RE = re.compile(r"[a-z0-9_]+")
_ENDINGS = (("ing", ""), ("ing", "e"), ("es", ""), ("ed", ""), ("ed", "e"), ("s", ""))
"""Regular English endings tried, in order, when a word is not a key: (ending, replacement)."""


class SynonymMapError(ValueError):
    """`synonyms.yaml` breaks the rules in its header."""


@dataclass(frozen=True)
class Expansion:
    """What one query searches: its words at full weight and the added synonyms at reduced weight."""

    words: tuple[str, ...]
    synonyms: tuple[str, ...] = ()
    applied: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    ignored: tuple[str, ...] = ()


def parse_synonyms(text: str) -> dict[str, tuple[str, ...]]:
    """The map in `text` (YAML: key -> list of words), checked against the rules of synonyms.yaml."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SynonymMapError(f"{SYNONYMS_FILE} is not valid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SynonymMapError(f"{SYNONYMS_FILE} must be a mapping of words to lists of words")
    found: dict[str, tuple[str, ...]] = {}
    for key, values in data.items():
        if not isinstance(key, str) or not _ENTRY_RE.fullmatch(key):
            raise SynonymMapError(f"key {key!r} is not a single lower-case word")
        if not isinstance(values, list) or not values:
            raise SynonymMapError(f"{key}: the synonyms must be a non-empty list of words")
        for value in values:
            if not isinstance(value, str) or not _ENTRY_RE.fullmatch(value):
                raise SynonymMapError(f"{key}: {value!r} is not a single lower-case word")
        if len(set(values)) != len(values):
            raise SynonymMapError(f"{key}: a synonym is listed twice")
        if key in values:
            raise SynonymMapError(f"{key}: a word must not list itself as its synonym")
        found[key] = tuple(values)
    return found


@functools.cache
def load_synonyms() -> Mapping[str, tuple[str, ...]]:
    """The committed map, read once per process."""
    text = resources.files(__package__).joinpath(SYNONYMS_FILE).read_text(encoding="utf-8")
    return MappingProxyType(parse_synonyms(text))


def query_words(query: str) -> list[str]:
    """The words of `query`, lower-cased and deduplicated, in order."""
    return list(dict.fromkeys(_WORD_RE.findall(query.lower())))


def _synonyms_of(word: str, synonyms: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    if word in synonyms:
        return synonyms[word]
    for ending, replacement in _ENDINGS:
        if word.endswith(ending) and len(word) > len(ending) + 2:
            base = word[: -len(ending)] + replacement
            if base in synonyms:
                return synonyms[base]
    return ()


def expand(query: str, synonyms: Mapping[str, tuple[str, ...]] | None = None) -> Expansion:
    """The words and synonyms search_patterns searches for `query` (see the module docstring)."""
    table = load_synonyms() if synonyms is None else synonyms
    every = query_words(query)
    kept = [word for word in every if word not in STOPWORDS] or every
    ignored = tuple(word for word in every if word not in kept)
    added: list[str] = []
    applied: dict[str, tuple[str, ...]] = {}
    for word in kept:
        fresh = [s for s in _synonyms_of(word, table) if s not in kept and s not in added]
        fresh = fresh[: MAX_SYNONYMS - len(added)]
        if fresh:
            applied[word] = tuple(fresh)
            added.extend(fresh)
    return Expansion(words=tuple(kept), synonyms=tuple(added), applied=applied, ignored=ignored)
