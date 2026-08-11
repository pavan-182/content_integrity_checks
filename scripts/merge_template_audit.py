from __future__ import annotations

import argparse
import csv
from pathlib import Path


POSITIVE = {"Confirmed template reuse", "Probable template reuse"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Add reviewed audit pairs to a template-detection gold CSV.")
    parser.add_argument("--gold-csv", type=Path, required=True)
    parser.add_argument("--audit-csv", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()

    with args.gold_csv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields, gold = reader.fieldnames or [], list(reader)
    original_count = len(gold)
    with args.audit_csv.open(encoding="utf-8-sig", newline="") as handle:
        audit = list(csv.DictReader(handle))
    seen = {tuple(sorted((row["abstract_id_a"], row["abstract_id_b"]))) for row in gold}
    for row in audit:
        pair = tuple(sorted((row["ID A"].strip(), row["ID B"].strip())))
        if pair in seen:
            continue
        verdict = row["Expected label (manual audit)"].strip()
        positive = verdict in POSITIVE
        gold.append({
            "pair_id": f"G{len(gold) + 1:03d}",
            "abstract_id_a": pair[0],
            "abstract_id_b": pair[1],
            "pair_class": "Possible template reuse" if positive else "Insufficient evidence",
            "reviewed_verdict": verdict,
            "review_confidence": row["Audit confidence"].strip(),
            "recommended_pair_class": "Possible template reuse" if positive else "Insufficient evidence",
            "review_group": "Reviewed unlabelled pipeline prediction",
            "review_basis": "Manual audit of phase0 titles and abstracts",
            "review_rationale": row["Reason (phase0 title + abstract review)"].strip(),
            "primary_evidence": " | ".join(filter(None, [row["Phase0 evidence excerpt A"].strip(), row["Phase0 evidence excerpt B"].strip()])),
            "legitimate_study_context": "",
            "suspicious_family": "yes" if positive else "no",
            "reviewer_notes": f"Detector: {row['Detector match type'].strip()}; confidence: {row['Detector confidence'].strip()}; audit: {row['Error type'].strip()}.",
            "source_a": "",
            "source_b": "",
        })
        seen.add(pair)
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(gold)
    print(f"rows={len(gold)} added={len(gold) - original_count} output={args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
