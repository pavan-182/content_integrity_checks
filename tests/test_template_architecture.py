import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from content_integrity.models import Finding, ParsedRecord
from content_integrity.pipeline import _aggregate_findings, _family_rows, _pair_finding_rows
from content_integrity.reporting import PAIR_COLUMNS, write_csv
from content_integrity.template_clustering import (
    PairFinding,
    cluster_template_findings,
    merge_pair_findings,
)


def pair(
    left,
    right,
    confidence="high",
    match="entity_value_substitution",
    severity="high",
    sections=None,
):
    return PairFinding(
        pair_id=f"PAIR-{left}--{right}",
        record_id=left,
        matched_record_id=right,
        source_file=f"{left}.xml",
        matched_source_file=f"{right}.xml",
        title=f"Title {left}",
        matched_title=f"Title {right}",
        primary_match_type=match,
        supporting_match_types=[match],
        confidence=confidence,
        severity=severity,
        matched_sections=sections or ["Results"],
    )


def detector_finding(check_type, confidence="high", relationship_context="none"):
    return SimpleNamespace(
        record_id="A",
        matched_record_id="B",
        source_file="A.xml",
        matched_source_file="B.xml",
        title="Title A",
        matched_title="Title B",
        check_type=check_type,
        match_type=check_type,
        confidence=confidence,
        severity="high",
        matched_sections=["Results"],
        matched_sentence_count=2,
        shared_text_coverage=0.4,
        original_text_similarity=0.8,
        masked_skeleton_similarity=0.9,
        ngram_similarity=0.7,
        high_value_section_similarity=0.9,
        weighted_section_similarity=0.85,
        variable_substitutions="drug: A -> B",
        relationship_context=relationship_context,
        shared_skeleton_excerpt="shared wording",
        evidence="fixed evidence",
        review_status="candidate",
    )


class TemplateArchitectureTests(unittest.TestCase):
    def test_both_detectors_merge_once_and_promote_confidence(self):
        rows = merge_pair_findings(
            [detector_finding("exact_text_reuse", "high")],
            [detector_finding("entity_normalized_template", "high")],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].confidence, "very_high")
        self.assertEqual(
            rows[0].supporting_match_types,
            ["entity_normalized_template", "exact_text_reuse"],
        )
        self.assertEqual(rows[0].matched_source_file, "B.xml")

    def test_confidence_order_and_other_record_are_correct_from_both_sides(self):
        records = [
            ParsedRecord(source_file=f"{record_id}.xml", record_id=record_id)
            for record_id in ("A", "B", "C")
        ]
        rows = _aggregate_findings(
            records,
            [],
            [pair("A", "B", "high"), pair("A", "C", "very_high")],
            [],
        )
        summaries = {row["record_id"]: row for row in rows}
        self.assertEqual(summaries["A"]["template_confidence"], "very_high")
        self.assertEqual(summaries["A"]["strongest_matched_record_id"], "C")
        self.assertEqual(summaries["B"]["strongest_matched_record_id"], "A")

    def test_template_priority_ignores_other_detector_severity(self):
        record = ParsedRecord(source_file="A.xml", record_id="A")
        other = Finding(
            finding_id="F-1",
            record_id="A",
            source_file="A.xml",
            detector_type="llm_response_trace",
            category="trace",
            matched_text="trace",
            evidence_snippet="trace",
            section_or_field="Abstract",
            severity="high",
            confidence=1.0,
            rule_id="R-1",
        )
        summary = _aggregate_findings(
            [record],
            [other],
            [pair("A", "B", severity="low")],
            [],
        )[0]
        self.assertEqual(summary["template_review_priority"], "Low")
        self.assertEqual(summary["highest_severity"], "High")

    def test_two_member_pair_is_not_a_visible_family(self):
        records = [ParsedRecord(source_file=f"{i}.xml", record_id=i) for i in ("A", "B")]
        internal = cluster_template_findings([pair("A", "B")], records)
        summaries = _aggregate_findings(records, [], [pair("A", "B")], internal)
        self.assertEqual(_family_rows(internal), [])
        self.assertTrue(all(row["template_flag"] == "Yes" for row in summaries))
        self.assertTrue(all(row["template_cluster_flag"] == "No" for row in summaries))

    def test_three_strong_members_form_one_visible_family(self):
        records = [ParsedRecord(source_file=f"{i}.xml", record_id=i) for i in ("A", "B", "C")]
        pairs = [pair("A", "B"), pair("A", "C"), pair("B", "C")]
        internal = cluster_template_findings(pairs, records)
        families = _family_rows(internal)
        self.assertEqual(len(families), 1)
        self.assertEqual(families[0]["member_ids"], ["A", "B", "C"])
        self.assertEqual(families[0]["edge_density"], 1.0)
        self.assertEqual(families[0]["median_pair_confidence"], "high")
        self.assertEqual(families[0]["family_confidence"], "very_high")
        summaries = _aggregate_findings(records, [], pairs, internal)
        self.assertTrue(all(row["template_cluster_flag"] == "Yes" for row in summaries))

    def test_weak_edges_do_not_form_a_family(self):
        records = [ParsedRecord(source_file=f"{i}.xml", record_id=i) for i in ("A", "B", "C")]
        rows = cluster_template_findings(
            [pair("A", "B", "low"), pair("B", "C", "low")],
            records,
        )
        self.assertEqual(rows, [])

    def test_shared_trial_lowers_severity(self):
        finding = detector_finding("exact_text_reuse", relationship_context="shared trial ID: NCT12345678")
        self.assertEqual(merge_pair_findings([finding], [])[0].severity, "low")

    def test_pair_evidence_prefers_the_actual_matched_sentences(self):
        finding = detector_finding("exact_text_reuse")
        finding.record_matched_sentences = ["The exact matched sentence."]
        finding.matched_text_blocks = []
        self.assertEqual(
            merge_pair_findings([finding], [])[0].evidence_excerpt,
            "The exact matched sentence.",
        )

    def test_pair_csv_schema_is_stable_when_empty_or_populated(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = write_csv(Path(directory) / "empty.csv", [], PAIR_COLUMNS)
            populated = write_csv(
                Path(directory) / "populated.csv",
                _pair_finding_rows([pair("A", "B")]),
                PAIR_COLUMNS,
            )
            with empty.open(newline="", encoding="utf-8") as handle:
                empty_header = next(csv.reader(handle))
            with populated.open(newline="", encoding="utf-8") as handle:
                populated_header = next(csv.reader(handle))
        self.assertEqual(empty_header, PAIR_COLUMNS)
        self.assertEqual(populated_header, PAIR_COLUMNS)

    def test_dominant_pattern_uses_frequency_then_confidence(self):
        records = [ParsedRecord(source_file=f"{i}.xml", record_id=i) for i in ("A", "B", "C")]
        rows = cluster_template_findings(
            [
                pair("A", "B", match="z_pattern"),
                pair("A", "C", match="z_pattern"),
                pair("B", "C", "very_high", "a_pattern"),
            ],
            records,
        )
        self.assertEqual({row["template_pattern_type"] for row in rows}, {"z_pattern"})

    def test_clustering_is_deterministic(self):
        records = [ParsedRecord(source_file=f"{i}.xml", record_id=i) for i in ("A", "B", "C")]
        pairs = [pair("B", "C"), pair("A", "C"), pair("A", "B")]
        self.assertEqual(
            cluster_template_findings(pairs, records),
            cluster_template_findings(list(reversed(pairs)), records),
        )


if __name__ == "__main__":
    unittest.main()
