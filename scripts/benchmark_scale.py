"""Measure pipeline cost at ASCO scale and record it as a comparable baseline.

The repository targets roughly 6,000 abstracts per run but has never been measured there,
so nothing distinguishes "this change is slower" from "this corpus is bigger". This script
builds a synthetic corpus of a requested size from real records, runs the real pipeline over
it, and writes per-stage wall clock, peak RSS, and the run's own counters to JSON.

Usage:
    python scripts/benchmark_scale.py --records 6000 --source real_asco_files
    python scripts/benchmark_scale.py --records 500 --output /tmp/quick.json

ponytail: the synthetic corpus replicates and perturbs real records, so it approximates
corpus *shape* (length, section structure, entity density) rather than the true distribution
of template families. It is a performance baseline, not an accuracy corpus - accuracy lives
in scripts/run_eval.py and scripts/evaluate_template_detection.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import resource
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_integrity.pipeline import run_default_pipeline
from content_integrity.xml_parser import discover_xml_files, parse_xml_records

# Replicated copies must not be byte-identical: an exact-duplicate block collapses into one
# hash bucket and would make candidate generation look far cheaper than it is in reality.
WORD_RE = re.compile(r"\b[a-z]{5,}\b")
DEFAULT_PERTURBATION = 0.15


class _StageCollector(logging.Handler):
    """Collect the structured stage timings that pipeline._stage/_run_detector emit."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.stages: dict[str, float] = {}

    def emit(self, record: logging.LogRecord) -> None:
        stage = getattr(record, "stage", None)
        if stage is not None:
            self.stages[stage] = round(float(getattr(record, "seconds", 0.0)), 2)


def _perturb(text: str, rng: random.Random, rate: float) -> str:
    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        if rng.random() >= rate:
            return word
        letters = list(word)
        index = rng.randrange(len(letters))
        letters[index] = rng.choice("abcdefghijklmnopqrstuvwxyz")
        return "".join(letters)

    return WORD_RE.sub(replace, text)


def build_corpus(sources: list[Path], target: int, directory: Path, rate: float, seed: int) -> int:
    """Replicate source XML until the corpus holds at least `target` *records*.

    One XML file can carry many records (an ASCO `article_set`, or an `article` with
    sub-articles), so the files are counted by the records they actually parse to. Sizing by
    file count instead would silently overshoot by two orders of magnitude.
    """
    originals = [path for source in sources for path in discover_xml_files(source)]
    if not originals:
        raise SystemExit(f"No XML files found under {', '.join(str(s) for s in sources)}")
    records_per_file = [len(parse_xml_records(path)) for path in originals]
    rng = random.Random(seed)
    records = 0
    copies = 0
    while records < target:
        index = copies % len(originals)
        source = originals[index]
        text = source.read_text(encoding="utf-8", errors="replace")
        # The first pass stays verbatim so the corpus keeps genuine duplicate structure for
        # the detectors to find; later passes are perturbed so the whole corpus does not
        # collapse into one exact-hash block and make blocking look free.
        if copies >= len(originals):
            text = _perturb(text, rng, rate)
        (directory / f"bench_{copies:05d}_{source.stem}.xml").write_text(text, encoding="utf-8")
        records += records_per_file[index]
        copies += 1
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--records", type=int, default=6000, help="Synthetic corpus size.")
    parser.add_argument(
        "--source", type=Path, nargs="+", default=[ROOT / "real_asco_files"],
        help="Directories of real XML to replicate from.",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "tests" / "fixtures" / "scale_baseline.json")
    parser.add_argument("--perturbation", type=float, default=DEFAULT_PERTURBATION)
    parser.add_argument("--seed", type=int, default=0, help="Seed; fixed so runs are comparable.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    collector = _StageCollector()
    logging.getLogger("content_integrity.pipeline").addHandler(collector)

    with tempfile.TemporaryDirectory(prefix="asco-bench-") as workspace:
        workspace = Path(workspace)
        corpus = workspace / "corpus"
        corpus.mkdir()
        built = build_corpus(args.source, args.records, corpus, args.perturbation, args.seed)
        logging.info("benchmark: built %d synthetic records", built)

        start = time.perf_counter()
        result = run_default_pipeline(
            input_dir=corpus,
            tortured_dictionary_path=ROOT / "🤷_tortured.csv",
            output_dir=workspace / "out",
            authorship_json_path=workspace / "absent.json",
        )
        total = time.perf_counter() - start

    metadata = dict(result.run_metadata_rows)
    # The pipeline's own count is authoritative; build_corpus only replicates until it
    # expects to have reached the target.
    count = len(result.records)
    report = {
        "_comment": "Performance baseline from scripts/benchmark_scale.py. Compare a change "
                    "against this; regenerate deliberately when the corpus or hardware changes.",
        "record_count": count,
        "requested_records": args.records,
        "perturbation_rate": args.perturbation,
        "seed": args.seed,
        "total_seconds": round(total, 1),
        "seconds_per_record": round(total / count, 4) if count else 0.0,
        # ru_maxrss is kilobytes on Linux.
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "stage_seconds": dict(sorted(collector.stages.items())),
        "counters": {
            key: metadata.get(key)
            for key in (
                "parsed_successfully",
                "failed_files",
                "comparable_record_count",
                "template_candidate_pair_count",
                "template_final_pair_count",
                "enriched_family_count",
                "entity_model_inference_count",
                "operational_issue_count",
                "llm_gateway_request_count",
            )
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
