from __future__ import annotations

import unittest

from content_integrity.entity_evaluation import evaluate_entities
from content_integrity.entity_extraction import extract_typed_entities, mask_text, validate_masking


class EntityExtractionTests(unittest.TestCase):
    def test_hybrid_entities_are_masked_with_valid_spans(self) -> None:
        text = "SNHG7 sponges miR-485 in MCF-7 cells; RT-qPCR measured overall survival at NCT12345678."
        masked, entities = mask_text(text, "Methods")
        self.assertIn("<LNCRNA>", masked)
        self.assertIn("<MIRNA>", masked)
        self.assertIn("<CELL_LINE>", masked)
        self.assertIn("<ASSAY>", masked)
        self.assertIn("<ENDPOINT>", masked)
        self.assertIn("<TRIAL_ID>", masked)
        self.assertEqual(validate_masking(text, masked, entities), [])

    def test_extended_types_and_exact_evaluation(self) -> None:
        text = (
            "HER2 breast cancer cells received immunotherapy; ClinicalTrials.gov lists "
            "NCT12345678 and PI3K/AKT pathway testing by ELISA in older adults."
        )
        types = {entity.entity_type for entity in extract_typed_entities(text)}
        self.assertTrue({"protein", "disease", "treatment_class", "registry", "trial_id", "pathway", "assay", "population"} <= types)
        report = evaluate_entities([(
            [{"start": 0, "end": 4, "entity_type": "protein"}],
            [{"start": 0, "end": 4, "entity_type": "protein"}, {"start": 5, "end": 10, "entity_type": "gene"}],
        )])
        self.assertEqual(report["overall"]["tp"], 1)
        self.assertEqual(report["overall"]["fn"], 1)


if __name__ == "__main__":
    unittest.main()
