"""Local LLM endpoint that is still starting up, checked without a network or a model."""

import logging
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests
import llm_utils


class FakeResponse:
	def __init__(self, content="latest space news"):
		self.content = content

	def raise_for_status(self):
		pass

	def json(self):
		return {"choices": [{"message": {"content": self.content}}]}


def refused_connection():
	return requests.exceptions.ConnectionError(
		"HTTPConnectionPool(host='localhost', port=11434): Max retries exceeded with url: "
		"/v1/chat/completions (Caused by NewConnectionError('<urllib3.connection.HTTPConnection "
		"object>: Failed to establish a new connection: [WinError 10061] No connection could be "
		"made because the target machine actively refused it'))"
	)


class LocalStartupWaitTests(unittest.TestCase):
	def setUp(self):
		self.enterContext(mock.patch.dict(os.environ, {}, clear=True))
		os.environ.update(
			LLM_PROVIDER="local",
			LOCAL_LLM_BASE_URL="http://synthetic-local.invalid/v1",
			LOCAL_LLM_MODEL="synthetic-local",
			LLM_STARTUP_RETRIES="3",
			LLM_STARTUP_RETRY_SECONDS="0.5",
		)
		self.enterContext(mock.patch.object(llm_utils, "_startup_wait_pending", True))
		self.sleeps = []
		self.enterContext(mock.patch.object(llm_utils.time, "sleep", side_effect=self.sleeps.append))

	def post_with(self, side_effect):
		return self.enterContext(mock.patch.object(llm_utils.requests, "post", side_effect=side_effect))

	def test_a_refused_connection_is_retried_until_the_server_answers(self):
		response = FakeResponse()
		post = self.post_with([refused_connection(), refused_connection(), response])

		self.assertEqual(llm_utils.get_llm_response([{"role": "user", "content": "synthetic"}]), "latest space news")
		self.assertEqual(post.call_count, 3)
		self.assertEqual(self.sleeps, [0.5, 0.5])

	def test_the_run_reports_the_wait_once_before_the_server_starts(self):
		os.environ["LLM_STARTUP_RETRIES"] = "2"
		post = self.enterContext(mock.patch.object(llm_utils.requests, "post"))
		post.side_effect = [refused_connection(), FakeResponse()]

		with self.assertLogs("llm_utils", level=logging.WARNING) as logs:
			llm_utils.get_llm_response([{"role": "user", "content": "synthetic"}])

		self.assertEqual(len(logs.records), 1)
		self.assertIn("not accepting connections yet", logs.records[0].getMessage())
		self.assertIn("1s", logs.records[0].getMessage())

	def test_an_endpoint_that_never_answers_raises_after_the_configured_retries(self):
		post = self.post_with([refused_connection()] * 10)

		with self.assertRaises(requests.exceptions.ConnectionError):
			llm_utils.get_llm_response([{"role": "user", "content": "synthetic"}])

		self.assertEqual(post.call_count, 4)
		self.assertEqual(self.sleeps, [0.5, 0.5, 0.5])

	def test_the_fallback_generator_still_takes_over_when_the_wait_expires(self):
		self.post_with([refused_connection()] * 10)

		query = llm_utils.get_search_query_from_task_description("Search for the latest space news")

		self.assertEqual(query, "latest space news")

	def test_the_wait_is_paid_once_per_run_not_once_per_query(self):
		later = FakeResponse("later query")
		post = self.post_with([refused_connection()] * 10)

		with self.assertRaises(requests.exceptions.ConnectionError):
			llm_utils.get_llm_response([{"role": "user", "content": "synthetic"}])
		spent, self.sleeps = len(self.sleeps), []

		post.side_effect = [refused_connection(), later]
		with self.assertRaises(requests.exceptions.ConnectionError):
			llm_utils.get_llm_response([{"role": "user", "content": "synthetic"}])

		self.assertEqual(spent, 3)
		self.assertEqual(self.sleeps, [])
		self.assertEqual(post.call_count, 5)

	def test_zero_retries_keeps_the_previous_fail_fast_behaviour(self):
		os.environ["LLM_STARTUP_RETRIES"] = "0"
		post = self.post_with([refused_connection()] * 3)

		with self.assertRaises(requests.exceptions.ConnectionError):
			llm_utils.get_llm_response([{"role": "user", "content": "synthetic"}])

		self.assertEqual(post.call_count, 1)
		self.assertEqual(self.sleeps, [])

	def test_an_unusable_retry_setting_falls_back_to_the_default(self):
		os.environ["LLM_STARTUP_RETRIES"] = "not-a-number"
		os.environ["LLM_STARTUP_RETRY_SECONDS"] = "not-a-number"

		retries, delay = llm_utils._get_startup_retry_settings()

		self.assertEqual(retries, llm_utils.MAX_LOCAL_STARTUP_RETRIES)
		self.assertEqual(delay, llm_utils.DEFAULT_LOCAL_STARTUP_RETRY_SECONDS)

	def test_a_remote_provider_is_never_waited_for(self):
		os.environ.update(
			LLM_PROVIDER="openrouter",
			OPENROUTER_API_KEY="synthetic-not-a-key",
			OPENROUTER_BASE_URL="http://synthetic.invalid/v1",
			OPENROUTER_MODEL="synthetic/model",
		)
		post = self.post_with([refused_connection()] * 3)

		with self.assertRaises(requests.exceptions.ConnectionError):
			llm_utils.get_llm_response([{"role": "user", "content": "synthetic"}])

		self.assertEqual(post.call_count, 1)
		self.assertEqual(self.sleeps, [])

	def test_a_slow_model_is_not_treated_as_a_server_that_has_not_started(self):
		post = self.post_with([requests.exceptions.ReadTimeout("synthetic slow model")] * 3)

		with self.assertRaises(requests.exceptions.ReadTimeout):
			llm_utils.get_llm_response([{"role": "user", "content": "synthetic"}])

		self.assertEqual(post.call_count, 1)
		self.assertEqual(self.sleeps, [])

	def test_the_request_still_posts_the_configured_model_and_messages(self):
		post = self.enterContext(mock.patch.object(llm_utils.requests, "post", return_value=FakeResponse()))
		messages = [{"role": "user", "content": "synthetic"}]

		llm_utils.get_llm_response(messages)

		post.assert_called_once_with(
			"http://synthetic-local.invalid/v1/chat/completions",
			headers={"Content-Type": "application/json"},
			json={"model": "synthetic-local", "messages": messages},
			timeout=60.0,
		)


if __name__ == "__main__":
	unittest.main()
