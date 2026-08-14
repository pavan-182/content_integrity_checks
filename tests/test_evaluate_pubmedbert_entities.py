import unittest

from scripts.evaluate_pubmedbert_entities import _merge


class PubMedBertEvaluationTests(unittest.TestCase):
    def test_merge_preserves_deterministic_spans(self) -> None:
        deterministic = [
            {"start": 0, "end": 4, "entity_type": "Gene"},
            {"start": 5, "end": 8, "entity_type": "Other numeric value"},
        ]
        model = [{"start": 0, "end": 4, "entity_type": "Gene_or_gene_product", "score": 0.9}]
        self.assertEqual(_merge(deterministic, model, 0.8), deterministic)
