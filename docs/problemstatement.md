Business Problem
Customer
American Society of Clinical Oncology (ASCO)
Product
TNQTech Integrity Central
Dataset
~6,000 abstracts from the 2025 ASCO Annual Meeting
XML files
Includes both accepted and rejected abstracts

What ASCO's Actual Problem Is
ASCO receives thousands of conference abstracts every year.
Manually identifying suspicious submissions is difficult because reviewers are focused on scientific merit, not publication integrity.
For this initiative, the focus is on automatically identifying content integrity issues within submitted abstracts that may require further editorial investigation.
The goal is not to automatically reject abstracts, but to produce an integrity report highlighting potentially suspicious submissions for human review.
Objective
Develop an automated content integrity screening system that analyzes approximately 6,000 conference abstracts and produces a consolidated report identifying potential text-level integrity risks, enabling ASCO staff to efficiently review suspicious submissions before editorial decisions.
Functional Requirements
Content Integrity
Requirement
Purpose
Nonsense / tortured phrases
Detect meaningless or machine-like wording
LLM response traces
Detect leftover prompts or model artifacts
Templating
Identify abstracts derived from the same template

These are text-level integrity indicators.

Expected Deliverable
ASCO does not want individual reports.
They want one consolidated Excel workbook containing:
every abstract
every content integrity check
filterable columns
sortable results
pivot-ready format
Essentially, an editorial dashboard exported to Excel.

Scope Clarification
Initially, TNQTech considered multiple integrity dimensions.
For this project, the scope is limited to Content Integrity and focuses on:
nonsense / tortured phrases
AI-generated text detection
LLM response traces
template detection

What is NOT Being Asked
ASCO is not asking this system to evaluate:
novelty
methodology
scientific significance
scientific quality
acceptance recommendations
These remain the responsibility of reviewers and editors.
The system performs content integrity screening, not peer review.

Inputs and Outputs
Inputs
XML abstracts
Processing
Content analysis
Cross-abstract comparison
Risk aggregation
Output
Excel report containing:
Abstract ID
Content integrity indicators
Overall content risk
Filterable columns
Pivot tables

Implicit Product Requirements
The system should support:
Cross-document analysis across ~6,000 abstracts
AI-generated text detection
Detection of LLM response artifacts
Detection of nonsense / tortured phrases
Template and text similarity detection
Aggregation of multiple content integrity signals into a single risk assessment
This is a content integrity triage system designed to prioritize abstracts for editorial review.

Concise Product Problem Statement
ASCO needs TNQTech Integrity Central to automatically screen approximately 6,000 conference abstracts for content integrity risks, identify suspicious textual patterns across submissions, and generate a consolidated, filterable Excel report that enables editorial staff to prioritize manual investigation while preserving human decision-making for acceptance or rejection
