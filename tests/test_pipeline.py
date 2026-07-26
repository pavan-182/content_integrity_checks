from __future__ import annotations

import csv
import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from asco_integrity.detectors import built_in_llm_rules, build_tortured_rule_index, detect_llm_trace, detect_tortured_phrases, load_tortured_rules
from asco_integrity.models import Finding, ParsedRecord
from asco_integrity.pipeline import run_default_pipeline
from asco_integrity.template_detection import _candidate_pairs, _content_class, _similarity, build_normalized_text, build_skeleton_text, cluster_templates
from asco_integrity.validators import ContextValidator
from asco_integrity.validators.context_validator import _parse_validator_payload
from asco_integrity.xml_parser import parse_wiley_xml, parse_wiley_xml_records
from asco_integrity.utils import dedupe_records


def _write_temp_file(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
    return path


class PipelineTests(unittest.TestCase):
    def test_parser_splits_bundled_asco_sub_articles(self) -> None:
        path = self._temp_xml(
            """
            <article>
              <front><journal-meta><journal-title-group><journal-title>JCO</journal-title></journal-title-group></journal-meta>
                <article-meta>
                  <article-id custom-type="abstract-id">e1</article-id>
                  <title-group><article-title>Root abstract</article-title></title-group>
                  <contrib-group><contrib contrib-type="presenter"><name><surname>Root</surname><given-names>A</given-names></name></contrib><aff>Root Hospital</aff></contrib-group>
                  <copyright-year>2026</copyright-year>
                  <abstract><p><bold>e1</bold></p><p><bold>Background: </bold>Root background. <bold>Methods: </bold>Root methods.</p></abstract>
                  <kwd-group><kwd>root-keyword</kwd></kwd-group>
                </article-meta>
              </front>
              <sub-article article-type="meeting-abstract">
                <front-stub><journal-meta><journal-title-group><journal-title>JCO</journal-title></journal-title-group></journal-meta>
                  <article-meta>
                    <article-id custom-type="abstract-id">e2</article-id>
                    <article-categories><subj-group><subject>Breast Cancer—Metastatic</subject></subj-group></article-categories>
                    <title-group><article-title>Nested abstract</article-title></title-group>
                    <contrib-group><contrib contrib-type="author"><name><surname>Nested</surname><given-names>B</given-names></name></contrib><aff>Nested Hospital</aff></contrib-group>
                    <copyright-year>2026</copyright-year>
                    <abstract><p><bold>e2</bold></p><p><bold>Results: </bold>Nested results.<table-wrap><table><tr><td>REMOVE TABLE</td></tr></table></table-wrap> <bold>Conclusions: </bold>Nested conclusion.</p></abstract>
                    <kwd-group><kwd>nested-keyword</kwd></kwd-group>
                  </article-meta>
                </front-stub>
              </sub-article>
            </article>
            """
        )
        root, nested = parse_wiley_xml_records(path)
        self.assertEqual((root.record_id, nested.record_id), ("e1", "e2"))
        self.assertEqual((root.author_count, nested.author_count), (1, 1))
        self.assertEqual((root.affiliations, nested.affiliations), (["Root Hospital"], ["Nested Hospital"]))
        self.assertEqual((root.keywords, nested.keywords), (["root-keyword"], ["nested-keyword"]))
        self.assertEqual((root.publication_year, nested.publication_year), ("2026", "2026"))
        self.assertNotIn("REMOVE TABLE", nested.abstract_text)

    def test_template_similarity_is_tokenized_and_symmetric(self) -> None:
        left = "results " + "patient response " * 120
        right = "conclusion " + "patient response " * 120
        self.assertEqual(_similarity(left, right), _similarity(right, left))
        self.assertGreater(_similarity(left, right), 0.99)

    def test_template_detection_can_flag_shared_results_section(self) -> None:
        shared_results = "Twenty patients achieved durable response with improved survival and no unexpected adverse events during extended clinical follow up analysis."
        records = [
            ParsedRecord(
                source_file=f"{index}.xml",
                record_id=str(index),
                abstract_text=f"{opening} {shared_results}",
                abstract_sections=[
                    {"section": "Background", "text": opening},
                    {"section": "Results", "text": shared_results},
                ],
            )
            for index, opening in enumerate(
                (
                    "This unrelated introduction discusses a novel biomarker study in lung cancer with distinct methods and enrolment criteria.",
                    "A separate opening describes immunotherapy safety across another disease cohort using different eligibility and statistical assumptions.",
                )
            )
        ]
        clusters = cluster_templates(records)
        self.assertEqual(len(clusters), 2)
        self.assertTrue(all(row.template_pattern_type == "shared_section" for row in clusters))

    def test_ngram_candidates_recover_reordered_template(self) -> None:
        chunks = [
            "Patients received protocol treatment with prospective clinical assessment and standardized longitudinal outcome collection.",
            "Tumor response and safety endpoints were evaluated by independent reviewers using predefined analysis criteria.",
            "The findings support additional validation in larger diverse populations and future randomized clinical studies.",
        ]
        records = [
            ParsedRecord("a.xml", record_id="A", abstract_text=" ".join(chunks)),
            ParsedRecord("b.xml", record_id="B", abstract_text=" ".join((chunks[1], chunks[0], chunks[2]))),
        ]
        skeletons = {record.record_id: build_skeleton_text(record) for record in records}
        normalized = {record.record_id: build_normalized_text(record) for record in records}
        self.assertIn(("A", "B"), _candidate_pairs(records, skeletons, normalized))
        self.assertEqual([row.template_pattern_type for row in cluster_templates(records)], ["reordered_or_partial_template"] * 2)

    def test_content_class_preserves_valid_short_abstracts(self) -> None:
        short = ParsedRecord("short.xml", record_id="short", abstract_text="A concise but valid clinical abstract reports treatment outcomes in patients.")
        boilerplate = ParsedRecord("na.xml", record_id="na", abstract_text="N/A N/A N/A")
        self.assertEqual(_content_class(short, build_skeleton_text(short)), "valid_short")
        self.assertEqual(_content_class(boilerplate, build_skeleton_text(boilerplate)), "empty_or_unusable")

    def test_trial_ids_are_masked_before_gene_names(self) -> None:
        record = ParsedRecord("trial.xml", abstract_text="NCT12345678 evaluated BRCA1.")
        self.assertEqual(build_skeleton_text(record), "<TRIAL_ID> evaluated <GENE>.")

    def test_dedupe_keeps_distinct_content_from_same_source(self) -> None:
        records, warnings = dedupe_records([
            ParsedRecord("bundle.xml", record_id="same", abstract_text="First abstract"),
            ParsedRecord("bundle.xml", record_id="same", abstract_text="Different abstract"),
        ])
        self.assertEqual(len(records), 2)
        self.assertEqual(len({record.record_id for record in records}), 2)
        self.assertEqual(warnings[0]["reason"], "record_id_collision_disambiguated")

    def test_parser_handles_article_and_article_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            article_path = _write_temp_file(
                temp_dir,
                "article.xml",
                """
                <article article-type="Original Research">
                  <front>
                    <journal-meta>
                      <journal-title-group><journal-title>Test Journal</journal-title></journal-title-group>
                    </journal-meta>
                    <article-meta>
                      <article-id pub-id-type="manuscript">TEST-1</article-id>
                      <article-title>Sample Article</article-title>
                      <abstract>
                        <p><b>Background:</b> Something happened. <b>Methods:</b> We did tests. <b>Results:</b> Great. <b>Conclusion:</b> Works.</p>
                      </abstract>
                      <kwd-group><kwd>alpha</kwd><kwd>beta</kwd></kwd-group>
                      <contrib-group>
                        <contrib contrib-type="author"><name><given-names>Ada</given-names><surname>Lovelace</surname></name></contrib>
                      </contrib-group>
                      <aff id="aff1"><institution>Test Institute</institution></aff>
                      <history><date date-type="accepted"><year>2026</year></date></history>
                    </article-meta>
                  </front>
                </article>
                """,
            )
            article_set_path = _write_temp_file(
                temp_dir,
                "article_set.xml",
                """
                <article_set dtd_version="4.28.11">
                  <article ms_no="MS-1" tracking_no="T-1">
                    <journal>
                      <full_journal_title>Another Journal</full_journal_title>
                    </journal>
                    <publication_type>Research Article</publication_type>
                    <article_title>Another Sample</article_title>
                    <abstract>
                      <p>Background: First. Methods: Second. Results: Third. Conclusion: Fourth.</p>
                    </abstract>
                    <author_list>
                      <author>
                        <first_name>Grace</first_name><last_name>Hopper</last_name>
                        <affiliation><inst>Example University</inst></affiliation>
                      </author>
                    </author_list>
                    <history><date date-type="accepted"><year>2025</year></date></history>
                  </article>
                </article_set>
                """,
            )
            article_record = parse_wiley_xml(article_path)
            article_set_record = parse_wiley_xml(article_set_path)
            self.assertEqual(article_record.record_id, "TEST-1")
            self.assertEqual(article_record.journal, "Test Journal")
            self.assertTrue(article_record.structured_abstract)
            self.assertGreaterEqual(article_record.abstract_section_count, 4)
            self.assertEqual(article_set_record.record_id, "MS-1")
            self.assertEqual(article_set_record.journal, "Another Journal")
            self.assertEqual(article_set_record.publication_year, "2025")

    def test_llm_trace_detector_finds_synthetic_trace(self) -> None:
        record = parse_wiley_xml(
            self._temp_xml(
                """
                <article article-type="Original Research">
                  <front>
                    <journal-meta><journal-title-group><journal-title>Test</journal-title></journal-title-group></journal-meta>
                    <article-meta>
                      <article-id pub-id-type="manuscript">TEST-2</article-id>
                      <article-title>As an AI language model, this is a test</article-title>
                      <abstract><p>As an AI language model, I cannot provide medical advice.</p></abstract>
                      <history><date date-type="accepted"><year>2026</year></date></history>
                    </article-meta>
                  </front>
                </article>
                """
            )
        )
        findings = detect_llm_trace(record, built_in_llm_rules())
        self.assertTrue(any(finding.rule_id == "LLM-001" for finding in findings))
        self.assertTrue(any(finding.rule_id == "LLM-007" for finding in findings))

    def test_llm_trace_detector_finds_research_backed_weak_residue(self) -> None:
        record = ParsedRecord(
            source_file="sample.xml",
            record_id="TEST-WEAK",
            abstract_text=(
                "It is essential to note that the user requested a revision. "
                "### Revised abstract ---"
            ),
        )

        rule_ids = {finding.rule_id for finding in detect_llm_trace(record, built_in_llm_rules())}

        self.assertTrue({"LLM-025", "LLM-026", "LLM-027", "LLM-028"} <= rule_ids)

    def test_tortured_phrase_detector_finds_synthetic_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            dictionary_path = _write_temp_file(
                temp_dir,
                "tortured.csv",
                """
                Fingerprint - Tortured Phrase,Expected Text,Nb Retrieved Papers
                "nervous network","neural network","10"
                "mechanical learning","machine learning","12"
                """,
            )
            rules = load_tortured_rules(dictionary_path)
            record = parse_wiley_xml(
                self._temp_xml(
                    """
                    <article article-type="Original Research">
                      <front>
                        <journal-meta><journal-title-group><journal-title>Test</journal-title></journal-title-group></journal-meta>
                        <article-meta>
                          <article-id pub-id-type="manuscript">TEST-3</article-id>
                          <article-title>Mechanical learning is surprising</article-title>
                          <abstract><p>The model uses a nervous network.</p></abstract>
                          <history><date date-type="accepted"><year>2026</year></date></history>
                        </article-meta>
                      </front>
                    </article>
                    """
                )
            )
            findings = detect_tortured_phrases(record, rules, build_tortured_rule_index(rules))
            self.assertTrue(any(finding.matched_text.lower() == "nervous network" for finding in findings))
            self.assertTrue(any(finding.matched_text.lower() == "mechanical learning" for finding in findings))

    def test_tortured_phrase_detector_honors_dictionary_queries_and_sentence_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_str:
            dictionary_path = _write_temp_file(
                Path(temp_dir_str),
                "tortured.csv",
                '''
                Fingerprint - Tortured Phrase,Expected Text,Nb Retrieved Papers
                """surface region"" AND ""surface area""","surface area","1"
                """corridor impact"" AND (""magnetic"" OR ""voltage"")","Hall effect","1"
                """magnetic cell separation"" NOT ""with MACS""","magnetic-activated cell sorting","1"
                """very long tortured phrase""","expected phrase","1"
                """multi token tortured phrase""","expected phrase","1"
                ''',
            )
            rules = load_tortured_rules(dictionary_path)
            record = ParsedRecord(
                source_file="sample.xml",
                record_id="REC-1",
                title="A corridor impact from a voltage sensor",
                abstract_text=(
                    "The surface region was measured without the required context. "
                    "Magnetic cell separation with MACS was used. "
                    "A very long tortured. Phrase must not cross a sentence. "
                    "This multi-token tortured phrase must still be retrieved."
                ),
            )

            findings = detect_tortured_phrases(record, rules, build_tortured_rule_index(rules))

            self.assertEqual(
                [finding.matched_text.lower() for finding in findings],
                ["corridor impact", "multi-token tortured phrase"],
            )
            self.assertTrue(all(rule.rule_id.startswith("TP-") and len(rule.rule_id) == 15 for rule in rules))

    def test_template_detection_clusters_synthetic_abstracts(self) -> None:
        record_a = parse_wiley_xml(
            self._temp_xml(
                """
                <article article-type="Original Research">
                  <front>
                    <journal-meta><journal-title-group><journal-title>Test</journal-title></journal-title-group></journal-meta>
                    <article-meta>
                      <article-id pub-id-type="manuscript">TEST-4</article-id>
                      <article-title>Template A</article-title>
                      <abstract><p>Background: A total of 10 patients received treatment. Methods: The primary endpoint was response rate. Results: 80%. Conclusion: Positive.</p></abstract>
                      <history><date date-type="accepted"><year>2026</year></date></history>
                    </article-meta>
                  </front>
                </article>
                """
            )
        )
        record_b = parse_wiley_xml(
            self._temp_xml(
                """
                <article article-type="Original Research">
                  <front>
                    <journal-meta><journal-title-group><journal-title>Test</journal-title></journal-title-group></journal-meta>
                    <article-meta>
                      <article-id pub-id-type="manuscript">TEST-5</article-id>
                      <article-title>Template B</article-title>
                      <abstract><p>Background: A total of 20 patients received treatment. Methods: The primary endpoint was response rate. Results: 81%. Conclusion: Positive.</p></abstract>
                      <history><date date-type="accepted"><year>2026</year></date></history>
                    </article-meta>
                  </front>
                </article>
                """
            )
        )
        clusters = cluster_templates([record_a, record_b], similarity_threshold=0.8)
        self.assertEqual(len(clusters), 2)
        self.assertTrue(all(cluster.template_cluster_id.startswith("TPL-") for cluster in clusters))

    def test_template_clusters_appear_in_integrity_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            input_dir = temp_dir / "xmls"
            output_dir = temp_dir / "outputs"
            input_dir.mkdir()
            _write_temp_file(
                input_dir,
                "cluster_a.xml",
                """
                <article article-type="Original Research">
                  <front>
                    <journal-meta><journal-title-group><journal-title>Test Journal</journal-title></journal-title-group></journal-meta>
                    <article-meta>
                      <article-id pub-id-type="manuscript">TPL-A</article-id>
                      <article-title>Template A</article-title>
                      <abstract><p>Background: A total of 10 patients received treatment. Methods: The primary endpoint was response rate. Results: 80%. Conclusion: Positive.</p></abstract>
                      <history><date date-type="accepted"><year>2026</year></date></history>
                    </article-meta>
                  </front>
                </article>
                """,
            )
            _write_temp_file(
                input_dir,
                "cluster_b.xml",
                """
                <article article-type="Original Research">
                  <front>
                    <journal-meta><journal-title-group><journal-title>Test Journal</journal-title></journal-title-group></journal-meta>
                    <article-meta>
                      <article-id pub-id-type="manuscript">TPL-B</article-id>
                      <article-title>Template B</article-title>
                      <abstract><p>Background: A total of 20 patients received treatment. Methods: The primary endpoint was response rate. Results: 81%. Conclusion: Positive.</p></abstract>
                      <history><date date-type="accepted"><year>2026</year></date></history>
                    </article-meta>
                  </front>
                </article>
                """,
            )
            dict_path = temp_dir / "dict.csv"
            dict_path.write_text(
                "Fingerprint - Tortured Phrase,Expected Text,Nb Retrieved Papers\n\"nervous network\",\"neural network\",\"1\"\n",
                encoding="utf-8",
            )

            result = run_default_pipeline(input_dir=input_dir, tortured_dictionary_path=dict_path, output_dir=output_dir)

            with result.output_paths["findings_csv"].open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

            template_rows = [row for row in rows if row.get("detector_type") == "template_cluster"]
            self.assertTrue(template_rows)
            self.assertTrue(all(row.get("category") == "template_cluster" for row in template_rows))
            self.assertTrue(all(row.get("template_cluster_id", "").startswith("TPL-") for row in template_rows))
            self.assertTrue(all(row.get("shared_skeleton_excerpt") for row in template_rows))

    def test_full_pipeline_creates_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            input_dir = temp_dir / "xmls"
            output_dir = temp_dir / "outputs"
            input_dir.mkdir()
            _write_temp_file(
                input_dir,
                "sample.xml",
                """
                <article article-type="Original Research">
                  <front>
                    <journal-meta><journal-title-group><journal-title>Test Journal</journal-title></journal-title-group></journal-meta>
                    <article-meta>
                      <article-id pub-id-type="manuscript">TEST-6</article-id>
                      <article-title>Simple article</article-title>
                      <abstract><p>Background: Sample. Methods: Sample. Results: Sample. Conclusion: Sample.</p></abstract>
                      <history><date date-type="accepted"><year>2026</year></date></history>
                    </article-meta>
                  </front>
                </article>
                """,
            )
            dict_path = temp_dir / "dict.csv"
            dict_path.write_text(
                "Fingerprint - Tortured Phrase,Expected Text,Nb Retrieved Papers\n\"nervous network\",\"neural network\",\"1\"\n",
                encoding="utf-8",
            )
            result = run_default_pipeline(input_dir=input_dir, tortured_dictionary_path=dict_path, output_dir=output_dir)
            self.assertTrue(result.output_paths["workbook"].exists())
            self.assertTrue(result.output_paths["parsed_jsonl"].exists())
            self.assertTrue(result.output_paths["findings_csv"].exists())

    def test_validator_marks_bad_json_uncertain(self) -> None:
        class BrokenClient:
            def complete(self, *, system: str, user: str, max_tokens: int = 150, temperature: float = 0.0) -> str:
                return "not-json"

        validator = ContextValidator(client=BrokenClient())
        finding = Finding(
            finding_id="FND-00001",
            record_id="REC-1",
            source_file="sample.xml",
            detector_type="tortured_phrase",
            category="tortured_phrase",
            matched_text="nervous network",
            evidence_snippet="The model uses a nervous network.",
            section_or_field="abstract_text",
            severity="medium",
            confidence=0.87,
            rule_id="TP-00001",
            expected_term="neural network",
        )
        result = validator.validate(finding)

        self.assertEqual(result.status, "uncertain")
        self.assertEqual(result.reason, "Validator response could not be parsed.")
        self.assertEqual(result.finding_id, "FND-00001")

    def test_validator_parses_wrapped_json(self) -> None:
        parsed = _parse_validator_payload('Result: {"status":"confirmed","reason":"Trace confirmed."} done')
        self.assertEqual(parsed["status"], "confirmed")

    def test_pipeline_validation_flag_populates_finding_metadata(self) -> None:
        class StubClient:
            def __init__(self) -> None:
                self.max_tokens_requested: int | None = None

            def complete(self, *, system: str, user: str, max_tokens: int = 150, temperature: float = 0.0) -> str:
                self.max_tokens_requested = max_tokens
                payload = {"status": "rejected", "reason": "Standard terminology, not a plausible substitution."}
                return f"```json\n{json.dumps(payload)}\n```"

        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            input_dir = temp_dir / "xmls"
            output_dir = temp_dir / "outputs"
            input_dir.mkdir()
            _write_temp_file(
                input_dir,
                "sample.xml",
                """
                <article article-type="Original Research">
                  <front>
                    <journal-meta><journal-title-group><journal-title>Test Journal</journal-title></journal-title-group></journal-meta>
                    <article-meta>
                      <article-id pub-id-type="manuscript">TEST-7</article-id>
                      <article-title>Validator example</article-title>
                      <abstract><p>The model uses a nervous network in the background section.</p></abstract>
                      <history><date date-type="accepted"><year>2026</year></date></history>
                    </article-meta>
                  </front>
                </article>
                """,
            )
            dict_path = temp_dir / "dict.csv"
            dict_path.write_text(
                "Fingerprint - Tortured Phrase,Expected Text,Nb Retrieved Papers\n\"nervous network\",\"neural network\",\"1\"\n",
                encoding="utf-8",
            )

            stub_client = StubClient()
            with patch("asco_integrity.pipeline.build_gpt_oss_client", return_value=stub_client):
                result = run_default_pipeline(
                    input_dir=input_dir,
                    tortured_dictionary_path=dict_path,
                    output_dir=output_dir,
                    validate_llm=True,
                )

            tortured_findings = [finding for finding in result.findings if finding.detector_type == "tortured_phrase"]
            self.assertTrue(tortured_findings)
            self.assertEqual(stub_client.max_tokens_requested, 2048)
            self.assertEqual(tortured_findings[0].validation_status, "rejected")
            self.assertEqual(
                tortured_findings[0].validated_by,
                "gpt-oss-20b:context_validator_v2",
            )

            workbook = load_workbook(result.output_paths["workbook"], read_only=True)
            ws = workbook["Integrity Findings"]
            headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
            self.assertLess(headers.index("confidence"), headers.index("validation_status"))
            self.assertLess(headers.index("validation_status"), headers.index("validation_reason"))
            self.assertLess(headers.index("validation_reason"), headers.index("validated_by"))
            self.assertLess(headers.index("validated_by"), headers.index("rule_id"))

    def test_default_pipeline_leaves_validation_columns_blank(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            input_dir = temp_dir / "xmls"
            output_dir = temp_dir / "outputs"
            input_dir.mkdir()
            _write_temp_file(
                input_dir,
                "sample.xml",
                """
                <article article-type="Original Research">
                  <front>
                    <journal-meta><journal-title-group><journal-title>Test Journal</journal-title></journal-title-group></journal-meta>
                    <article-meta>
                      <article-id pub-id-type="manuscript">TEST-8</article-id>
                      <article-title>Default run</article-title>
                      <abstract><p>The model uses a nervous network in the abstract to ensure one finding exists.</p></abstract>
                      <history><date date-type="accepted"><year>2026</year></date></history>
                    </article-meta>
                  </front>
                </article>
                """,
            )
            dict_path = temp_dir / "dict.csv"
            dict_path.write_text(
                "Fingerprint - Tortured Phrase,Expected Text,Nb Retrieved Papers\n\"nervous network\",\"neural network\",\"1\"\n",
                encoding="utf-8",
            )

            result = run_default_pipeline(input_dir=input_dir, tortured_dictionary_path=dict_path, output_dir=output_dir)

            self.assertTrue(all(finding.validation_status == "" for finding in result.findings))
            self.assertTrue(all(finding.validation_reason == "" for finding in result.findings))
            self.assertTrue(all(finding.validated_by == "" for finding in result.findings))

    def _temp_xml(self, content: str) -> Path:
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xml")
        path = Path(temp.name)
        temp.close()
        path.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
