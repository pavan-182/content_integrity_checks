from __future__ import annotations

SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


def severity_rank(value: str) -> int:
    return SEVERITY_ORDER.get((value or "none").lower(), 0)


def _risk_from_signals(
    highest_signal_severity: str,
    detector_types: set[str],
    total_finding_count: int,
    template_cluster_flag: bool,
) -> str:
    if total_finding_count == 0 and not template_cluster_flag:
        return "None"
    if len(detector_types) >= 2:
        return "High"
    if severity_rank(highest_signal_severity) >= 3:
        return "High"
    if severity_rank(highest_signal_severity) == 2:
        return "Medium"
    # Includes nonsense_candidate: multiple low-severity findings use the existing Medium rule.
    if severity_rank(highest_signal_severity) == 1:
        return "Medium" if total_finding_count > 1 else "Low"
    if template_cluster_flag:
        return "Medium"
    return "None"
