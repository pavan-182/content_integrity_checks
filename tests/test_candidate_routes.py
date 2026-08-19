from __future__ import annotations

import time
import unittest

from content_integrity.candidate_routes import generate_candidate_pairs
from content_integrity.models import ParsedRecord
from content_integrity.template_matching_common import _candidate_pairs


class CandidateRouteTests(unittest.TestCase):
    def test_title_and_exact_masked_body_routes_are_retained(self) -> None:
        records = [
            ParsedRecord(source_file="x.xml", record_id="A", title="Outcomes of EGFR in lung cancer", abstract_text="50 patients received abemaciclib."),
            ParsedRecord(source_file="x.xml", record_id="B", title="Outcomes of KRAS in breast cancer", abstract_text="60 patients received ribociclib."),
        ]
        pairs = generate_candidate_pairs(records)
        self.assertEqual(len(pairs), 1)
        self.assertIn("title_template", pairs[0].routes)
        self.assertIn("exact_masked_body", pairs[0].routes)

    def test_pathological_approximate_buckets_are_skipped(self) -> None:
        common = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"
        records = [
            ParsedRecord("x.xml", record_id=f"R{index}", abstract_text=f"{common} unique{index}")
            for index in range(51)
        ]
        texts = {record.record_id: record.abstract_text for record in records}
        self.assertEqual(_candidate_pairs(records, texts, texts), set())

    def test_exact_matches_are_not_lost_when_bucket_is_large(self) -> None:
        # Past the MAX_APPROXIMATE_BUCKET cap (50), an exact-duplicate block no longer emits
        # the full O(k^2) clique - it stars every member off the smallest id instead, which
        # still guarantees every record is retrieved (unlike the approximate-bucket case in
        # test_pathological_approximate_buckets_are_skipped, which drops the whole bucket).
        records = [
            ParsedRecord("x.xml", record_id=f"R{index}", abstract_text="identical exact abstract")
            for index in range(51)
        ]
        pairs = generate_candidate_pairs(records)
        covered = {record_id for pair in pairs for record_id in (pair.left_record_id, pair.right_record_id)}
        self.assertEqual(covered, {record.record_id for record in records})
        self.assertEqual(len(pairs), 50)
        self.assertTrue(all("exact_original_body" in pair.routes for pair in pairs))

    def test_large_exact_duplicate_cluster_does_not_scale_combinatorially(self) -> None:
        # Simulates a boilerplate abstract (e.g. "trial in progress") reused across several
        # years' worth of submissions - a single exact-match bucket far past the 6,000/year
        # scale the design doc assumed. Pre-fix, this bucket alone produced a full O(k^2)
        # clique (~4.5M pairs for k=3000); post-fix it stars off one anchor (k-1 pairs).
        count = 3000
        records = [
            ParsedRecord("x.xml", record_id=f"R{index}", abstract_text="trial in progress, no results yet")
            for index in range(count)
        ]
        texts = {record.record_id: record.abstract_text for record in records}
        start = time.time()
        pairs = _candidate_pairs(records, texts, texts)
        elapsed = time.time() - start
        self.assertLessEqual(len(pairs), count)
        self.assertLess(elapsed, 3.0)


if __name__ == "__main__":
    unittest.main()
