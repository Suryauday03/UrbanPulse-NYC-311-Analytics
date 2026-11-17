"""Small repeatable tests for Urban Pulse cleaning rules.

Run from the project root:
    python -m unittest discover -s tests -v
"""
from pathlib import Path
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import clean_data  # noqa: E402


class DataQualityRuleTests(unittest.TestCase):
    def test_duplicate_request_ids_are_flagged_not_removed(self):
        frame = pd.DataFrame({"unique_key": pd.Series(["A", "A", "B"], dtype="string")})
        result = clean_data.check_duplicates(frame)
        self.assertEqual(len(result), 3)
        self.assertEqual(int(result["duplicate_request_id"].sum()), 2)

    def test_resolution_duration_excludes_open_and_negative_cases(self):
        frame = pd.DataFrame({
            "created_date": pd.to_datetime([
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
            ]),
            "closed_date": pd.to_datetime([
                "2026-01-01 02:00:00",
                "2025-12-31 23:00:00",
                None,
            ]),
        })
        result = clean_data.create_derived_features(frame)
        self.assertEqual(result.loc[0, "resolution_hours"], 2.0)
        self.assertTrue(pd.isna(result.loc[1, "resolution_hours"]))
        self.assertTrue(pd.isna(result.loc[2, "resolution_hours"]))
        self.assertTrue(bool(result.loc[1, "closed_before_created"]))
        self.assertTrue(bool(result.loc[0, "is_closed"]))
        self.assertFalse(bool(result.loc[2, "is_closed"]))

    def test_coordinate_validation_distinguishes_incomplete_from_invalid(self):
        frame = pd.DataFrame({
            "latitude": [40.7, 95.0, None],
            "longitude": [-73.9, -73.9, -73.9],
            "invalid_latitude": [False, False, False],
            "invalid_longitude": [False, False, False],
        })
        result = clean_data.validate_coordinates(frame)
        self.assertFalse(bool(result.loc[0, "invalid_coordinates"]))
        self.assertTrue(bool(result.loc[1, "invalid_coordinates"]))
        self.assertTrue(bool(result.loc[2, "incomplete_coordinates"]))
        self.assertFalse(bool(result.loc[2, "invalid_coordinates"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
