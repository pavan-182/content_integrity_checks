from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asco_integrity.detectors import built_in_llm_rules, detect_llm_trace
from asco_integrity.xml_parser import parse_xml


class AuthorExclusionTests(unittest.TestCase):
    def _parse(self, abstract_content: str) -> tuple[object, list[object]]:
        xml = f"""
        <article article-type="meeting-abstract">
          <front><article-meta>
            <article-id pub-id-type="manuscript">EXCLUSION-1</article-id>
            <title-group><article-title>Reference exclusion test</article-title></title-group>
            <contrib-group><contrib contrib-type="author"><name><surname>Chen</surname><given-names>J</given-names></name></contrib></contrib-group>
            <aff>Example Cancer Centre</aff>
            <abstract><p>{abstract_content}</p></abstract>
            <ref-list><ref><mixed-citation>As an AI language model, this reference is synthetic.</mixed-citation></ref></ref-list>
          </article-meta></front>
        </article>
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.xml"
            path.write_text(xml, encoding="utf-8")
            record = parse_xml(path)
        return record, detect_llm_trace(record, built_in_llm_rules())

    def test_author_affiliation_and_reference_text_are_excluded(self) -> None:
        record, findings = self._parse("Patients had durable clinical responses.")

        self.assertFalse(findings)
        self.assertEqual(record.excluded_sections, ["affiliations", "authors", "references"])
        self.assertNotIn("AI language model", record.raw_text)

    def test_same_trace_in_abstract_is_detected(self) -> None:
        _, findings = self._parse("As an AI language model, I cannot provide medical advice.")

        self.assertTrue(any(finding.rule_id == "LLM-001" for finding in findings))


if __name__ == "__main__":
    unittest.main()
