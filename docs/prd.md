PRD: ASCO Content Integrity Screening V1
Status: Draft
Date: July 13, 2026
1. Overview
ASCO needs a content integrity screening system that helps editorial staff identify suspicious text-level patterns in conference abstract submissions before final editorial decisions.
TNQTech Integrity Central should analyze approximately 6,000 ASCO 2025 Annual Meeting XML abstracts and generate one consolidated Excel workbook that highlights abstracts requiring manual integrity review.
The product should not make acceptance or rejection decisions. It should give ASCO staff clear, explainable evidence so they can prioritize investigation.
2. Primary User
ASCO editorial integrity staff
This user reviews large volumes of submitted abstracts and needs a fast way to identify submissions that may contain suspicious textual patterns, without manually reading every abstract for integrity issues.
3. User Moment
The ASCO staff member has a simple question:
“Which abstracts may have content integrity issues that need closer editorial review?”
They are not trying to judge scientific merit. They need a practical triage report that helps them find risky submissions quickly.
4. Problem Statement
ASCO receives thousands of conference abstracts every year. Reviewers focus mainly on scientific quality, novelty, and relevance, so text-level integrity issues can be difficult to detect consistently.
Without an automated screening system, suspicious patterns such as tortured phrases, LLM response traces, and repeated abstract templates may be missed or discovered too late in the editorial process.
5. Product Goal
Help ASCO editorial staff quickly identify abstracts with potential content integrity risks by producing an explainable, filterable, consolidated Excel report.
6. Desired Outcome
ASCO staff should be able to open one workbook, filter by risk signals, inspect the exact evidence behind each flag, and decide which abstracts require further human investigation.
7. Success Metrics
Metric
Desired Direction
XML abstracts successfully processed
Increase / near 100%
Abstracts included in final workbook
100%
Findings with evidence snippets
100%
Manual review time per suspicious abstract
Decrease
False positives from deterministic checks
Decrease
Unsupported tortured-phrase rules
Decrease
Editor ability to filter and prioritize abstracts
Increase
Workbook usability for pivoting and sorting
High


8. Experience Principles
The screening report should feel:
Explainable: every flag should show the exact evidence.
 Editorially safe: the system should recommend review, not rejection.
 Precise: V1 should prioritize high-confidence signals over broad speculation.
 Efficient: ASCO staff should not need to open separate reports per abstract.
 Filterable: results should work naturally in Excel with sorting, filtering, and pivots.
 Auditable: rules, pattern versions, and evidence should be traceable.
9. Scope
In Scope
XML abstract ingestion
One consolidated Excel workbook
One row per abstract summary
Detailed findings sheet
Nonsense / tortured-phrase detection
LLM response trace detection
Template detection across abstracts
Abstract-level content risk aggregation
Evidence snippets for every finding
Filterable and pivot-ready output
Human review recommendation
Out of Scope
AI-generated text detection
10. Key Requirements
Users can upload or provide a batch of ASCO XML abstracts.
The system can parse abstract text and key metadata from XML files.
The system can detect known tortured phrases and nonsense fingerprints.
The system can show the expected scientific term for a tortured phrase when available.
The system can detect explicit LLM response traces, chatbot residue, or prompt leakage.
The system can compare abstracts against each other to identify repeated templates or skeleton structures.
The system can generate an overall content integrity risk level for each abstract.
The system can show why an abstract was assigned its risk level.
The system can produce one consolidated Excel workbook.
The workbook includes every abstract, including abstracts with no findings.
The workbook includes detailed evidence for every finding.
The workbook supports filtering, sorting, and pivot-table analysis.
The system avoids language that implies fraud, misconduct, AI authorship, or rejection.
11. Detection Principles
The first version should use explainable detection methods, not broad black-box judgment.
Priority signals:
Exact or near-exact tortured phrase matches
Known nonsense fingerprints
Explicit LLM or chatbot residue
Prompt or instruction leakage
Repeated abstract skeletons across unrelated submissions
Multiple content integrity signals in the same abstract
The system should avoid treating normal oncology language, shared trial structure, or standard scientific phrasing as suspicious without evidence.
For V1, precision is more important than broad recall.

12. Edge Cases
Scenario
Expected Behavior
Abstract has no integrity findings
Include it in the workbook with no-risk status
XML file is malformed
Log the issue and mark processing status clearly
Phrase match may be legitimate oncology terminology
Mark as context-dependent or allow reviewer validation
Abstract discusses AI or ChatGPT as a study topic
Reduce confidence for LLM trace matches if context is legitimate
Multiple abstracts are similar because of a legitimate multi-site trial
Show cluster but include metadata context for human review
Same author group submits related abstracts
Do not automatically treat similarity as high risk
LLM trace appears in a quoted example
Flag with lower confidence or context note
Pattern dictionary rule cannot be interpreted
Add to unsupported-rule report, do not silently ignore
No suspicious submissions are found
Workbook should still show all abstracts and run metadata







13. Risks and Mitigations
Risk
Mitigation
Editors interpret flags as rejection decisions
Use “manual review recommended” language
Tortured-phrase matches create false positives
Use evidence strength, oncology allowlists, and reviewer validation
LLM traces are confused with AI-generated text detection
Clearly state this detects residue only, not AI authorship
Template detection flags legitimate trial-family abstracts
Add author, institution, and trial metadata context
Excel becomes hard to use with too many columns
Separate summary sheet and detailed findings sheets
Pattern datasets change over time
Store dictionary version and screening run metadata
V1 scope expands too far
Keep V1 limited to three content integrity checks

14. Open Questions
Should accepted and rejected abstracts appear together in the same workbook or be separated by status?
Which XML fields are guaranteed across all ASCO abstracts?
Does ASCO have historical abstract data for future template comparison, or is V1 limited to the 2025 batch?
Who at ASCO will validate flagged examples during pilot tuning?
What risk threshold should trigger mandatory editorial review?
Should the workbook include pivot tables by default, or only pivot-ready raw sheets?

15. Launch Criteria
The product is ready for launch when:
All valid XML abstracts are processed.
Every abstract appears in the consolidated workbook.
Tortured-phrase findings include matched phrase, expected term, and evidence snippet.
LLM response trace findings include matched phrase, category, and evidence snippet.
Template clusters are generated and reviewable.
Overall content risk is assigned consistently.
Workbook columns are filterable and sortable.
Run metadata and pattern versions are included.
ASCO staff can use the workbook without needing separate reports.
The system does not perform or imply AI-generated text detection.
16. Product Bet
If TNQTech Integrity Central gives ASCO one explainable, filterable Excel dashboard for content integrity screening, ASCO editorial staff will identify suspicious abstracts faster, review them more consistently, and preserve human decision-making while reducing manual review burden.

