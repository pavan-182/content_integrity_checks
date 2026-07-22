ASCO SYNTHETIC CONTENT-INTEGRITY TEST SET
=========================================

Files:
- ASCO_synthetic_content_integrity_24_articles.xml: 24 synthetic meeting abstracts in the same broad JATS hierarchy as the supplied ASCO XML (one root article plus 23 sub-articles).
- ASCO_synthetic_content_integrity_ground_truth.json: expected labels and planted evidence.

Distribution:
- 6 LLM response trace examples
- 6 tortured phrase examples
- 6 templated examples in two clusters of three (TPL-A and TPL-B)
- 6 normal examples

Important:
- All titles, authors, study details, institutions, results, identifiers, and DOIs are synthetic.
- The DOI and disclosure URLs are deliberately non-production test values.
- Tortured phrases are seeded synthetic examples for rule testing; they should not be treated as an authoritative fingerprint list.
- Template examples are deliberately similar and are intended to test cross-abstract clustering.
