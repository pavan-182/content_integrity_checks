import unittest

from asco_integrity.models import ParsedRecord
from asco_integrity.template_clustering import PairFinding, cluster_template_findings


def pair(left, right, confidence="high", match="entity_value_substitution"):
    return PairFinding(
        pair_id=f"PAIR-{left}--{right}",
        record_id=left,
        matched_record_id=right,
        primary_match_type=match,
        supporting_match_types=[match],
        confidence=confidence,
        severity="high",
        matched_sections=["results"],
    )


class TemplateArchitectureTests(unittest.TestCase):
    def test_two_member_group_is_not_a_visible_family(self):
        records = [ParsedRecord(source_file=f"{i}.xml", record_id=i) for i in ("A", "B")]
        rows = cluster_template_findings([pair("A", "B")], records)
        self.assertEqual({row["cluster_size"] for row in rows}, {2})

    def test_three_strong_edges_form_one_family(self):
        records = [ParsedRecord(source_file=f"{i}.xml", record_id=i) for i in ("A", "B", "C")]
        rows = cluster_template_findings([pair("A", "B"), pair("A", "C"), pair("B", "C")], records)
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["cluster_size"] for row in rows}, {3})
        self.assertEqual({row["edge_density"] for row in rows}, {1.0})

    def test_weak_edge_does_not_create_a_family(self):
        records = [ParsedRecord(source_file=f"{i}.xml", record_id=i) for i in ("A", "B", "C")]
        rows = cluster_template_findings([pair("A", "B", "low"), pair("B", "C", "low")], records)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
