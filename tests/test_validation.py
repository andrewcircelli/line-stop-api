"""Local tests for validation only. No AWS needed.

Run:  python3.12 -m unittest discover tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from validation import validate_event

GOOD = {"site_id": "site-a", "station_id": "weld-3", "event_type": "defect", "severity": 2}


class ValidateEventTests(unittest.TestCase):
    def test_good_event_has_no_errors(self):
        self.assertEqual(validate_event(GOOD), [])

    def test_good_event_with_note(self):
        self.assertEqual(validate_event({**GOOD, "note": "burr on edge"}), [])

    def test_missing_field_is_named(self):
        body = {k: v for k, v in GOOD.items() if k != "severity"}
        self.assertEqual(validate_event(body), ["missing field: severity"])

    def test_not_an_object(self):
        self.assertEqual(validate_event(["site-a"]), ["body must be a JSON object"])

    def test_unknown_event_type(self):
        self.assertIn("event_type must be one of: defect, stoppage",
                      validate_event({**GOOD, "event_type": "lunch"}))

    def test_severity_out_of_range(self):
        self.assertIn("severity must be a whole number from 1 to 5",
                      validate_event({**GOOD, "severity": 9}))

    def test_severity_true_is_rejected(self):
        self.assertIn("severity must be a whole number from 1 to 5",
                      validate_event({**GOOD, "severity": True}))

    def test_blank_site_id(self):
        self.assertIn("site_id must be a non-empty string",
                      validate_event({**GOOD, "site_id": "  "}))

    def test_unknown_field(self):
        self.assertIn("unknown fields: priority",
                      validate_event({**GOOD, "priority": "high"}))


if __name__ == "__main__":
    unittest.main()
