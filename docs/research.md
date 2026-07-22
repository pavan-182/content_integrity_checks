Content Integrity Research
This research aligns with the current ASCO scope.
The goal is not to detect whether an abstract was AI-generated.
The goal is to identify text-level content integrity risks that may require human editorial review.
ASCO needs one consolidated Excel workbook that helps editorial staff answer:
“Which abstracts look textually suspicious enough to review more closely?”
The system should screen approximately 6,000 XML abstracts and flag evidence-based issues across three detector areas:
LLM response traces
Nonsense / tortured phrases
Template detection
Risk aggregation across the above signals
The first three are independent detectors. The fourth combines their outputs into a single editorial risk view.
      
1. LLM Response Traces
This detector looks for leftover traces from an LLM or chatbot workflow inside the abstract.
This is different from AI-generated text detection.
The question is not:
“Was this abstract written by AI?”
The question is:
“Did the author accidentally leave chatbot residue, prompt text, or model response artifacts inside the abstract?”
These traces are strong integrity signals because they should not appear in a submitted scientific abstract.
Examples
Chatbot self-reference:
As an AI language model...
I cannot provide medical advice...
I do not have access to real-time data...
Response preambles:
Certainly, here is the revised abstract.
Below is the rewritten version.
Here is a more academic version.
Prompt or instruction leakage:
Rewrite this abstract.
Improve grammar.
Do not highlight negatives.
Positive review only.
Summarize the following.
Conversation labels:
User:
Assistant:
System:
Human:
Interface residue:
Regenerate response
Copy response
New chat
Markdown or formatting remnants may also be useful as weak supporting signals:
###
---
**
These weak formatting signals should not create a high-risk flag by themselves, but they can strengthen a finding when combined with stronger evidence.
Research Focus
Research should focus on:
Known LLM response phrases
Prompt leakage examples
Chatbot conversation residue
Editorial or author workflow residue
Markdown artifacts
XML encoding or extraction artifacts
False positives in abstracts about AI or chatbot studies
Detection Approach
This should be a deterministic, rule-based detector.
Recommended pipeline:
XML abstract
↓
Extract abstract sections
↓
Normalize text
↓
Run phrase and regex matching
↓
Classify trace category
↓
Capture evidence snippet
↓
Assign severity and confidence
↓
Write finding to Excel
Why this is a good V1 detector
This is one of the easiest and most defensible POCs because the evidence is concrete. If an abstract contains “As an AI language model” or “Here is the revised abstract,” the reviewer can immediately understand why it was flagged.
Output Needed
For each finding, capture:
Abstract ID
Matched phrase
Trace category
Abstract section
Evidence snippet
Severity
Confidence
Recommended action: manual review

2. Nonsense / Tortured Phrases
This detector looks for distorted, awkward, or nonsensical scientific phrases that may have been created by paraphrasing tools, poor translation, paper mills, or attempts to evade similarity detection.
The classic example is a normal scientific term being replaced with a strange synonym.
Examples
Artificial intelligence → Artificial cleverness
Machine learning → Mechanical learning
Neural network → Nervous network
Deep learning → Profound learning
Breast cancer → Bosom peril
In an oncology abstract, this matters because distorted scientific language can make the abstract harder to trust and may indicate that the text was produced or manipulated through questionable workflows.
The system is not proving misconduct. It is saying:
“This abstract contains unusual scientific wording that should be reviewed.”

Research Focus
Research should focus on:
Known tortured phrase databases
Problematic Paper Screener fingerprints
Publisher approaches to tortured phrase detection
Oncology-specific false positives
Medical terminology validation
Drug names, biomarkers, diseases, genes, and trial identifiers
How to preserve legitimate biomedical language while detecting suspicious substitutions
Important Scope Clarification
There are two different problems here.
V1 problem: detect known tortured phrases
This is practical and defensible.
The system checks the abstract against a maintained dictionary or fingerprint list of known tortured phrases.
Later problem: discover new tortured phrases
This is harder and should not be treated as a confirmed V1 finding. It may require biomedical NLP, sentence embeddings, SciBERT, BioBERT, UMLS, MeSH, or expert validation.
For V1, the system should prioritize known, explainable matches.
Detection Approach
Recommended V1 pipeline:
XML abstract
↓
Extract original text
↓
Normalize matching copy
↓
Match against known tortured phrase fingerprints
↓
Apply word boundaries and punctuation handling
↓
Apply oncology false-positive controls
↓
Capture matched sentence
↓
Write finding to Excel
Output Needed
For each finding, capture:
Abstract ID
Matched tortured phrase
Expected scientific term
Sentence or excerpt
Abstract section
Fingerprint rule
Rule type
Evidence strength
Dictionary or fingerprint version
Recommended action: manual review
Why this matters
Editors should not receive just a score. They need to see the suspicious wording directly.
For example:


Abstract Text
Flagged Phrase
Expected Term
“The model uses a nervous network...”
nervous network
neural network

That is much more useful than saying “Nonsense score: 82.”

3. Template Detection
This detector looks for abstracts that appear to be built from the same underlying writing skeleton.
This is a cross-document problem. A single abstract may look normal on its own. The suspicious pattern appears only when many abstracts are compared together.
Current implementation note: the code compares only the files passed into a single pipeline run. It does not yet compare against a historical or external reference corpus.
The question is:
“Are multiple abstracts structurally derived from the same fill-in-the-blank template?”
Normal similarity
Some structure is expected in scientific abstracts:
Background
Methods
Results
Conclusion
That alone is not suspicious.
Suspicious similarity
The concern is when multiple abstracts have nearly identical sentence structure, with only variables changed.
Example:
A total of ___ patients with ___ were treated with ___.
The primary endpoint was ___.
The response rate was ___%.
No unexpected safety signals were observed.
If many unrelated abstracts follow the same structure with only disease names, drugs, numbers, or biomarkers swapped, that may require editorial review.
Research Focus
Research should focus on:
Document similarity
Section-level similarity
Sentence-level similarity
Skeleton or template extraction
Near-duplicate detection
Cluster detection
Metadata checks using authors, institutions, trial IDs, or disease areas
False positives from legitimate multi-site oncology trials
Recommended V1 Approach
At ASCO’s scale, around 6,000 abstracts, the system does not need an overly complex big-data architecture.
The first version can use a simpler and more explainable approach:
6000 XML abstracts
↓
Parse title and abstract sections
↓
Mask variables such as numbers, drugs, diseases, genes, percentages
↓
Create normalized abstract skeletons
↓
Compare abstracts against each other
↓
Calculate similarity scores
↓
Group highly similar abstracts into clusters
↓
Add metadata context
↓
Write clusters to Excel
What to avoid in V1
Avoid over-engineering the first version with unnecessary infrastructure such as:
Vector database
Approximate nearest neighbor search
Complex graph algorithms
Large-scale distributed processing
For 6,000 abstracts, pairwise comparison is manageable.
Output Needed
For each template cluster, capture:
Cluster ID
Abstract ID
Cluster size
Similarity score
Similar abstract IDs
Shared skeleton excerpt
Shared authors or institutions, if available
Cross-institution flag
Review priority
Recommended action: manual review
Why metadata matters
A group of similar abstracts from the same trial network may be legitimate.
A group of highly similar abstracts from unrelated authors or institutions may be more suspicious.
The detector should not say:
“This is fraudulent.”
It should say:
“These abstracts are structurally similar and may require review.”

4. Risk Aggregation
Once the individual detectors run, their outputs need to be combined into one editorial view.
The goal is to help ASCO staff prioritize review.
The system should not create a misconduct probability. It should create an editorial content-risk level.
Example
Detector
Finding
LLM response traces
No trace found
Tortured phrases
2 strong matches
Template detection
High similarity cluster
Overall content risk
High

The reviewer should be able to see both the final risk level and the evidence behind it.
Recommended Risk Levels
Risk Level
Meaning
High
Strong evidence from one major signal or multiple signals
Medium
One moderate signal or multiple weak/contextual signals
Low
Weak signal that may still be worth checking
None
No content integrity signal detected

Example Rules
High risk:
Strong LLM trace found
Distinctive tortured phrase found
Abstract belongs to a highly similar template cluster
Multiple detector types produce findings
Medium risk:
Context-dependent tortured phrase
Moderate template similarity
Weak LLM residue plus another signal
Low risk:
Weak markdown residue only
One low-confidence match
Similarity that may be explained by shared authors or trial structure
Output Needed
In the main Excel summary sheet, include:
Abstract ID
LLM trace flag
Tortured phrase flag
Template cluster flag
Finding count
Highest severity
Overall content risk
Review required
Review reason
