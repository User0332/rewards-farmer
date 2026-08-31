from typing import Generator
import random
import json
import os
import ollama
from urllib import request, parse
from urllib.error import HTTPError


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_OLLAMA_MODEL = "gemma4:cloud"

# Set to "gemini" or "ollama" to choose the active backend.
DEFAULT_LLM_BACKEND = "gemini"


DEFAULT_SYSTEM_PROMPT_FOR_SEARCH_QUEST = (
	"You are a helpful assistant tasked with creating a search query based on a directive. "
	"Output nothing but the search query you create, and do not include any additional commentary or explanation. "
	"Do not include any labels or quotes. "
	"The search query must be the only output, and do not format the query as an imperative to 'search for' something. "
	"Imagine that your output will be fed directly into a search engine as you provide it. "
	"For example, if the directive is 'Search on Bing for the latest news about space exploration', you might output 'latest news space exploration'. "
	"Outputting 'search on Bing for the latest news about space exploration' or 'search bing.com/news for space exploration' would be incorrect, "
	"as those answers include instructions to perform a search rather than just the search query itself. "
	"Additionally, be specific, e.g. if a prompt asks you to search for vacation flights, include "
	"a specific destination rather than just searching 'vacation flights'. The current year is 2026. "
	"Make your query concise, ideally 6 words or less, and do not include any punctuation. "
)

DEFAULT_USER_PROMPT_FOR_SEARCH_QUEST_WITHOUT_DESC = """Base your search query on the following task description: """

DEFAULT_SYSTEM_PROMPT_FOR_SEARCH_POINTS = (
	"The user is interested in learning more about topics related to a word that will be given to you. "
	"Your task is to come up with subsequent search queries that relate to each other, each one branching out "
	"from the previous one so that the user can explore a topic in depth. Your first search query should be "
	"based on the word that the user gives you, and each subsequent search query should be at least remotely based on the previous ones. "
	"Output only the single search query you come up with and do not include any additional commentary or explanation. Do not include any labels or quotes. "
	"The search queries should ideally be short (6 words max) and do not need to be fully fledged questions, but they should be unique. The current year is 2026."
)

DEFAULT_USER_PROMPT_FOR_SEARCH_POINTS_WITHOUT_DESC = """Generate the first search query based on the following word: """

USER_PROMPT_FOR_SEARCH_QUERY_CONTINUATION = """Generate the next search query."""

def get_ollama_response(messages: list[dict[str, str]], model: str=DEFAULT_OLLAMA_MODEL) -> str:
	response = ollama.chat(
# Without an explicit timeout a stalled or cold ollama backend blocks the whole
# run forever, which is fatal for an unattended scheduled run.
_CLIENT = ollama.Client(timeout=180)

MAX_EMPTY_RETRIES = 5


def get_ollama_response(messages: list[dict[str, str]], model: str="gemma4:cloud") -> str:
	response = _CLIENT.chat(
		model=model,
		messages=messages
	)

	message = response.message
	content = message.content
	if content is None:
		raise TypeError("Ollama response message content was empty")

	return str(content)


def get_ai_response(
	messages: list[dict[str, str]],
	model: str = DEFAULT_GEMINI_MODEL,
	api_key: str | None = None,
	backend: str = DEFAULT_LLM_BACKEND,
) -> str:
	if backend == "ollama":
		return get_ollama_response(messages, model=model)

	resolved_api_key = api_key or GEMINI_API_KEY

	if not resolved_api_key:
		raise ValueError("Set GEMINI_API_KEY in your .env or environment, or pass api_key explicitly")

	system_parts = []
	contents = []

	for message in messages:
		role = message["role"]
		content = message["content"]

		if role == "system":
			system_parts.append({"text": content})
			continue

		gemini_role = "model" if role == "assistant" else "user"
		contents.append({
			"role": gemini_role,
			"parts": [{"text": content}]
		})

	payload: dict[str, object] = {
		"contents": contents
	}

	if system_parts:
		payload["systemInstruction"] = {"parts": system_parts}

	url = (
		f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
		f"?key={parse.quote(resolved_api_key)}"
	)

	req = request.Request(
		url,
		data=json.dumps(payload).encode("utf-8"),
		headers={"Content-Type": "application/json"},
		method="POST"
	)

	try:
		with request.urlopen(req) as response:
			body = json.loads(response.read().decode("utf-8"))
	except HTTPError as exc:
		try:
			error_body = exc.read().decode("utf-8")
		except Exception:
			error_body = str(exc)
		raise RuntimeError(f"Gemini request failed ({exc.code}): {error_body}") from exc

	try:
		return body["candidates"][0]["content"]["parts"][0]["text"]
	except (KeyError, IndexError, TypeError):
		raise RuntimeError(f"Unexpected Gemini response format: {body}")


def get_nonempty_ollama_response(messages: list[dict[str, str]]) -> str:
	"""Retry a bounded number of times instead of spinning forever on empties."""
	for attempt in range(MAX_EMPTY_RETRIES):
		response = get_ollama_response(messages)

		if response and response.strip():
			return response

		print(f"[WARNING] Empty LLM response, retry {attempt + 1}/{MAX_EMPTY_RETRIES}")

	raise RuntimeError(f"LLM returned nothing usable after {MAX_EMPTY_RETRIES} attempts")

def get_search_query_from_task_description(task_description: str) -> str:
	# compat
	if "lyrics of your favorite song" in task_description.lower(): return "sweet caroline lyrics"

	messages = [
		{
			"role": "system",
			"content": DEFAULT_SYSTEM_PROMPT_FOR_SEARCH_QUEST
		},
		{
			"role": "user",
			"content": DEFAULT_USER_PROMPT_FOR_SEARCH_QUEST_WITHOUT_DESC + task_description
		}
	]

	while not (response := get_ai_response(messages)): pass # ensure non-empty response
	response = get_nonempty_ollama_response(messages)

	return response.lower()

def get_related_search_queries(seed_word: str, num_queries: int=20) -> Generator[str, None, None]:
	messages = [
		{
			"role": "system",
			"content": DEFAULT_SYSTEM_PROMPT_FOR_SEARCH_POINTS
		},
		{
			"role": "user",
			"content": DEFAULT_USER_PROMPT_FOR_SEARCH_POINTS_WITHOUT_DESC + seed_word
		}
	]

	for _ in range(num_queries):
		while not (response := get_ai_response(messages)): pass # ensure non-empty response
		response = get_nonempty_ollama_response(messages)

		yield response.lower()

		messages.append({
			"role": "assistant",
			"content": response
		})

		messages.append({
			"role": "user",
			"content": USER_PROMPT_FOR_SEARCH_QUERY_CONTINUATION
		})

NOUNS = [
	noun.strip().lower() for noun in open("nouns.txt", "r").read().splitlines()
	if len(noun.strip()) >= 3
]

def get_random_noun() -> str:
	return random.choice(NOUNS)
