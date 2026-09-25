"""Change coupling counted from the commits a walk read, the way the docs define it.
No crapkit.

- docs/agent-json.md (`coupling --json`): `support` is shared commits and
  `confidence` is the max-direction ratio: the shared commits over the
  commits of either file, whichever ratio is larger.
- README.md (`coupling`): bulk commits never couple pairs. The bulk size is
  code-maat's: a change set of more than 30 files takes no part in pairing
  (code-maat --max-changeset-size, default 30). Such a commit still counts
  toward each file's own commits (ruling H7).
- docs/agent-json.md#the-coupling-cache: ranking drops any pair naming a file
  `git ls-files` no longer lists.
- The rank order is support times confidence, highest first, then the two
  paths. The docs name no order (a doc gap); this is the order crapkit prints,
  written down so that a change to it shows.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from itertools import combinations

from accuracy.kit import exact

BULK = 30


@dataclass(frozen=True)
class Pair:
    files: tuple[str, str]
    support: int
    confidence: Decimal | Fraction  # exact, then half-even to 4 places

    @property
    def rank(self):
        return (-self.support * self.confidence, self.files)


def counts(file_sets: list[frozenset]) -> tuple[Counter, Counter]:
    """(commits per file, shared commits per sorted pair) over the change sets."""
    files, pairs = Counter(), Counter()
    for changed in file_sets:
        files.update(changed)
        if len(changed) <= BULK:
            pairs.update(combinations(sorted(changed), 2))
    return files, pairs


def confidence(files: Counter, pair: tuple[str, str], support: int) -> Fraction:
    return max(Fraction(support, files[pair[0]]), Fraction(support, files[pair[1]]))


def _tracked_pairs(tallies: tuple[Counter, Counter], tracked: set[str]) -> list[Pair]:
    files, pairs = tallies
    return [Pair(pair, support, confidence(files, pair, support))
            for pair, support in pairs.items() if set(pair) <= tracked]


def ranked_from(tallies: tuple[Counter, Counter], tracked: set[str], min_support: int = 1,
                min_confidence: Fraction = Fraction(0)) -> list[Pair]:
    """Every pair of tracked files at or over both thresholds, from (commits per
    file, shared commits per pair), in rank order, its confidence rounded
    half-even to 4 places."""
    kept = [pair for pair in _tracked_pairs(tallies, tracked)
            if pair.support >= min_support and pair.confidence >= min_confidence]
    rounded = [Pair(pair.files, pair.support, exact.half_even(pair.confidence, 4))
               for pair in kept]
    return sorted(rounded, key=lambda pair: pair.rank)


def ranked(file_sets: list[frozenset], tracked: set[str], min_support: int = 1,
           min_confidence: Fraction = Fraction(0)) -> list[Pair]:
    """ranked_from over the counts this module takes from the change sets."""
    return ranked_from(counts(file_sets), tracked, min_support, min_confidence)


def change_sets(commits) -> list[frozenset]:
    """Each walked commit's paths as one change set; a merge's empty set pairs nothing."""
    return [frozenset(commit.paths) for commit in commits]
