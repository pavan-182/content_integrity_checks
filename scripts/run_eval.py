from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from asco_integrity.pipeline import run_default_pipeline
from asco_integrity.template_detection import cluster_templates


CORPUS = ROOT / "tests" / "fixtures" / "eval_corpus"
THRESHOLDS = (0.75, 0.80, 0.85, 0.88, 0.90, 0.93)


def _expected(labels: dict[str, dict[str, object]], detector_type: str, optional: bool = False) -> set[str]:
    return {
        record_id
        for record_id, label in labels.items()
        if detector_type in label.get("optional_finding_types" if optional else "expected_finding_types", [])
    }


def _predicted_findings(result) -> dict[str, set[str]]:
    predicted: dict[str, set[str]] = defaultdict(set)
    for finding in result.findings:
        predicted[finding.detector_type].add(finding.record_id)
    predicted["template_cluster"] = {
        row.record_id for row in result.template_rows if row.cluster_severity != "excluded"
    }
    return predicted


def _metrics(expected: set[str], predicted: set[str]) -> tuple[float, float, list[str], list[str]]:
    true_positives = expected & predicted
    precision = len(true_positives) / len(predicted) if predicted else 1.0
    recall = len(true_positives) / len(expected) if expected else 1.0
    return precision, recall, sorted(predicted - expected), sorted(expected - predicted)


def _pair_sets(labels: dict[str, dict[str, object]], rows) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    expected_groups: dict[str, list[str]] = defaultdict(list)
    for record_id, label in labels.items():
        if family := label["expected_cluster_membership"]:
            expected_groups[str(family)].append(record_id)
    predicted_groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row.cluster_severity != "excluded":
            predicted_groups[row.template_cluster_id].append(row.record_id)
    expected_pairs = {pair for members in expected_groups.values() for pair in combinations(sorted(members), 2)}
    predicted_pairs = {pair for members in predicted_groups.values() for pair in combinations(sorted(members), 2)}
    return expected_pairs, predicted_pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the ASCO pipeline on labelled synthetic XML.")
    parser.add_argument("--similarity-threshold", type=float, default=0.88)
    parser.add_argument("--detect-nonsense-candidates", action="store_true")
    args = parser.parse_args(argv)

    labels = json.loads((CORPUS / "labels.json").read_text(encoding="utf-8"))["records"]
    with tempfile.TemporaryDirectory() as output_dir:
        result = run_default_pipeline(
            input_dir=CORPUS,
            tortured_dictionary_path=ROOT / "🤷_tortured.csv",
            output_dir=output_dir,
            similarity_threshold=args.similarity_threshold,
            detect_nonsense_candidates=args.detect_nonsense_candidates,
        )

    print(f"Synthetic evaluation: {len(labels)} labelled abstracts")
    print("detector                 precision  recall  false positives / missed")
    predicted = _predicted_findings(result)
    failed = False
    detector_types = ["llm_response_trace", "tortured_phrase", "template_cluster"]
    if args.detect_nonsense_candidates:
        detector_types.append("nonsense_candidate")
    for detector_type in detector_types:
        precision, recall, false_positives, missed = _metrics(
            _expected(labels, detector_type, optional=detector_type == "nonsense_candidate"), predicted[detector_type]
        )
        failed |= bool(false_positives or missed)
        detail = f"FP={false_positives or '-'}; missed={missed or '-'}"
        print(f"{detector_type:24} {precision:9.3f} {recall:7.3f}  {detail}")

    actual_risks = {row["record_id"]: row["overall_content_risk"] for row in result.abstract_summary_rows}
    risk_mismatches = [
        record_id
        for record_id, label in labels.items()
        if actual_risks.get(record_id) != (
            label.get("expected_optional_risk", label["expected_risk"])
            if args.detect_nonsense_candidates and "nonsense_candidate" in label.get("optional_finding_types", [])
            else label["expected_risk"]
        )
    ]
    print(f"risk mismatches: {risk_mismatches or '-'}")
    failed |= bool(risk_mismatches)

    print("\ntemplate threshold sweep (pairwise cluster metrics)")
    print("threshold  precision  recall  false_positive_pairs  missed_pairs")
    for threshold in THRESHOLDS:
        rows = cluster_templates(result.records, similarity_threshold=threshold)
        expected_pairs, predicted_pairs = _pair_sets(labels, rows)
        precision, recall, false_positives, missed = _metrics(expected_pairs, predicted_pairs)
        print(f"{threshold:9.2f}  {precision:9.3f} {recall:7.3f} {len(false_positives):20d} {len(missed):13d}")
        if threshold == args.similarity_threshold:
            failed |= bool(false_positives or missed)
            legit_ids = {record_id for record_id in labels if record_id.startswith("eval_legit_")}
            leaked_legit = sorted(predicted["template_cluster"] & legit_ids)
            print(f"chosen-threshold legitimate-similarity false positives: {leaked_legit or '-'}")
            failed |= bool(leaked_legit)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
