from __future__ import annotations

import unittest

from content_integrity.utils import split_sentences


class SplitSentencesTests(unittest.TestCase):
    def test_common_biomedical_abbreviations_do_not_fracture_sentences(self) -> None:
        text = (
            "Overall response rate was 45% (Fig. 2), vs. placebo (e.g. cohort A, "
            "i.v. dosing, no. 12 patients)."
        )
        self.assertEqual(split_sentences(text), [text])

    def test_decimal_numbers_do_not_fracture_sentences(self) -> None:
        text = "The response rate was 5.2% in the treatment arm."
        self.assertEqual(split_sentences(text), [text])

    def test_real_sentence_boundaries_still_split(self) -> None:
        text = "The population (N=120) was enrolled. The response rate was 45%."
        self.assertEqual(
            split_sentences(text),
            ["The population (N=120) was enrolled.", "The response rate was 45%."],
        )

    def test_empty_and_unpunctuated_text(self) -> None:
        self.assertEqual(split_sentences(""), [])
        self.assertEqual(split_sentences("A fragment without punctuation"), ["A fragment without punctuation"])


if __name__ == "__main__":
    unittest.main()
