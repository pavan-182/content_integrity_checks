from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from asco_integrity.models import ParsedRecord
from asco_integrity.xml_parser import parse_xml
from scripts.detect_llm_response_traces_llm import (
    RULES,
    SYSTEM_PROMPT,
    analyze_batch,
    analyze_records,
    estimate_request_tokens,
    pack_batches,
    validate_model_response,
    write_findings_csv,
)


ROOT = Path(__file__).resolve().parents[1]


def _record(record_id: str, text: str) -> ParsedRecord:
    return ParsedRecord(
        source_file=f"{record_id}.xml",
        record_id=record_id,
        title=f"Title {record_id}",
        abstract_text=text,
        abstract_sections=[{"section": "Abstract", "text": text}],
    )


class ScriptedClient:
    model_name = "test/gpt-oss-20b"

    def __init__(self, responder) -> None:
        self.responder = responder
        self.batch_sizes: list[int] = []

    def complete(self, *, system: str, user: str, max_tokens: int, temperature: float) -> str:
        payload = json.loads(user)
        self.batch_sizes.append(len(payload["records"]))
        return json.dumps(self.responder(payload))


class LLMTraceBatchScriptTests(unittest.TestCase):
    def test_prompt_contains_all_28_rules(self) -> None:
        self.assertEqual(len(RULES), 28)
        self.assertTrue(all(rule.rule_id in SYSTEM_PROMPT for rule in RULES))

    def test_batch_packing_respects_budget_and_record_cap(self) -> None:
        records = [_record(str(index), "Clinical text " * 80) for index in range(7)]
        budget = estimate_request_tokens(records[:2])
        batches = pack_batches(records, input_token_budget=budget, max_records_per_batch=2)

        self.assertTrue(all(len(batch) <= 2 for batch in batches))
        self.assertTrue(all(estimate_request_tokens(batch) <= budget for batch in batches))

    def test_response_writes_only_valid_positive_trace(self) -> None:
        records = [
            _record("A", "As an AI language model, I cannot verify this result."),
            _record("B", "Patients experienced durable clinical responses."),
        ]
        raw = json.dumps(
            {
                "results": [
                    {
                        "record_id": "A",
                        "traces": [{
                            "rule_id": "LLM-001",
                            "matched_text": "As an AI language model",
                            "section_or_field": "Abstract",
                            "confidence": 0.99,
                            "reason": "This is explicit assistant self-identification.",
                        }],
                    },
                    {"record_id": "B", "traces": []},
                ]
            }
        )

        rows = validate_model_response(raw, records, "B0001", "test-model")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rule_id"], "LLM-001")
        self.assertEqual(rows[0]["severity"], "high")
        self.assertIn("As an AI language model", rows[0]["evidence_snippet"])

    def test_unknown_rule_or_fabricated_text_is_rejected(self) -> None:
        record = _record("A", "Patients experienced durable clinical responses.")
        base = {
            "record_id": "A",
            "traces": [{
                "rule_id": "LLM-999",
                "matched_text": "Patients",
                "section_or_field": "Abstract",
                "confidence": 0.9,
                "reason": "Invalid rule.",
            }],
        }
        with self.assertRaises(ValueError):
            validate_model_response(json.dumps({"results": [base]}), [record], "B1", "model")

        base["traces"][0]["rule_id"] = "LLM-001"
        base["traces"][0]["matched_text"] = "fabricated assistant phrase"
        with self.assertRaises(ValueError):
            validate_model_response(json.dumps({"results": [base]}), [record], "B1", "model")

    def test_invalid_batch_is_retried_then_split(self) -> None:
        records = [_record("A", "Clean clinical sentence one."), _record("B", "Clean clinical sentence two.")]

        def responder(payload):
            if len(payload["records"]) > 1:
                return {"invalid": []}
            return {"results": [{"record_id": payload["records"][0]["record_id"], "traces": []}]}

        client = ScriptedClient(responder)
        rows = analyze_batch(client, records, batch_id="B0001")

        self.assertEqual(rows, [])
        self.assertEqual(client.batch_sizes, [2, 2, 1, 1])

    def test_header_only_csv_when_no_traces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_findings_csv(Path(directory) / "findings.csv", [])
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))

        self.assertEqual(len(rows), 1)
        self.assertIn("rule_id", rows[0])

    def test_six_labelled_llm_trace_records_are_recovered(self) -> None:
        paths = sorted((ROOT / "tests" / "fixtures" / "eval_corpus" / "positives").glob("syn9000[1-6]_llm_response_trace.xml"))
        records = [parse_xml(path) for path in paths]
        planted = [
            ("Here is the revised", "LLM-009"),
            ("As an AI language model", "LLM-001"),
            ("Certainly! Below is", "LLM-012"),
            ("Please let me know if you would like me to", "LLM-013"),
            ("### Key findings", "LLM-027"),
            ("I have rewritten the text to improve clarity and academic tone", "LLM-016"),
        ]

        def responder(payload):
            results = []
            for item in payload["records"]:
                traces = []
                for phrase, rule_id in planted:
                    for section in item["sections"]:
                        if phrase.lower() in section["text"].lower():
                            start = section["text"].lower().index(phrase.lower())
                            exact = section["text"][start:start + len(phrase)]
                            traces.append({
                                "rule_id": rule_id,
                                "matched_text": exact,
                                "section_or_field": section["section"],
                                "confidence": 0.95,
                                "reason": "The text contains explicit response residue.",
                            })
                            break
                results.append({"record_id": item["record_id"], "traces": traces})
            return {"results": results}

        rows, _ = analyze_records(ScriptedClient(responder), records)

        self.assertEqual(len(records), 6)
        self.assertEqual({row["record_id"] for row in rows}, {record.record_id for record in records})


if __name__ == "__main__":
    unittest.main()
