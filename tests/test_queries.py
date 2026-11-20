import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import queries

class SearchQueryForTaskTests(unittest.TestCase):
	def test_known_task_uses_short_path_query(self):
		task = next(iter(queries.KNOWN_TASKS))
		expected = queries.KNOWN_TASKS[task]

		with (
			mock.patch.dict(os.environ, {"QUERY_SOURCE": queries.TRENDS}),
			mock.patch("query_sources.query_from_task_description") as trends_backend,
			mock.patch("llm_utils.get_search_query_from_task_description") as llm_backend,
		):
			self.assertEqual(queries.search_query_for_task(task), expected)
			trends_backend.assert_not_called()
			llm_backend.assert_not_called()

		with (
			mock.patch.dict(os.environ, {"QUERY_SOURCE": queries.LLM}),
			mock.patch("query_sources.query_from_task_description") as trends_backend,
			mock.patch("llm_utils.get_search_query_from_task_description") as llm_backend,
		):
			self.assertEqual(queries.search_query_for_task(task), expected)
			trends_backend.assert_not_called()
			llm_backend.assert_not_called()

	def test_unknown_task_uses_configured_backend(self):
		with (
			mock.patch.dict(os.environ, {"QUERY_SOURCE": queries.TRENDS}),
			mock.patch(
				"query_sources.query_from_task_description",
				return_value="<query from configured backend>",
			),
		):
			self.assertEqual(
				queries.search_query_for_task("an unknown task"),
				"<query from configured backend>",
			)