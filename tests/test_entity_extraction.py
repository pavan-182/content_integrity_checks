from __future__ import annotations

import unittest
from unittest.mock import patch

from content_integrity.entity_evaluation import evaluate_entities
from content_integrity.entity_extraction import (
    extract_typed_entities,
    mask_text,
    validate_masking,
)


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

    def test_new_cell_lines_and_contextual_biomarker_are_deterministic(self) -> None:
        entities = extract_typed_entities("A549 and H1975 cells expressed circPVT1 as a potentially valuable biomarker.")
        self.assertEqual(
            [(entity.text, entity.entity_type) for entity in entities],
            [("A549", "cell_line"), ("H1975", "cell_line"), ("circPVT1", "biomarker")],
        )

    @patch("content_integrity.entity_extraction._pubmedbert_pipeline")
    def test_pubmedbert_only_fills_allowed_non_overlapping_types(self, pipeline) -> None:
        pipeline.return_value.return_value = [
            {"entity_group": "Gene_or_gene_product", "score": 0.99, "start": 0, "end": 7},
            {"entity_group": "Gene_or_gene_product", "score": 0.95, "start": 16, "end": 20},
            {"entity_group": "Cell", "score": 0.99, "start": 21, "end": 26},
            {"entity_group": "Cancer", "score": 0.70, "start": 30, "end": 36},
        ]
        with patch.dict("os.environ", {"ASCO_PUBMEDBERT_MODEL": "test", "ASCO_PUBMEDBERT_MIN_SCORE": "0.8"}):
            entities = extract_typed_entities("miR-708 targets FOXP cells in cancer")
        self.assertEqual(
            [(entity.text, entity.entity_type, entity.extraction_method) for entity in entities],
            [("miR-708", "mirna", "hybrid_context"), ("FOXP", "gene", "pubmedbert")],
        )

    @patch("content_integrity.entity_extraction._pubmedbert_pipeline", side_effect=RuntimeError("model unavailable"))
    def test_configured_pubmedbert_failure_is_not_silent(self, pipeline) -> None:
        with patch.dict("os.environ", {"ASCO_PUBMEDBERT_MODEL": "test"}):
            with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                extract_typed_entities("FOXP expression")


if __name__ == "__main__":
    unittest.main()
