"""Generate reproducible synthetic ASCO-shaped XML datasets for reliability and capacity tests.

Every word comes from the grammar in this file; no customer manuscript text is read or copied.
The XML mirrors the shape of the real ASCO bundles (one `article` root carrying further
`sub-article` records) so the real parser, detectors, and reporting run unchanged.

Synthetic data is for engineering validation only (runtime, memory, failure handling,
reconciliation). It must not be used to estimate detector accuracy.

Usage:
    python scripts/generate_load_dataset.py --profile scale --output-dir outputs/load_datasets/scale
    python scripts/generate_load_dataset.py --profile ci --records 60 --seed 7 --output-dir /tmp/ci

Profiles and their defaults are listed in PROFILES. The output directory receives the XML
files plus `manifest.json` (generator version, parameters, per-file SHA-256, and expected
input/duplicate/invalid counts). Re-running with the same arguments reproduces identical bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from xml.sax.saxutils import escape

GENERATOR_VERSION = "asco-synthetic-load-v4"


@dataclass(frozen=True)
class Profile:
    records: int
    records_per_file: int = 250
    # Share of records rewritten from a family base by entity substitution (template reuse).
    family_share: float = 0.03
    family_size_range: tuple[int, int] = (2, 5)
    long_abstracts: bool = False
    llm_residue_share: float = 0.004
    tortured_share: float = 0.01
    # Real ASCO abstracts cite a registered trial ID in roughly 4% of records.
    trial_id_share: float = 0.04
    malformed_cases: bool = False


PROFILES: dict[str, Profile] = {
    "ci": Profile(records=60, records_per_file=25),
    "medium": Profile(records=1000),
    "scale": Profile(records=6000),
    # Large families exceed the candidate-bucket caps; many 30-50 member families sit just under
    # them, which is the worst case for within-bucket pair growth.
    "high_similarity": Profile(records=1500, family_share=0.9, family_size_range=(30, 120)),
    "long_abstract": Profile(records=300, long_abstracts=True),
    "malformed": Profile(records=0, malformed_cases=True),
    "mixed": Profile(records=1000, malformed_cases=True),
}

DISEASES = [
    "breast cancer", "non-small cell lung cancer", "colorectal cancer", "prostate cancer",
    "ovarian cancer", "pancreatic cancer", "gastric cancer", "hepatocellular carcinoma",
    "renal cell carcinoma", "urothelial carcinoma", "melanoma", "glioblastoma",
    "multiple myeloma", "acute myeloid leukemia", "diffuse large B-cell lymphoma",
    "head and neck squamous cell carcinoma", "endometrial cancer", "cervical cancer",
    "small cell lung cancer", "mesothelioma", "esophageal adenocarcinoma", "thyroid cancer",
    "triple-negative breast cancer", "chronic lymphocytic leukemia", "soft tissue sarcoma",
]
DRUGS = [
    "pembrolizumab", "nivolumab", "atezolizumab", "durvalumab", "trastuzumab", "pertuzumab",
    "osimertinib", "alectinib", "lorlatinib", "olaparib", "niraparib", "palbociclib",
    "ribociclib", "abemaciclib", "enzalutamide", "abiraterone", "lenvatinib", "cabozantinib",
    "sotorasib", "adagrasib", "capecitabine", "docetaxel", "carboplatin", "gemcitabine",
    "bevacizumab", "cetuximab", "ipilimumab", "tucatinib", "sacituzumab govitecan",
    "trastuzumab deruxtecan", "venetoclax", "ibrutinib", "daratumumab", "lenalidomide",
]
GENES = ["EGFR", "ALK", "KRAS", "BRAF", "HER2", "BRCA1", "BRCA2", "PIK3CA", "TP53", "MET", "RET", "ROS1", "NTRK", "PD-L1"]
ENDPOINTS = [
    "overall survival", "progression-free survival", "objective response rate",
    "disease-free survival", "pathological complete response", "time to treatment failure",
    "event-free survival", "duration of response", "quality of life", "time to next treatment",
]
POPULATIONS = [
    "older adults", "patients aged 65 years or older", "rural patients", "Medicaid enrollees",
    "adolescents and young adults", "postmenopausal women", "veterans", "uninsured patients",
    "patients with limited English proficiency", "community practice patients",
]
SETTINGS = [
    "an academic cancer center", "a community oncology network", "a national claims database",
    "a statewide cancer registry", "an integrated health system", "a safety-net hospital",
    "a multicenter consortium", "a regional telehealth program", "a comprehensive cancer network",
]
DESIGNS = [
    "a retrospective cohort study", "a prospective observational study", "a randomized phase II trial",
    "a single-arm phase II trial", "a cross-sectional survey", "a quality improvement initiative",
    "a pragmatic cluster-randomized trial", "a matched case-control study", "a mixed-methods evaluation",
]
INTERVENTIONS = [
    "nurse navigation", "electronic symptom monitoring", "a multidisciplinary tumor board",
    "financial toxicity screening", "geriatric assessment", "virtual survivorship visits",
    "pharmacist-led toxicity management", "a decision aid", "same-day scheduling",
    "an automated referral pathway", "remote patient monitoring", "early palliative care",
]
OUTCOMES = [
    "time to first treatment", "emergency department visits", "treatment completion",
    "patient-reported symptom burden", "30-day readmissions", "guideline-concordant care",
    "clinical trial enrollment", "out-of-pocket costs", "hospice enrollment", "visit adherence",
]
STATS = [
    "multivariable Cox regression", "logistic regression", "propensity score matching",
    "Kaplan-Meier estimation", "generalized estimating equations", "inverse probability weighting",
    "interrupted time series analysis", "mixed-effects models", "competing risk regression",
]
# Public examples of tortured phrases (Cabanac et al., 2021) that exist in the runtime dictionary.
TORTURED_SENTENCES = [
    "Counterfeit consciousness tools were piloted to triage referral queues.",
    "An irregular woodland model ranked predictors of treatment delay.",
    "Colossal information from the registry was linked to claims files.",
]
LLM_RESIDUE_SENTENCES = [
    "As an AI language model, I cannot verify the clinical accuracy of these statements.",
    "Certainly! Here is a revised version of the results section for your abstract.",
]


def _pick(rng: random.Random, values: list[str]) -> str:
    return values[rng.randrange(len(values))]


# Real abstracts share topic vocabulary but rarely share long word sequences. Sentences are built
# from short connectors and several large, independently sampled slots, so a given five-word
# sequence recurs rarely; calibrate against scripts' candidate-density measurement on real data.
ADJECTIVES = [
    "adjusted", "baseline", "clinical", "comparative", "concurrent", "consecutive", "contemporary",
    "cumulative", "demographic", "differential", "documented", "early", "eligible", "estimated",
    "exploratory", "extended", "geographic", "heterogeneous", "incremental", "independent",
    "institutional", "integrated", "lower", "matched", "measurable", "median", "moderate",
    "multilevel", "national", "observed", "operational", "patient-level", "persistent", "pooled",
    "predicted", "primary", "prospective", "quarterly", "racial", "regional", "relative",
    "retrospective", "routine", "rural", "secondary", "sequential", "socioeconomic", "specific",
    "stratified", "structured", "subsequent", "sustained", "system-level", "targeted", "timely",
    "unadjusted", "unplanned", "urban", "weighted", "yearly",
]
NOUNS = [
    "access", "adherence", "admissions", "allocation", "appointments", "assessment", "attrition",
    "barriers", "benchmarks", "billing", "burden", "capacity", "caregivers", "clinicians",
    "cohorts", "communication", "completion", "complications", "concordance", "consultations",
    "continuity", "coordination", "costs", "coverage", "delays", "deprivation", "diagnoses",
    "disparities", "distance", "documentation", "dose intensity", "education", "encounters",
    "engagement", "enrollment", "equity", "escalation", "expenditures", "follow-up", "handoffs",
    "hospitalizations", "imaging", "income", "infusions", "insurance", "interruptions", "literacy",
    "mortality", "navigation", "notifications", "outcomes", "pathways", "payers", "performance",
    "pharmacy", "prescribing", "procedures", "protocols", "quality", "readmissions", "referrals",
    "reimbursement", "reporting", "resources", "retention", "scheduling", "screening", "staffing",
    "surveillance", "survivorship", "symptoms", "telehealth", "throughput", "toxicity", "transfers",
    "transportation", "triage", "uptake", "utilization", "variation", "visits", "volume",
    "wait times", "workflow", "workload",
]
VERBS = [
    "accelerated", "accompanied", "affected", "altered", "anticipated", "clarified", "coincided with",
    "complemented", "constrained", "correlated with", "decreased", "diminished", "distinguished",
    "enabled", "exceeded", "explained", "facilitated", "improved", "increased", "influenced",
    "limited", "mirrored", "modified", "offset", "outpaced", "paralleled", "predicted", "preceded",
    "reduced", "reflected", "reinforced", "shaped", "shortened", "strengthened", "supported",
    "tracked with", "undermined", "varied with",
]
CONNECTORS = [
    "among", "across", "within", "for", "in", "throughout", "during", "after", "before", "despite",
    "alongside", "beyond", "following", "under", "via",
]
HEDGES = ["notably ", "importantly ", "overall ", "additionally ", "similarly ", "conversely ", "unexpectedly "]


def _phrase(rng: random.Random) -> str:
    return f"{_pick(rng, ADJECTIVES)} {_pick(rng, NOUNS)}"


def _clause(rng: random.Random, slots: dict[str, str]) -> str:
    """Subject, verb, object, and context sampled independently; named slots are the minority.

    Calibrated against the real batch's candidate density (see docs/PHASE2_RELIABILITY_CAPACITY.md):
    fixed multiword values and repeated sentence boundaries inflate shared masked five-word
    sequences far beyond real abstracts, which would overstate template-comparison cost.
    """
    hedge = _pick(rng, HEDGES) if rng.random() < 0.2 else ""
    subject = _phrase(rng) if rng.random() < 0.8 else rng.choice([slots["intervention"], slots["outcome"]])
    context = _phrase(rng) if rng.random() < 0.7 else rng.choice([slots["population"], slots["disease"], slots["setting"], slots["drug"], slots["gene"]])
    return f"{hedge}{subject} {_pick(rng, VERBS)} {_phrase(rng)} {_pick(rng, CONNECTORS)} {context} {_pick(rng, NOUNS)}"


def _statistic(rng: random.Random) -> str:
    value = round(rng.uniform(0.41, 1.35), 2)
    measure = rng.choice([
        f"HR {value}; 95% CI {round(value - rng.uniform(0.05, 0.2), 2)}-{round(value + rng.uniform(0.05, 0.3), 2)}",
        f"OR {value}; p = {rng.choice(['0.001', '0.003', '0.01', '0.02', '0.04', '0.21', '0.48'])}",
        f"{round(rng.uniform(4, 92), 1)}% vs {round(rng.uniform(4, 92), 1)}%",
        f"n = {rng.randrange(20, 9000)}",
        f"median {round(rng.uniform(3, 60), 1)} {rng.choice(['months', 'days', 'weeks'])}",
    ])
    return f"{measure} {rng.choice(['for', 'in', 'among', 'with'])} {_pick(rng, ADJECTIVES)} {_pick(rng, NOUNS)}"


def _sentence_background(rng: random.Random, slots: dict[str, str]) -> str:
    return f"{_clause(rng, slots)}."


def _sentence_methods(rng: random.Random, slots: dict[str, str]) -> str:
    if rng.random() < 0.1:
        # A design sentence, with sampled words around the multiword design and setting values.
        return f"{slots['design'].capitalize()} of {_phrase(rng)} {_pick(rng, CONNECTORS)} {slots['setting']} {_pick(rng, NOUNS)} ({rng.randrange(2010, 2023)})."
    return f"{_clause(rng, slots)} using {_phrase(rng)}."


def _sentence_results(rng: random.Random, slots: dict[str, str]) -> str:
    return f"{_clause(rng, slots)} ({_statistic(rng)})."


def _sentence_conclusions(rng: random.Random, slots: dict[str, str]) -> str:
    return f"{_clause(rng, slots)}."


SECTION_BUILDERS = [
    ("Background", _sentence_background, (1, 3)),
    ("Methods", _sentence_methods, (2, 4)),
    ("Results", _sentence_results, (3, 6)),
    ("Conclusions", _sentence_conclusions, (1, 2)),
]


def _slots(rng: random.Random) -> dict[str, str]:
    return {
        "disease": _pick(rng, DISEASES), "drug": _pick(rng, DRUGS), "gene": _pick(rng, GENES),
        "endpoint": _pick(rng, ENDPOINTS), "population": _pick(rng, POPULATIONS),
        "setting": _pick(rng, SETTINGS), "design": _pick(rng, DESIGNS),
        "intervention": _pick(rng, INTERVENTIONS), "outcome": _pick(rng, OUTCOMES),
        "stat": _pick(rng, STATS),
    }


def _capitalize(sentence: str) -> str:
    return sentence[:1].upper() + sentence[1:]


@dataclass(frozen=True)
class SyntheticAbstract:
    record_id: str
    title: str
    sections: tuple[tuple[str, str], ...]
    subject: str


def _abstract(rng: random.Random, record_id: str, profile: Profile) -> SyntheticAbstract:
    slots = _slots(rng)
    # Real ASCO abstracts: ~92% four sections, ~5% three, a few with one to five.
    layout = rng.random()
    builders = SECTION_BUILDERS if layout < 0.92 else SECTION_BUILDERS[1:] if layout < 0.97 else SECTION_BUILDERS[:1] + SECTION_BUILDERS[2:]
    target_chars = rng.randint(6000, 14000) if profile.long_abstracts else int(min(2990, max(1050, rng.gauss(2470, 380))))
    sections: list[list[str]] = [[] for _ in builders]
    for position, (_, builder, (low, _high)) in enumerate(builders):
        for _ in range(low):
            sections[position].append(_capitalize(builder(rng, _slots(rng) if rng.random() < 0.35 else slots)))
    # Grow the Results and Methods sections until the abstract reaches its sampled length.
    while sum(len(sentence) + 1 for section in sections for sentence in section) < target_chars:
        position = rng.choice([index for index, (label, _, _) in enumerate(builders) if label in {"Methods", "Results"}] or [0])
        sections[position].append(_capitalize(builders[position][1](rng, _slots(rng) if rng.random() < 0.35 else slots)))
    if rng.random() < profile.llm_residue_share:
        sections[-1].insert(0, _pick(rng, LLM_RESIDUE_SENTENCES))
    if rng.random() < profile.tortured_share:
        sections[min(1, len(sections) - 1)].append(_pick(rng, TORTURED_SENTENCES))
    if rng.random() < profile.trial_id_share:
        sections[min(1, len(sections) - 1)].append(f"This study was registered as NCT{rng.randrange(10**7, 10**8):08d}.")
    title = _capitalize(
        f"{_phrase(rng)} {_pick(rng, CONNECTORS)} {rng.choice([slots['population'], _phrase(rng)])} "
        f"{rng.choice(['with', 'receiving', 'treated for', 'referred for'])} {rng.choice([slots['disease'], slots['drug'], _pick(rng, NOUNS)])}"
    )
    return SyntheticAbstract(
        record_id=record_id,
        title=title,
        sections=tuple((label, " ".join(text)) for (label, _, _), text in zip(builders, sections)),
        subject=rng.choice(["Care Delivery/Models of Care", "Breast Cancer—Local/Regional/Adjuvant", "Health Services Research", "Developmental Therapeutics"]),
    )


def _family_variant(rng: random.Random, base: SyntheticAbstract, record_id: str) -> SyntheticAbstract:
    """Template reuse: same skeleton, study-specific entities and numbers substituted."""
    swaps = [(old, _pick(rng, pool)) for pool in (DRUGS, DISEASES, GENES, POPULATIONS) for old in pool if old in base.title or any(old in text for _, text in base.sections)]
    digits = str.maketrans("0123456789", "".join(rng.sample("0123456789", 10)))

    def rewrite(text: str) -> str:
        for old, new in swaps:
            text = text.replace(old, new)
        return text.translate(digits)

    return replace(
        base,
        record_id=record_id,
        title=rewrite(base.title),
        sections=tuple((label, rewrite(text)) for label, text in base.sections),
    )


def _sub_article_xml(item: SyntheticAbstract, *, root: bool) -> str:
    body = "".join(f"<bold>{escape(label)}: </bold>{escape(text)} " for label, text in item.sections)
    meta = (
        "<article-meta>"
        f'<article-id pub-id-type="custom" custom-type="abstract-id">{escape(item.record_id)}</article-id>'
        f'<article-id pub-id-type="doi">10.9999/SYNTHETIC.{escape(item.record_id)}</article-id>'
        f'<article-categories><subj-group subj-group-type="heading"><subject>{escape(item.subject)}</subject></subj-group></article-categories>'
        f"<title-group><article-title>{escape(item.title)}</article-title></title-group>"
        '<contrib-group><contrib contrib-type="presenter"><name><surname>Synthetic</surname>'
        f"<given-names>Presenter {escape(item.record_id[-6:])}</given-names></name></contrib>"
        "<aff>Synthetic Oncology Research Center</aff></contrib-group>"
        '<pub-date pub-type="ppub"><year>2026</year></pub-date>'
        f"<abstract><p><bold>e{int(hashlib.sha256(item.record_id.encode()).hexdigest()[:6], 16)}</bold></p><p>{body.strip()}</p></abstract>"
        "</article-meta>"
    )
    journal = (
        "<journal-meta><journal-title-group><journal-title>Synthetic Journal of Clinical Oncology"
        "</journal-title></journal-title-group></journal-meta>"
    )
    if root:
        return f"<front>{journal}{meta}</front>"
    return f'<sub-article article-type="meeting-abstract"><front-stub>{journal}{meta}</front-stub></sub-article>'


def _bundle_xml(items: list[SyntheticAbstract]) -> str:
    head, *rest = items
    return (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        '<article article-type="meeting-abstract">'
        + _sub_article_xml(head, root=True)
        + "".join(_sub_article_xml(item, root=False) for item in rest)
        + "</article>\n"
    )


def _malformed_cases(rng: random.Random, prefix: str) -> list[tuple[str, bytes, dict[str, int]]]:
    """(file name, bytes, expected counts) for each invalid-input shape the batch must survive."""
    valid = _abstract(rng, f"{prefix}-mal-valid", PROFILES["ci"])
    good = _bundle_xml([valid]).encode()
    empty_abstract = replace(_abstract(rng, f"{prefix}-mal-noabstract", PROFILES["ci"]), sections=())
    no_text = replace(_abstract(rng, f"{prefix}-mal-notext", PROFILES["ci"]), sections=(), title="")
    collision_left = _abstract(rng, f"{prefix}-mal-collision", PROFILES["ci"])
    collision_right = replace(_abstract(rng, "x", PROFILES["ci"]), record_id=collision_left.record_id)
    duplicate = _abstract(rng, f"{prefix}-mal-duplicate", PROFILES["ci"])
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE article [<!ENTITY a "aaaaaaaaaa">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
        '<article><front><article-meta><title-group><article-title>&c;</article-title></title-group>'
        "</article-meta></front></article>"
    ).encode()
    one = {"input_records": 1}
    return [
        (f"{prefix}_mal_truncated.xml", good[: len(good) // 2], {**one, "invalid_records": 1}),
        (f"{prefix}_mal_bad_encoding.xml", good.replace(b"Synthetic Oncology", b"Synthetic \xff\xfe Oncology"), {**one, "invalid_records": 1}),
        (f"{prefix}_mal_empty.xml", b"", {**one, "invalid_records": 1}),
        (f"{prefix}_mal_not_xml.xml", b"this is not xml, just text with an .xml extension\n", {**one, "invalid_records": 1}),
        (f"{prefix}_mal_unexpected_root.xml", b"<?xml version='1.0'?><records><record>free text only</record></records>", {**one, "invalid_records": 1}),
        (f"{prefix}_mal_entity_expansion.xml", bomb, {**one}),
        (f"{prefix}_mal_empty_abstract.xml", _bundle_xml([empty_abstract]).encode(), {**one}),
        (f"{prefix}_mal_no_text.xml", _bundle_xml([no_text]).encode(), {**one, "invalid_records": 1}),
        (f"{prefix}_mal_collision_a.xml", _bundle_xml([collision_left]).encode(), {**one}),
        (f"{prefix}_mal_collision_b.xml", _bundle_xml([collision_right]).encode(), {**one}),
        (f"{prefix}_mal_repeated_subarticle.xml", _bundle_xml([valid, duplicate, duplicate]).encode(), {"input_records": 3, "duplicate_records": 1}),
    ]


def generate(profile_name: str, output_dir: Path, *, seed: int = 20260915, records: int | None = None) -> dict[str, object]:
    """Write the dataset and its manifest; return the manifest. Output directory must be empty."""
    profile = PROFILES[profile_name]
    if records is not None:
        profile = replace(profile, records=records)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError(f"Output directory is not empty: {output_dir}")
    rng = random.Random(f"{GENERATOR_VERSION}:{profile_name}:{seed}")
    prefix = f"syn{profile_name.replace('_', '')}"
    items: list[SyntheticAbstract] = []
    while len(items) < profile.records:
        remaining = profile.records - len(items)
        base = _abstract(rng, f"{prefix}-{len(items) + 1:06d}", profile)
        items.append(base)
        if rng.random() < profile.family_share and remaining >= profile.family_size_range[0]:
            for _ in range(min(rng.randint(*profile.family_size_range), remaining) - 1):
                items.append(_family_variant(rng, base, f"{prefix}-{len(items) + 1:06d}"))
    # Families are generated contiguously; shuffle so files and ordering do not encode them.
    rng.shuffle(items)
    files: list[tuple[str, bytes, dict[str, int]]] = []
    for index in range(0, len(items), profile.records_per_file):
        chunk = items[index:index + profile.records_per_file]
        files.append((f"{prefix}_{index // profile.records_per_file + 1:04d}.xml", _bundle_xml(chunk).encode(), {"input_records": len(chunk)}))
    if profile.malformed_cases:
        cases = _malformed_cases(rng, prefix)
        # Interleave invalid inputs between valid files for the mixed profile.
        for offset, case in enumerate(cases):
            files.insert(min(len(files), offset * 2 + 1), case)
    manifest_files = []
    totals = {"input_records": 0, "duplicate_records": 0, "invalid_records": 0}
    for name, content, counts in files:
        (output_dir / name).write_bytes(content)
        manifest_files.append({"name": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(), **counts})
        for key in totals:
            totals[key] += counts.get(key, 0)
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "profile": profile_name,
        "seed": seed,
        "parameters": asdict(profile),
        "synthetic_only": True,
        "use_restriction": "Engineering validation only; not for detector accuracy.",
        "expected": totals,
        "file_count": len(manifest_files),
        "files": manifest_files,
        "dataset_sha256": hashlib.sha256("".join(item["sha256"] for item in manifest_files).encode()).hexdigest(),
        "command": f"python scripts/generate_load_dataset.py --profile {profile_name} --seed {seed}"
                   + (f" --records {records}" if records is not None else "") + " --output-dir <dir>",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="Empty directory outside Git (for example under outputs/).")
    parser.add_argument("--seed", type=int, default=20260915)
    parser.add_argument("--records", type=int, default=None, help="Override the profile's valid record count.")
    args = parser.parse_args(argv)
    manifest = generate(args.profile, args.output_dir, seed=args.seed, records=args.records)
    print(json.dumps({key: manifest[key] for key in ("profile", "seed", "expected", "file_count", "dataset_sha256")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
