"""Derive surface-form query variants from one natural query without a model.

The evaluation needs many query surfaces per fact, but every extra model or human
judgment is another chance to change the meaning and silently corrupt the gold
labels. These transforms are pure text rewrites driven by an approved term map,
so a variant inherits its parent's labels with no new judgment and no tokens.

Each variant records which retrieval capability it probes and which normalization
would defeat it, so a failure points at a named gap instead of a vague
"paraphrase robustness" claim.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .io_utils import object_sha256, write_jsonl


DATA_DIR = Path(__file__).parent / "data"
DEFAULT_TERM_MAP_PATH = DATA_DIR / "term_map.v1.json"
APPROVED_STATUS = "approved"


class TermMapError(ValueError):
    """The term map is missing, malformed, or not approved for a scored run."""


@dataclass(frozen=True)
class TermMap:
    version: str
    domain: str
    status: str
    digest: str
    ko_to_en: dict[str, str]
    en_to_ko: dict[str, str]
    to_abbreviation: dict[str, str]
    excluded_generic_risk: tuple[str, ...] = ()

    @property
    def approved(self) -> bool:
        return self.status == APPROVED_STATUS


@dataclass(frozen=True)
class Variant:
    kind: str
    transform_id: str
    probe: str
    defeated_by: str
    text: str


def load_term_map(
    path: Path = DEFAULT_TERM_MAP_PATH,
    *,
    allow_unapproved: bool = False,
    include_generic_risk: bool = False,
) -> TermMap:
    """Load and validate the term map. Refuses unapproved maps by default.

    Entries flagged ``generic_risk`` are ordinary Korean words that also happen
    to be domain terms ("피해", "이점"). Substituting them outside the domain
    sense changes what the question asks, which would break the promise that a
    variant inherits its parent's labels, so they stay out of the substitution
    maps unless a caller opts in.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise TermMapError(f"term map not found: {path}") from error
    except json.JSONDecodeError as error:
        raise TermMapError(f"term map is not valid JSON: {path}") from error

    for key in ("version", "domain", "status", "entries"):
        if key not in payload:
            raise TermMapError(f"term map is missing '{key}': {path}")
    entries = payload["entries"]
    if not isinstance(entries, list) or not entries:
        raise TermMapError(f"term map has no entries: {path}")

    status = str(payload["status"])
    if status != APPROVED_STATUS and not allow_unapproved:
        raise TermMapError(
            f"term map status is '{status}', not '{APPROVED_STATUS}'. "
            "An operator must verify the rows before a scored run, or pass "
            "allow_unapproved=True for an unscored dry run."
        )

    ko_to_en: dict[str, str] = {}
    en_to_ko: dict[str, str] = {}
    to_abbreviation: dict[str, str] = {}
    excluded: list[str] = []
    seen_ko: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise TermMapError(f"entry {index} is not an object")
        korean = entry.get("ko")
        english = entry.get("en")
        if not isinstance(korean, str) or not korean.strip():
            raise TermMapError(f"entry {index} has no 'ko'")
        if not isinstance(english, str) or not english.strip():
            raise TermMapError(f"entry {index} has no 'en'")
        if korean.strip().casefold() == english.strip().casefold():
            raise TermMapError(f"entry {index} maps '{korean}' to itself")
        korean = korean.strip()
        english = english.strip()
        if entry.get("generic_risk") is True and not include_generic_risk:
            excluded.append(korean)
            continue

        surfaces = [korean]
        aliases = entry.get("ko_aliases", [])
        if not isinstance(aliases, list):
            raise TermMapError(f"entry {index} has a non-list 'ko_aliases'")
        surfaces.extend(str(alias).strip() for alias in aliases if str(alias).strip())
        for surface in surfaces:
            key = surface.casefold()
            if key in seen_ko:
                raise TermMapError(f"duplicate Korean surface '{surface}'")
            seen_ko.add(key)
            ko_to_en[key] = english
        en_to_ko.setdefault(english.casefold(), korean)

        abbreviation = entry.get("abbreviation")
        if abbreviation is not None:
            abbreviation = str(abbreviation).strip()
            if not abbreviation:
                raise TermMapError(f"entry {index} has an empty 'abbreviation'")
            for surface in (*surfaces, english):
                if surface.casefold() != abbreviation.casefold():
                    to_abbreviation[surface.casefold()] = abbreviation

    return TermMap(
        version=str(payload["version"]),
        domain=str(payload["domain"]),
        status=status,
        digest=object_sha256(payload),
        ko_to_en=ko_to_en,
        en_to_ko=en_to_ko,
        to_abbreviation=to_abbreviation,
        excluded_generic_risk=tuple(excluded),
    )


HANGUL = "가-힣"

# Korean is agglutinative, so a term is normally followed by a particle rather
# than a space. Regex \b does not hold between Hangul syllables, so a bare
# alternation rewrites the inside of longer words ("피해망상" -> "damage망상").
# Matching an optional particle from this closed list and re-attaching it keeps
# the substitution on real term boundaries.
JOSA: tuple[str, ...] = (
    "에서는", "에게서", "으로는", "이라도", "에서", "에게", "으로", "라도", "부터",
    "까지", "보다", "처럼", "이나", "한테", "이랑", "조차", "마저", "밖에", "든지",
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "나",
    "랑", "께",
)
_JOSA_SET = frozenset(JOSA)
_JOSA_ALTERNATION = "|".join(sorted(JOSA, key=len, reverse=True))

_LATIN_THEN_HANGUL = re.compile(rf"[A-Za-z0-9]([{HANGUL}]+)")
_HANGUL_THEN_LATIN = re.compile(rf"[{HANGUL}][A-Za-z]")


def _has_hangul(text: str) -> bool:
    return bool(re.search(f"[{HANGUL}]", text))


def _fuses_scripts(text: str) -> bool:
    """True when Latin and Hangul are glued inside one token.

    A rewritten term may legitimately carry a Korean particle
    ("armour class를"), but any other script fusion means the substitution cut
    through a word and the variant must not be emitted.
    """
    if _HANGUL_THEN_LATIN.search(text):
        return True
    return any(match.group(1) not in _JOSA_SET for match in _LATIN_THEN_HANGUL.finditer(text))


def _build_pattern(sources: Iterable[str]) -> re.Pattern[str] | None:
    korean = sorted((s for s in sources if _has_hangul(s)), key=len, reverse=True)
    latin = sorted((s for s in sources if not _has_hangul(s)), key=len, reverse=True)
    parts = []
    if korean:
        alternation = "|".join(re.escape(source) for source in korean)
        parts.append(
            rf"(?<![{HANGUL}])(?P<ko>{alternation})(?P<josa>{_JOSA_ALTERNATION})?(?![{HANGUL}])"
        )
    if latin:
        alternation = "|".join(re.escape(source) for source in latin)
        parts.append(rf"(?<![A-Za-z0-9])(?P<en>{alternation})(?![A-Za-z0-9])")
    if not parts:
        return None
    return re.compile("|".join(parts), re.IGNORECASE)


def _replace(text: str, mapping: Mapping[str, str]) -> str | None:
    """Rewrite whole terms only; None when nothing changed or the result is damaged."""
    if not mapping:
        return None
    pattern = _build_pattern(mapping)
    if pattern is None:
        return None
    hits = 0

    def _substitute(match: re.Match[str]) -> str:
        nonlocal hits
        groups = match.groupdict()
        source = groups.get("ko") or groups.get("en")
        if source is None:
            return match.group(0)
        replacement = mapping.get(source.casefold())
        if replacement is None:
            return match.group(0)
        hits += 1
        return replacement + (groups.get("josa") or "")

    rewritten = pattern.sub(_substitute, text)
    if not hits or rewritten == text:
        return None
    if _fuses_scripts(rewritten) and not _fuses_scripts(text):
        return None
    return rewritten


def _join_all_spaces(text: str, _: TermMap) -> str | None:
    joined = re.sub(r"\s+", "", text)
    return joined if joined != text else None


def _pad_spaces(text: str, _: TermMap) -> str | None:
    padded = re.sub(r" ", "  ", text)
    return padded if padded != text else None


def _korean_to_english(text: str, term_map: TermMap) -> str | None:
    return _replace(text, term_map.ko_to_en)


def _english_to_korean(text: str, term_map: TermMap) -> str | None:
    return _replace(text, term_map.en_to_ko)


def _abbreviate(text: str, term_map: TermMap) -> str | None:
    return _replace(text, term_map.to_abbreviation)


def _mixed_then_joined(text: str, term_map: TermMap) -> str | None:
    """The compound case: a Korean-English mix that also loses its spacing."""
    mixed = _korean_to_english(text, term_map)
    if mixed is None:
        return None
    return _join_all_spaces(mixed, term_map)


# (kind, transform_id, probe, defeated_by, function) in a fixed order so the
# derived query set is byte-identical across runs.
TRANSFORMS: tuple[tuple[str, str, str, str, Callable[[str, TermMap], str | None]], ...] = (
    ("spacing", "spacing.join_all", "spacing_insensitive_matching", "subword_or_ngram_indexing", _join_all_spaces),
    ("spacing", "spacing.pad", "whitespace_normalization", "whitespace_normalization", _pad_spaces),
    ("mixed", "mixed.ko_to_en", "cross_lingual_term_matching", "bilingual_term_map", _korean_to_english),
    ("mixed", "mixed.en_to_ko", "cross_lingual_term_matching", "bilingual_term_map", _english_to_korean),
    ("abbreviation", "abbreviation.apply", "abbreviation_matching", "abbreviation_expansion", _abbreviate),
    ("compound", "compound.ko_to_en_join", "mixed_language_without_spacing", "bilingual_term_map_and_subword_indexing", _mixed_then_joined),
)


def build_variants(natural: str, term_map: TermMap) -> list[Variant]:
    """Return every transform that actually changed the query, deduplicated."""
    source = natural.strip()
    if not source:
        return []
    seen = {source}
    variants: list[Variant] = []
    for kind, transform_id, probe, defeated_by, transform in TRANSFORMS:
        rewritten = transform(source, term_map)
        if rewritten is None or rewritten in seen:
            continue
        seen.add(rewritten)
        variants.append(Variant(kind, transform_id, probe, defeated_by, rewritten))
    return variants


# Field names the local runner and the public-fixture validator already use.
QUESTION_ID_FIELD = "question_id"
QUERY_FIELD = "query"
QUERY_KIND_FIELD = "query_kind"


def variant_rows(
    parent: Mapping[str, Any],
    term_map: TermMap,
    *,
    include_natural: bool = True,
) -> list[dict[str, Any]]:
    """Expand one natural fixture row into label-sharing evaluation rows.

    Every field of the parent row is carried over, so provenance, the reference
    answer, the predicates and ``fact_cluster_id`` stay attached to the derived
    queries. Only the identity, the query text and the variant metadata change.
    """
    question_id = str(parent[QUESTION_ID_FIELD])
    natural = str(parent[QUERY_FIELD]).strip()
    common = {
        **dict(parent),
        "derived_from": question_id,
        "term_map_version": term_map.version,
        "term_map_digest": term_map.digest,
        "term_map_status": term_map.status,
    }
    rows: list[dict[str, Any]] = []
    if include_natural:
        rows.append(
            {
                **common,
                QUESTION_ID_FIELD: question_id,
                QUERY_FIELD: natural,
                QUERY_KIND_FIELD: "natural",
                "transform_id": "natural.source",
                "probe": "baseline",
                "defeated_by": "",
            }
        )
    for variant in build_variants(natural, term_map):
        rows.append(
            {
                **common,
                QUESTION_ID_FIELD: f"{question_id}::{variant.transform_id}",
                QUERY_FIELD: variant.text,
                QUERY_KIND_FIELD: variant.kind,
                "transform_id": variant.transform_id,
                "probe": variant.probe,
                "defeated_by": variant.defeated_by,
            }
        )
    return rows


# The only fields the local runner accepts as QA input. Everything else in a
# fixture row (reference answer, predicates, provenance) is oracle material and
# the runner rejects it outright.
QA_INPUT_KEYS = (
    "question_id",
    "fact_cluster_id",
    "query",
    "query_kind",
    "fixture_run_fingerprint",
)


def qa_input_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project fixture rows down to the fields the local runner allows.

    ``variant_rows`` inherits the whole parent row so provenance and labels stay
    attached for review and reporting. That full row must never reach the QA
    model, so this projection is the only supported way to feed derived queries
    to the runner.
    """
    projected: list[dict[str, Any]] = []
    for row in rows:
        missing = [key for key in ("question_id", "fact_cluster_id", "query", "query_kind") if key not in row]
        if missing:
            raise ValueError(f"row is missing required QA fields: {', '.join(missing)}")
        projected.append({key: row[key] for key in QA_INPUT_KEYS if key in row})
    return projected


def glossary_overlap(term_map: TermMap, retriever_glossary: Iterable[str]) -> dict[str, Any]:
    """Report how much of the evaluation term map the retriever already knows.

    A variant built from a substitution the retriever also expands measures the
    glossary, not the retriever. Full overlap makes the mixed-language variants
    trivially solvable and must not be reported as robustness.
    """
    evaluation_terms = {term.casefold() for term in term_map.ko_to_en}
    known = {str(term).strip().casefold() for term in retriever_glossary if str(term).strip()}
    shared = sorted(evaluation_terms & known)
    total = len(evaluation_terms)
    ratio = len(shared) / total if total else 0.0
    return {
        "evaluation_terms": total,
        "shared_terms": len(shared),
        "overlap_ratio": round(ratio, 4),
        "trivially_solvable": total > 0 and len(shared) == total,
        "shared_sample": shared[:10],
    }


def expand_fixture(
    rows: Iterable[Mapping[str, Any]],
    term_map: TermMap,
    *,
    natural_only: bool = True,
) -> list[dict[str, Any]]:
    """Expand the natural rows of a fixture into the full derived query set.

    Rows the model already produced in another surface form are passed through
    untouched, so running this over an existing fixture never drops a query.
    """
    expanded: list[dict[str, Any]] = []
    for row in rows:
        if natural_only and str(row.get(QUERY_KIND_FIELD, "natural")) != "natural":
            expanded.append(dict(row))
            continue
        expanded.extend(variant_rows(row, term_map))
    return expanded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSONL of natural queries")
    parser.add_argument("--output", type=Path, required=True, help="JSONL of derived queries")
    parser.add_argument("--term-map", type=Path, default=DEFAULT_TERM_MAP_PATH)
    parser.add_argument(
        "--allow-unapproved",
        action="store_true",
        help="run against an unverified term map; results are not scoreable",
    )
    args = parser.parse_args(argv)

    term_map = load_term_map(args.term_map, allow_unapproved=args.allow_unapproved)
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    expanded = expand_fixture(rows, term_map)
    written = write_jsonl(args.output, expanded)
    summary = {
        "natural_queries": len(rows),
        "derived_queries": written,
        "multiplier": round(written / len(rows), 3) if rows else 0.0,
        "term_map_version": term_map.version,
        "term_map_status": term_map.status,
        "term_map_digest": term_map.digest,
        "scoreable": term_map.approved,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
