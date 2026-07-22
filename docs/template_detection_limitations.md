# Masked near-duplicate detection limitations

This detector is a Tier 1 masked near-duplicate detector, not a general
templating classifier. It reports original-text, masked-skeleton, section and
cluster-cohesion evidence for editorial review.

Candidate generation uses exact original and masked text, exact section
fingerprints, a 15-token prefix, masked-entity shape, and a capped inverted
index of masked five-token shingles. Pair verification combines original,
masked, order-independent n-gram, and weighted section evidence.

Semantic paraphrases with little shared wording can still be missed. Learned
section weights and customer-facing thresholds require a labelled ASCO set and
are intentionally not claimed by this POC.

Reported matches use `cluster_severity=candidate`; no high/medium/low templating
severity is claimed until those bands are validated against labelled examples.

The entity-shape route excludes the non-informative `NOPLACEHOLDER` bucket;
zero-placeholder records still use exact, prefix, and section candidates.
