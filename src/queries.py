"""Where search queries come from.

Two backends. `llm` is the default and is unchanged, so nothing about an
existing setup moves. `trends` uses public feeds and needs no account, no
model and no key, which is the difference between running this in five
minutes and installing Ollama first.

    QUERY_SOURCE=trends python src/main.py

The LLM's whole job in this project is producing short strings to type into
Bing, and Bing's own autosuggest answers that question directly.
"""

import logging
import os

import query_sources

# llm_utils is imported inside the llm branch rather than here. It imports
# ollama at module scope, so importing it eagerly would make the ollama package
# a hard requirement even for a run that never touches a model, which is the
# opposite of the point. A trends-only install, the Docker image for instance,
# does not ship it.

logger = logging.getLogger(__name__)

LLM = "llm"
TRENDS = "trends"

DEFAULT_SOURCE = LLM

ENV_VAR = "QUERY_SOURCE"

KNOWN_TASKS = {
	"Search on Bing for hotels to stay at on your next adventure": "hotels in Paris",
	"Search on Bing to find jewelry for any occasion": "jewelry",
	"Search on Bing for real-estate available in your dream town": "real estate in San Francisco",
	"Search on Bing to see if your team won their recent game or match": "recent sports scores",
	"Search on Bing for a flight to your perfect vacation": "flights to Miami",
	"Search on Bing for the best insurance plans for your needs": "car insurance plans",
	"Search on Bing to compare personal and student loan options": "student loan options",
	"Search on Bing to find items on your shopping list": "school supplies"
}


def selected_source() -> str:
	"""Read on each call so a test can change it without reimporting."""
	choice = os.environ.get(ENV_VAR, DEFAULT_SOURCE).strip().lower()

	return choice if choice in (LLM, TRENDS) else DEFAULT_SOURCE


def search_query_for_task(task_description: str) -> str:
	"""A query for one "Search on Bing for X" card."""

	if (short_path := KNOWN_TASKS.get(task_description)): return short_path

	if selected_source() == TRENDS:
		query = query_sources.query_from_task_description(task_description)

		if query:
			return query

		# Not a network failure: an unreachable autosuggest still returns the
		# trimmed description. Nothing is left here only when the description was
		# instruction words all the way down, which leaves the sentence itself as
		# the last thing worth typing.
		logger.warning("No searchable words in the task description, using it as written.")

		return task_description.lower()

	import llm_utils

	return llm_utils.get_search_query_from_task_description(task_description)


def related_queries(count: int):
	"""`count` queries for the daily search quota."""
	if selected_source() == TRENDS:
		queries = query_sources.related_queries(count)

		if queries:
			return queries

		logger.warning("No query source reachable, falling back to the wordlist.")

		# nouns.txt is already in the repo for exactly this kind of seed.
		return query_sources.wordlist_queries(count)

	import llm_utils

	return llm_utils.get_related_search_queries(llm_utils.get_random_noun(), num_queries=count)
