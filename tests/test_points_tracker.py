import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from points_tracker import PointsTracker


class PointsEarnedCalculationTests(unittest.TestCase):
    def test_uses_today_delta_when_total_balance_is_stale(self):
        earned = PointsTracker.calculate_points_earned(
            start_balance=6305,
            end_balance=6305,
            start_today_points=80,
            end_today_points=110,
        )

        self.assertEqual(earned, 30)

    def test_uses_balance_delta_when_today_points_are_unavailable(self):
        earned = PointsTracker.calculate_points_earned(
            start_balance=6305,
            end_balance=6315,
            start_today_points=None,
            end_today_points=None,
        )

        self.assertEqual(earned, 10)


if __name__ == "__main__":
    unittest.main()
