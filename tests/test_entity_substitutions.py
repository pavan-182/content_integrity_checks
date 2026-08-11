from __future__ import annotations

import unittest

from content_integrity.entity_substitutions import collect_entity_substitutions
from content_integrity.models import ParsedRecord


class EntitySubstitutionTests(unittest.TestCase):
    def test_reports_typed_shared_and_changed_values_with_sentences(self) -> None:
        records = [
            ParsedRecord(source_file="x.xml", record_id="A", title="EGFR in lung cancer", abstract_text="EGFR was measured by PCR. Overall survival improved."),
            ParsedRecord(source_file="x.xml", record_id="B", title="KRAS in breast cancer", abstract_text="KRAS was measured by PCR. Progression-free survival improved."),
        ]
        row = collect_entity_substitutions(records)[0]
        self.assertIn("gene: EGFR -> KRAS", row.likely_substitutions)
        self.assertIn("assay: PCR", row.shared_entities)
        self.assertIn("EGFR was measured", row.left_supporting_sentences)


if __name__ == "__main__":
    unittest.main()
