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

from content_integrity.pipeline import run_default_pipeline
from content_integrity.template_clustering import canonical_pair_key


CORPUS = ROOT / "tests" / "fixtures" / "eval_corpus"


def _metrics(expected: set, predicted: set) -> tuple[float, float, list, list]:
    true_positives = expected & predicted
    precision = len(true_positives) / len(predicted) if predicted else 1.0
    recall = len(true_positives) / len(expected) if expected else 1.0
    return precision, recall, sorted(predicted - expected), sorted(expected - predicted)


def _expected_groups(labels: dict[str, dict[str, object]]) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = defaultdict(set)
    for record_id, label in labels.items():
        if family := label.get("expected_cluster_membership"):
            groups[str(family)].add(record_id)
    return dict(groups)


def _pairs(groups: dict[str, set[str]]) -> set[tuple[str, str]]:
    return {
        pair
        for members in groups.values()
        for pair in combinations(sorted(members), 2)
    }


def _predicted_groups(result) -> dict[str, set[str]]:
    return {
        str(row["template_family_id"]): set(row["member_ids"])
        for row in result.template_family_rows
        if int(row["member_count"]) >= 3
    }


def _legacy_pairs(path: Path) -> set[tuple[str, str]]:
    groups: dict[str, set[str]] = defaultdict(set)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("template_cluster_id") and row.get("cluster_severity") != "excluded":
                groups[row["template_cluster_id"]].add(row["record_id"])
    return _pairs(dict(groups))


def _print_metrics(name: str, expected: set, predicted: set) -> bool:
    precision, recall, false_positives, missed = _metrics(expected, predicted)
    print(
        f"{name:28} precision={precision:.3f} recall={recall:.3f} "
        f"false_positives={false_positives or '-'} missed={missed or '-'}"
    )
    return bool(false_positives or missed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate pair, family, and abstract template outputs.")
    parser.add_argument("--detect-nonsense-candidates", action="store_true")
    parser.add_argument("--legacy-similarity-threshold", type=float, default=0.88)
    args = parser.parse_args(argv)

    labels = json.loads((CORPUS / "labels.json").read_text(encoding="utf-8"))["records"]
    expected_groups = _expected_groups(labels)
    expected_pairs = _pairs(expected_groups)
    expected_members = set().union(*expected_groups.values()) if expected_groups else set()

    with tempfile.TemporaryDirectory() as baseline_dir, tempfile.TemporaryDirectory() as comparison_dir:
        common = {
            "input_dir": CORPUS,
            "tortured_dictionary_path": ROOT / "🤷_tortured.csv",
            "detect_nonsense_candidates": args.detect_nonsense_candidates,
            "legacy_similarity_threshold": args.legacy_similarity_threshold,
        }
        baseline = run_default_pipeline(output_dir=baseline_dir, **common)
        comparison = run_default_pipeline(
            output_dir=comparison_dir,
            compare_legacy_template_clustering=True,
            **common,
        )

        predicted_pairs = {
            canonical_pair_key(pair.record_id, pair.matched_record_id)
            for pair in baseline.pair_findings
        }
        predicted_groups = _predicted_groups(baseline)
        predicted_family_pairs = _pairs(predicted_groups)
        predicted_members = set().union(*predicted_groups.values()) if predicted_groups else set()

        failed = _print_metrics("pair findings", expected_pairs, predicted_pairs)
        failed |= _print_metrics("family members", expected_members, predicted_members)
        failed |= _print_metrics("family pairwise", expected_pairs, predicted_family_pairs)

        expected_family_by_member = {
            member: family
            for family, members in expected_groups.items()
            for member in members
        }
        wrongly_merged = {
            family: sorted(members)
            for family, members in predicted_groups.items()
            if len({expected_family_by_member.get(member) for member in members} - {None}) > 1
        }
        predicted_family_by_member = {
            member: family
            for family, members in predicted_groups.items()
            for member in members
        }
        split_expected = {}
        for family, members in expected_groups.items():
            predicted_ids = {
                predicted_family_by_member.get(member) for member in members
            }
            if None in predicted_ids or len(predicted_ids) != 1:
                split_expected[family] = sorted(predicted_ids - {None})
        print(f"wrongly merged families: {wrongly_merged or '-'}")
        print(f"split expected families: {split_expected or '-'}")
        failed |= bool(wrongly_merged or split_expected)

        summaries = {row["record_id"]: row for row in baseline.abstract_summary_rows}
        expected_flags = {
            record_id
            for record_id, label in labels.items()
            if label.get("expected_cluster_membership")
            or "template_cluster" in label.get("expected_finding_types", [])
        }
        actual_flags = {
            record_id for record_id, row in summaries.items() if row["template_flag"] == "Yes"
        }
        failed |= _print_metrics("abstract template flags", expected_flags, actual_flags)

        family_members = predicted_members
        two_member_only = {
            record_id
            for pair in baseline.pair_findings
            for record_id in (pair.record_id, pair.matched_record_id)
            if record_id not in family_members
        }
        flag_errors = sorted(
            record_id
            for record_id in two_member_only
            if summaries[record_id]["template_cluster_flag"] != "No"
        )
        family_flag_errors = sorted(
            record_id
            for record_id in family_members
            if summaries[record_id]["template_cluster_flag"] != "Yes"
        )
        finding_counts = defaultdict(int)
        for finding in baseline.findings:
            finding_counts[finding.record_id] += 1
        risk_count_errors = sorted(
            record_id
            for record_id, row in summaries.items()
            if row["total_finding_count"]
            != finding_counts[record_id] + (1 if row["template_flag"] == "Yes" else 0)
        )
        print(f"two-member cluster-flag errors: {flag_errors or '-'}")
        print(f"family cluster-flag errors: {family_flag_errors or '-'}")
        print(f"template double-count errors: {risk_count_errors or '-'}")
        failed |= bool(flag_errors or family_flag_errors or risk_count_errors)

        production_unchanged = (
            [pair.to_dict() for pair in baseline.pair_findings]
            == [pair.to_dict() for pair in comparison.pair_findings]
            and baseline.template_family_rows == comparison.template_family_rows
            and baseline.abstract_summary_rows == comparison.abstract_summary_rows
        )
        legacy_pairs = _legacy_pairs(comparison.output_paths["legacy_template_comparison_jsonl"])
        print(
            "legacy comparison: "
            f"legacy_only={sorted(legacy_pairs - predicted_pairs) or '-'} "
            f"new_only={sorted(predicted_pairs - legacy_pairs) or '-'} "
            f"shared={len(legacy_pairs & predicted_pairs)} "
            f"false_positives={sorted(legacy_pairs - expected_pairs) or '-'} "
            f"missed_labelled={sorted(expected_pairs - legacy_pairs) or '-'} "
            f"production_unchanged={production_unchanged}"
        )
        failed |= not production_unchanged

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
