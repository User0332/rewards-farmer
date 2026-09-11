"""Tests for where a simulated move actually leaves the pointer (#74).

The move is driven by two things that both stop short of the target. The path
normalises elapsed time onto the sigmoid's input range with a 4.5 multiplier,
and logistic_sigmoid(4.5) is 0.978, so the bezier is never evaluated at its
endpoint. On top of that move_mouse sampled on the wall clock with a strict
`< end_time`, so it never asked for a sample at movement_time either. The
pointer was left a few pixels short of the point move_to_element picked, on
every move, which costs clicks on the smallest controls and reports nothing
when it does.

None of them need a browser.

	python -m unittest discover -s tests
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mouse_trajectory
from mouse_trajectory import (
	MouseUtils,
	get_final_path_from_real_time,
	logistic_sigmoid,
)

# Long enough to take several samples, short enough that the suite stays quick.
BRIEF_MOVE = 0.05


class RecordingActionBuilder:
	"""Stands in for ActionBuilder and keeps every location it is asked for."""

	def __init__(self, driver, duration=0):
		self.locations = driver.locations
		self.pointer_action = self

	def move_to_location(self, x, y):
		self._pending = (x, y)
		return self

	def perform(self):
		self.locations.append(self._pending)


class FakeMouseDriver:
	"""Answers the one script move_mouse runs and records nothing else."""

	def __init__(self):
		self.locations = []

	def execute_script(self, script, *args):
		if "innerWidth" in script:
			return [1920, 1080]

		return None


def make_mouse_utils(driver):
	"""A MouseUtils without the cursor visualization its __init__ injects."""
	mouse = MouseUtils.__new__(MouseUtils)
	mouse.driver = driver
	mouse.fallback_init_pos = (0, 0)

	return mouse


class PathEndsOnTheTarget(unittest.TestCase):
	def test_a_sample_at_movement_time_is_the_target(self):
		random.seed(7)
		start, end = (100, 100), (400, 300)
		path = get_final_path_from_real_time(1.0, start, end)

		self.assertEqual(path(1.0), end)

	def test_a_sample_before_the_end_still_follows_the_path(self):
		"""The endpoint must come from reaching the end, not from short-circuiting."""
		random.seed(7)
		start, end = (100, 100), (400, 300)
		path = get_final_path_from_real_time(1.0, start, end)

		midpoint = path(0.5)

		self.assertNotEqual(midpoint, end)
		self.assertNotEqual(midpoint, start)

	def test_the_sigmoid_alone_does_not_reach_the_end(self):
		"""Why the branch above exists. If 4.5 ever changes, this says what it cost."""
		self.assertLess(logistic_sigmoid(4.5), 1.0)
		self.assertAlmostEqual(logistic_sigmoid(4.5), 0.978026, places=6)


class MoveMouseLandsOnTheTarget(unittest.TestCase):
	def setUp(self):
		self._real_builder = mouse_trajectory.ActionBuilder
		mouse_trajectory.ActionBuilder = RecordingActionBuilder

	def tearDown(self):
		mouse_trajectory.ActionBuilder = self._real_builder

	def test_the_last_move_is_to_the_target(self):
		random.seed(11)
		driver = FakeMouseDriver()
		mouse = make_mouse_utils(driver)

		start, end = (200, 200), (600, 450)
		path = get_final_path_from_real_time(BRIEF_MOVE, start, end)

		mouse.move_mouse(BRIEF_MOVE, path, visualize=False)

		self.assertEqual(driver.locations[-1], end)

	def test_the_move_is_sampled_along_the_way_not_jumped(self):
		random.seed(11)
		driver = FakeMouseDriver()
		mouse = make_mouse_utils(driver)

		start, end = (200, 200), (600, 450)
		path = get_final_path_from_real_time(BRIEF_MOVE, start, end)

		mouse.move_mouse(BRIEF_MOVE, path, visualize=False)

		self.assertGreater(len(driver.locations), 1)
		self.assertNotEqual(driver.locations[0], end)

	def test_a_move_with_no_time_left_still_lands_on_the_target(self):
		"""A zero-length move must not leave the pointer wherever it started."""
		random.seed(11)
		driver = FakeMouseDriver()
		mouse = make_mouse_utils(driver)

		start, end = (200, 200), (600, 450)
		path = get_final_path_from_real_time(0.0, start, end)

		mouse.move_mouse(0.0, path, visualize=False)

		self.assertTrue(driver.locations, "the pointer was never moved at all")
		self.assertEqual(driver.locations[-1], end)


if __name__ == "__main__":
	unittest.main()
