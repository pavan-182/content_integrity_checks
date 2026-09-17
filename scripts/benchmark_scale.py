"""Run the real pipeline repeatedly on a dataset and record capacity and reliability evidence.

Each run is a fresh CLI subprocess (so peak memory and CPU are that run's own, measured by the
OS) writing to its own output directory. The report combines the pipeline's run_metrics.json
and run_summary.json with process usage and a cross-run consistency check. It never reads or
prints manuscript text.

Model modes:
- offline: deterministic rules only, no gateway.
- fake-gateway: the deterministic GPT-OSS test double (scripts/fake_gpt_oss_gateway.py) over
  loopback HTTP, with optional latency and failure injection. This proves control flow under
  repeatable responses; it is not evidence of real GPT-OSS latency or capacity.

Evidence labelling: a dataset produced by scripts/generate_load_dataset.py is labelled synthetic
from its manifest. Any other directory must be labelled explicitly with --evidence real, and
only for authorized abstracts.

Usage:
    python scripts/generate_load_dataset.py --profile scale --output-dir outputs/load_datasets/scale
    python scripts/benchmark_scale.py --dataset outputs/load_datasets/scale --runs 3 \\
        --model-mode fake-gateway --fake-latency-ms 20 --output outputs/benchmarks/scale-6000
    python scripts/benchmark_scale.py --dataset real_asco_files --evidence real --runs 1 \\
        --model-mode offline --output outputs/benchmarks/real-519
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fake_gpt_oss_gateway import FAILURE_MODES, FakeGateway, GatewayBehaviour  # noqa: E402

CONSISTENCY_KEYS = ("records", "findings", "template", "failures_by_stage_and_category")


def _run_once(command: list[str], environment: dict[str, str]) -> dict[str, object]:
    started = time.perf_counter()
    process = subprocess.Popen(command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    stderr_lines: list[str] = []
    assert process.stderr is not None
    for line in process.stderr:  # Stage lines carry run IDs and timings only.
        stderr_lines.append(line.rstrip())
    _, status, usage = os.wait4(process.pid, 0)
    process.returncode = os.waitstatus_to_exitcode(status)
    return {
        "exit_code": process.returncode,
        "process_wall_seconds": round(time.perf_counter() - started, 2),
        # ru_maxrss is kilobytes on Linux.
        "process_peak_rss_mb": round(usage.ru_maxrss / 1024, 1),
        "process_cpu_seconds": round(usage.ru_utime + usage.ru_stime, 2),
        "stderr_tail": stderr_lines[-5:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory outside Git for run outputs and the report.")
    parser.add_argument("--evidence", choices=("real", "synthetic"), help="Required when the dataset has no generator manifest.")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--model-mode", choices=("offline", "fake-gateway"), default="offline")
    parser.add_argument("--fake-latency-ms", type=float, default=0.0)
    parser.add_argument("--fake-failure-rate", type=float, default=0.0)
    parser.add_argument("--fake-failure-modes", default="429,500,503")
    parser.add_argument("--fake-max-concurrency", type=int, default=None, help="Gateway quota; requests beyond it receive 429.")
    parser.add_argument("--llm-max-concurrency", type=int, default=4)
    parser.add_argument("--dictionary", type=Path, default=ROOT / "🤷_tortured.csv")
    args = parser.parse_args(argv)

    manifest_path = args.dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else None
    evidence = "synthetic" if manifest and manifest.get("synthetic_only") else args.evidence
    if evidence is None:
        parser.error("--evidence is required for a dataset without a synthetic generator manifest")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"--output must be a new or empty directory: {args.output}")
    modes = tuple(mode for mode in args.fake_failure_modes.split(",") if mode)
    if set(modes) - set(FAILURE_MODES):
        parser.error(f"unknown failure modes in {args.fake_failure_modes}")

    args.output.mkdir(parents=True, exist_ok=True)
    behaviour = GatewayBehaviour(args.fake_latency_ms / 1000, args.fake_failure_rate, modes, args.fake_max_concurrency)
    gateway = FakeGateway(behaviour) if args.model_mode == "fake-gateway" else None
    environment = {key: value for key, value in os.environ.items() if not key.startswith("INTELLIHUB_") and key != "api_key"}
    environment["INTELLIHUB_ENV_FILE"] = "/dev/null"  # Never pick up live gateway credentials.
    runs: list[dict[str, object]] = []
    try:
        if gateway:
            gateway.__enter__()
            environment.update(gateway.env())
        for index in range(1, args.runs + 1):
            run_output = args.output / f"run-{index}"
            command = [
                sys.executable, str(ROOT / "scripts/run_pipeline.py"), "--input-dir", str(args.dataset),
                "--tortured-dictionary", str(args.dictionary), "--output-dir", str(run_output),
                "--llm-max-concurrency", str(args.llm_max_concurrency),
            ]
            if args.model_mode == "offline":
                command.append("--offline")
            requests_before = gateway.counters.snapshot()["requests"] if gateway else 0
            process = _run_once(command, environment)
            run: dict[str, object] = {"run": index, **process}
            if process["exit_code"] == 0:
                metrics = json.loads((run_output / "run_metrics.json").read_text())
                summary = json.loads((run_output / "run_summary.json").read_text())
                run.update({
                    "run_id": metrics["run_id"],
                    "totals": metrics["totals"],
                    "stages": metrics["stages"],
                    "gateway": metrics["gateway"],
                    "reconciled": summary["reconciled"],
                    "failed_checks": [check["name"] for check in summary["checks"] if not check["passed"]],
                    **{key: summary[key] for key in CONSISTENCY_KEYS},
                    "json_sha256": summary["outputs"]["content_integrity_json"]["sha256"],
                })
            if gateway:
                run["fake_gateway_requests"] = gateway.counters.snapshot()["requests"] - requests_before
            runs.append(run)
            print(json.dumps({key: run.get(key) for key in ("run", "exit_code", "process_wall_seconds", "process_peak_rss_mb", "reconciled")}), flush=True)
    finally:
        if gateway:
            gateway.__exit__(None, None, None)

    completed = [run for run in runs if run["exit_code"] == 0]
    report = {
        "evidence": evidence,
        "evidence_statement": (
            "Synthetic engineering data: proves orchestration, runtime, memory, and failure handling at this volume; not detector accuracy."
            if evidence == "synthetic" else
            "Authorized real abstracts: behaviour on currently available real inputs; not the full production population."
        ),
        "model_mode": args.model_mode,
        "model_statement": (
            "Deterministic GPT-OSS test double: control flow only; not real GPT-OSS latency, throughput, or capacity."
            if args.model_mode == "fake-gateway" else "Offline: no model calls."
        ),
        "dataset": {
            "path": str(args.dataset),
            "manifest": {key: manifest[key] for key in ("generator_version", "profile", "seed", "parameters", "expected", "dataset_sha256")} if manifest else None,
        },
        "host": {"platform": platform.platform(), "python": platform.python_version(), "cpu_count": os.cpu_count()},
        "configuration": {
            "runs": args.runs, "llm_max_concurrency": args.llm_max_concurrency,
            "fake_gateway": vars(behaviour) if gateway else None,
        },
        "runs": runs,
        "all_runs_succeeded": len(completed) == len(runs),
        "all_runs_reconciled": bool(completed) and all(run["reconciled"] for run in completed),
        "consistent_across_runs": bool(completed) and all(
            all(run[key] == completed[0][key] for key in CONSISTENCY_KEYS) for run in completed
        ),
        "identical_json_across_runs": bool(completed) and len({run["json_sha256"] for run in completed}) == 1,
    }
    (args.output / "benchmark_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("evidence", "model_mode", "all_runs_succeeded", "all_runs_reconciled", "consistent_across_runs", "identical_json_across_runs")}, indent=2))
    print(f"wrote {args.output / 'benchmark_report.json'}")
    return 0 if report["all_runs_succeeded"] and report["all_runs_reconciled"] and report["consistent_across_runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
