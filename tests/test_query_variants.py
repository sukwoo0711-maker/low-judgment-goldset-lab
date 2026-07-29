from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from goldset_lab.query_variants import (
    DEFAULT_TERM_MAP_PATH,
    QA_INPUT_KEYS,
    TermMapError,
    _fuses_scripts,
    build_variants,
    expand_fixture,
    glossary_overlap,
    load_term_map,
    qa_input_rows,
    variant_rows,
)


BASE_ENTRIES = [
    {"ko": "내성 굴림", "ko_aliases": ["내성굴림"], "en": "saving throw", "abbreviation": "내성"},
    {"ko": "굴림", "en": "roll"},
    {"ko": "발더스 게이트 3", "en": "Baldur's Gate 3", "abbreviation": "BG3"},
]


def _parent(text, question_id="q1", cluster="fc1", **extra):
    return {"question_id": question_id, "query": text, "fact_cluster_id": cluster, **extra}


class QueryVariantTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _map(self, entries, *, status="approved", version="test"):
        path = self.tmp_path / "term_map.json"
        payload = {"version": version, "domain": "test", "status": status, "entries": entries}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _approved(self, entries=None):
        return load_term_map(self._map(entries or BASE_ENTRIES))

    def _by_transform(self, text, term_map=None):
        term_map = term_map or self._approved()
        return {v.transform_id: v.text for v in build_variants(text, term_map)}

    # term map validation ------------------------------------------------

    def test_shipped_term_map_is_unapproved_until_an_operator_verifies_it(self):
        with self.assertRaisesRegex(TermMapError, "draft_unverified"):
            load_term_map(DEFAULT_TERM_MAP_PATH)
        term_map = load_term_map(DEFAULT_TERM_MAP_PATH, allow_unapproved=True)
        self.assertFalse(term_map.approved)
        self.assertTrue(term_map.digest)

    def test_term_map_rejects_malformed_rows(self):
        with self.assertRaisesRegex(TermMapError, "no 'en'"):
            load_term_map(self._map([{"ko": "주문"}]))
        with self.assertRaisesRegex(TermMapError, "itself"):
            load_term_map(self._map([{"ko": "spell", "en": "spell"}]))
        with self.assertRaisesRegex(TermMapError, "duplicate"):
            load_term_map(self._map([{"ko": "주문", "en": "spell"}, {"ko": "주문", "en": "cast"}]))
        with self.assertRaisesRegex(TermMapError, "no entries"):
            load_term_map(self._map([]))

    def test_generic_words_are_excluded_unless_a_caller_opts_in(self):
        entries = [
            {"ko": "주문", "en": "spell", "generic_risk": True},
            {"ko": "방어도", "en": "armour class"},
        ]
        default = load_term_map(self._map(entries))
        self.assertNotIn("주문", default.ko_to_en)
        self.assertEqual(default.excluded_generic_risk, ("주문",))
        opted_in = load_term_map(self._map(entries), include_generic_risk=True)
        self.assertIn("주문", opted_in.ko_to_en)

    # transforms ---------------------------------------------------------

    def test_longest_term_wins_so_a_compound_term_is_not_split(self):
        variants = self._by_transform("내성 굴림 규칙")
        self.assertEqual(variants["mixed.ko_to_en"], "saving throw 규칙")
        self.assertNotIn("내성 roll", variants.values())

    def test_transforms_cover_the_requested_surface_forms(self):
        variants = self._by_transform("내성 굴림 규칙")
        self.assertEqual(variants["spacing.join_all"], "내성굴림규칙")
        self.assertEqual(variants["spacing.pad"], "내성  굴림  규칙")
        self.assertEqual(variants["abbreviation.apply"], "내성 규칙")
        self.assertEqual(variants["compound.ko_to_en_join"], "savingthrow규칙")

    def test_english_source_is_rewritten_back_into_korean(self):
        variants = self._by_transform("saving throw 조건")
        self.assertEqual(variants["mixed.en_to_ko"], "내성 굴림 조건")

    def test_an_english_term_inside_a_longer_word_is_left_alone(self):
        """'roll' must not rewrite the middle of 'scroll' or 'rolling'."""
        term_map = self._approved()
        for source in ("scroll of protection", "rolling average", "casting time"):
            texts = {v.text for v in build_variants(source, term_map)}
            self.assertFalse(any("굴림" in text for text in texts), source)

    def test_a_korean_term_inside_a_longer_word_is_left_alone(self):
        """Hangul has no regex word boundary, so '피해망상' must not become 'damage망상'."""
        term_map = self._approved([{"ko": "피해", "en": "damage"}])
        self.assertFalse(any("damage" in v.text for v in build_variants("피해망상 사건", term_map)))

    def test_a_particle_after_a_rewritten_term_is_preserved(self):
        term_map = self._approved([{"ko": "피해", "en": "damage"}])
        variants = self._by_transform("피해를 계산하는 방법", term_map)
        self.assertEqual(variants["mixed.ko_to_en"], "damage를 계산하는 방법")

    def test_script_fusion_is_detected(self):
        self.assertTrue(_fuses_scripts("sc굴림 of protection"))
        self.assertTrue(_fuses_scripts("시전ing time"))
        self.assertTrue(_fuses_scripts("damage망상"))
        self.assertFalse(_fuses_scripts("armour class를 계산"))
        self.assertFalse(_fuses_scripts("내성 굴림 규칙"))

    def test_variants_are_deterministic_and_never_repeat_the_source(self):
        term_map = self._approved()
        source = "발더스 게이트 3 내성 굴림"
        first = build_variants(source, term_map)
        second = build_variants(source, term_map)
        self.assertEqual([vars(item) for item in first], [vars(item) for item in second])
        texts = [item.text for item in first]
        self.assertEqual(len(texts), len(set(texts)))
        self.assertNotIn(source, texts)

    def test_a_query_with_nothing_to_change_yields_no_variants(self):
        self.assertEqual(build_variants("무관한질의", self._approved()), [])

    # rows and projection ------------------------------------------------

    def test_rows_use_runner_field_names_and_inherit_the_parent_row(self):
        term_map = self._approved()
        parent = _parent(
            "내성 굴림 규칙",
            reference_answer="정답",
            source_url="https://example.invalid/a",
            license_basis="CC BY-SA 4.0",
        )
        rows = variant_rows(parent, term_map)
        self.assertEqual(rows[0]["question_id"], "q1")
        self.assertEqual(rows[0]["query_kind"], "natural")
        self.assertTrue(all(row["query"] for row in rows))
        self.assertEqual({row["fact_cluster_id"] for row in rows}, {"fc1"})
        self.assertEqual({row["derived_from"] for row in rows}, {"q1"})
        self.assertEqual({row["reference_answer"] for row in rows}, {"정답"})
        self.assertEqual({row["license_basis"] for row in rows}, {"CC BY-SA 4.0"})
        self.assertEqual({row["term_map_digest"] for row in rows}, {term_map.digest})
        self.assertEqual(len({row["question_id"] for row in rows}), len(rows))
        self.assertTrue(all(row["probe"] for row in rows))

    def test_qa_projection_strips_every_oracle_field(self):
        """The runner rejects any key outside its allowlist, so the projection must match it."""
        rows = variant_rows(
            _parent("내성 굴림 규칙", query_kind="natural", reference_answer="정답", predicates=["p1"]),
            self._approved(),
        )
        projected = qa_input_rows(rows)
        self.assertEqual(len(projected), len(rows))
        for row in projected:
            self.assertLessEqual(set(row), set(QA_INPUT_KEYS))
            self.assertNotIn("reference_answer", row)
            self.assertNotIn("predicates", row)
            self.assertTrue(row["query"])
        with self.assertRaisesRegex(ValueError, "missing required QA fields"):
            qa_input_rows([{"question_id": "q1"}])

    def test_expand_fixture_multiplies_natural_rows_and_passes_others_through(self):
        term_map = self._approved()
        source = [
            _parent("내성 굴림 규칙", question_id="q1", cluster="fc1", query_kind="natural"),
            _parent("이미 만들어진 변형", question_id="q2", cluster="fc1", query_kind="paraphrase"),
        ]
        expanded = expand_fixture(source, term_map)
        self.assertGreater(len(expanded), len(source))
        kept = [row for row in expanded if row["question_id"] == "q2"]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["query_kind"], "paraphrase")

    def test_glossary_overlap_flags_a_trivially_solvable_variant_set(self):
        term_map = self._approved()
        partial = glossary_overlap(term_map, ["내성 굴림"])
        self.assertEqual(partial["shared_terms"], 1)
        self.assertFalse(partial["trivially_solvable"])
        full = glossary_overlap(term_map, list(term_map.ko_to_en))
        self.assertTrue(full["trivially_solvable"])
        self.assertEqual(full["overlap_ratio"], 1.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
