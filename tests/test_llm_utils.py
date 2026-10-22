"""Exercise cold-model recovery without contacting any model provider."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import llm_utils


class LocalLlmRequestTests(unittest.TestCase):
	def setUp(self):
		self.env = mock.patch.dict(os.environ, {}, clear=True)
		self.env.start()
		self.addCleanup(self.env.stop)
		self.provider = mock.patch.object(llm_utils, "DEFAULT_LLM_PROVIDER", "local")
		self.provider.start()
		self.addCleanup(self.provider.stop)
		self.post = mock.patch.object(llm_utils.requests, "post", side_effect=AssertionError("Unpatched model request forbidden"))
		self.request = self.post.start()
		self.addCleanup(self.post.stop)

	def _response(self):
		response = mock.Mock()
		response.json.return_value = {"choices": [{"message": {"content": "test query"}}]}
		self.request.side_effect = None
		self.request.return_value = response
		return response

	def test_default_timeout_is_180_seconds_for_cold_local_model(self):
		response = self._response()
		messages = [{"role": "user", "content": "synthetic"}]
		self.assertEqual(llm_utils.get_llm_response(messages), "test query")
		self.assertEqual(self.request.call_args.kwargs["timeout"], 180.0)
		self.assertEqual(self.request.call_args.args[0], "http://localhost:11434/v1/chat/completions")
		self.assertEqual(self.request.call_args.kwargs["json"]["messages"], messages)
		response.raise_for_status.assert_called_once_with()

	def test_explicit_timeout_and_model_still_override_defaults(self):
		self._response()
		os.environ.update(LLM_REQUEST_TIMEOUT_SECONDS="12.5", LOCAL_LLM_MODEL="synthetic-model")
		llm_utils.get_llm_response([])
		self.assertEqual(self.request.call_args.kwargs["timeout"], 12.5)
		self.assertEqual(self.request.call_args.kwargs["json"]["model"], "synthetic-model")


class NonemptyLlmRetryTests(unittest.TestCase):
	def setUp(self):
		self.request_patch = mock.patch.object(llm_utils, "get_llm_response", side_effect=AssertionError("Unpatched model request forbidden"))
		self.request = self.request_patch.start()
		self.addCleanup(self.request_patch.stop)

	def test_failed_call_counts_as_attempt_and_next_response_can_succeed(self):
		self.request.side_effect = [llm_utils.requests.Timeout("synthetic cold load"), "usable query"]
		messages = [{"role": "user", "content": "synthetic"}]
		with self.assertLogs(llm_utils.logger, level="WARNING") as logs:
			self.assertEqual(llm_utils.get_nonempty_llm_response(messages), "usable query")
		self.assertEqual(self.request.call_args_list, [mock.call(messages), mock.call(messages)])
		self.assertIn("attempt 1/5", logs.output[0])

	def test_all_failed_calls_exhaust_budget_and_preserve_last_cause(self):
		errors = [llm_utils.requests.Timeout(f"synthetic failure {index}") for index in range(llm_utils.MAX_EMPTY_RETRIES)]
		self.request.side_effect = errors
		with self.assertLogs(llm_utils.logger, level="WARNING"), self.assertRaises(RuntimeError) as error:
			llm_utils.get_nonempty_llm_response([])
		self.assertEqual(self.request.call_count, llm_utils.MAX_EMPTY_RETRIES)
		self.assertIs(error.exception.__cause__, errors[-1])
		self.assertIn("after 5 attempts", str(error.exception))

	def test_empty_and_failed_calls_share_one_attempt_budget(self):
		last_error = llm_utils.requests.ConnectionError("synthetic connection")
		self.request.side_effect = ["", last_error, " ", "", "\n"]
		with self.assertLogs(llm_utils.logger, level="WARNING"), self.assertRaises(RuntimeError) as error:
			llm_utils.get_nonempty_llm_response([])
		self.assertEqual(self.request.call_count, llm_utils.MAX_EMPTY_RETRIES)
		self.assertIs(error.exception.__cause__, last_error)

	def test_empty_only_responses_stay_bounded_without_an_error_cause(self):
		self.request.side_effect = ["", " ", "\n", "", "\t"]
		with self.assertLogs(llm_utils.logger, level="WARNING"), self.assertRaises(RuntimeError) as error:
			llm_utils.get_nonempty_llm_response([])
		self.assertEqual(self.request.call_count, llm_utils.MAX_EMPTY_RETRIES)
		self.assertIsNone(error.exception.__cause__)

	def test_keyboard_interrupt_is_not_counted_or_swallowed(self):
		self.request.side_effect = KeyboardInterrupt
		with self.assertRaises(KeyboardInterrupt):
			llm_utils.get_nonempty_llm_response([])
		self.request.assert_called_once_with([])

	def test_task_query_falls_back_only_after_failed_attempt_budget(self):
		self.request.side_effect = llm_utils.requests.Timeout("synthetic cold load")
		with self.assertLogs(llm_utils.logger, level="WARNING") as logs:
			self.assertEqual(llm_utils.get_search_query_from_task_description("Search on Bing about northern lights tonight"), "northern lights tonight")
		self.assertEqual(self.request.call_count, llm_utils.MAX_EMPTY_RETRIES)
		self.assertIn("Using fallback search query generator", logs.output[-1])

	def test_related_queries_fall_back_for_remainder_after_failed_attempt_budget(self):
		self.request.side_effect = llm_utils.requests.Timeout("synthetic cold load")
		with mock.patch.object(llm_utils, "get_random_noun", side_effect=["forest", "river", "cloud", "tree"]), self.assertLogs(llm_utils.logger, level="WARNING"):
			self.assertEqual(list(llm_utils.get_related_search_queries("seed", num_queries=2)), ["seed forest river", "seed cloud tree"])
		self.assertEqual(self.request.call_count, llm_utils.MAX_EMPTY_RETRIES)


if __name__ == "__main__":
	unittest.main()
