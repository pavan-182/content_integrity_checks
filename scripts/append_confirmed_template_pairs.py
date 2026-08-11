from __future__ import annotations

import argparse
import csv
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Append manually confirmed pairs to a template gold CSV.")
    parser.add_argument("--gold-csv", type=Path, required=True)
    parser.add_argument("--confirmed-csv", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()
    with args.gold_csv.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames or [], list(reader)
    with args.confirmed_csv.open(encoding="utf-8", newline="") as handle:
        confirmed = list(csv.DictReader(handle))
    seen = {tuple(sorted((row["abstract_id_a"], row["abstract_id_b"]))) for row in rows}
    for row in confirmed:
        pair = tuple(sorted((row["abstract_id_a"], row["abstract_id_b"])))
        if pair in seen:
            raise ValueError(f"Duplicate pair: {pair}")
        rows.append({
            "pair_id": f"G{len(rows) + 1:03d}",
            "abstract_id_a": pair[0], "abstract_id_b": pair[1],
            "pair_class": "Possible template reuse",
            "reviewed_verdict": row["reviewed_verdict"],
            "review_confidence": row["review_confidence"],
            "recommended_pair_class": "Possible template reuse",
            "review_group": "Confirmed formerly unlabelled pipeline prediction",
            "review_basis": "Manual title and abstract review",
            "review_rationale": row["review_rationale"],
            "primary_evidence": row["primary_evidence"],
            "legitimate_study_context": "",
            "suspicious_family": "yes",
            "reviewer_notes": "User-confirmed after pipeline review.",
            "source_a": row["source_a"], "source_b": row["source_b"],
        })
        seen.add(pair)
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"rows={len(rows)} added={len(confirmed)} output={args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
