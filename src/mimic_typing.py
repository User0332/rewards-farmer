import time
import random
from typing import Iterable
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

FIRST_INTERVAL = (0.05, 0.12)
SECOND_INTERVAL = (0.12, 0.22)
THIRD_INTERVAL = (0.22, 0.38)

FIRST_INTERVAL_PROBABILITY = 0.42
SECOND_INTERVAL_PROBABILITY = 0.48
THIRD_INTERVAL_PROBABILITY = 1.0 - (FIRST_INTERVAL_PROBABILITY + SECOND_INTERVAL_PROBABILITY)

# Keyboard adjacency mapping for natural typo emulation
ADJACENT_KEYS = {
	'a': 'sqwz', 'b': 'vghn', 'c': 'xdfv', 'd': 'serfcx', 'e': 'wsdr',
	'f': 'drtgvc', 'g': 'ftyhbv', 'h': 'gyujnb', 'i': 'ujko', 'j': 'huikmn',
	'k': 'jiolm', 'l': 'kop', 'm': 'njk', 'n': 'bhjm', 'o': 'iklp',
	'p': 'ol', 'q': 'wa', 'r': 'edft', 's': 'awedxz', 't': 'rfgy',
	'u': 'yhji', 'v': 'cfgb', 'w': 'qase', 'x': 'zsdc', 'y': 'tghu', 'z': 'asx'
}

class KeyboardUtils:
	def __init__(self, driver: webdriver.Edge):
		self.driver = driver

	def send_keys(self, keys: Iterable[str], allow_typos: bool = True):
		actions = ActionChains(self.driver, duration=0)

		for key in keys:
			# Only allow typos on standard lowercase letters with 2.5% chance
			if allow_typos and isinstance(key, str) and len(key) == 1 and key.lower() in ADJACENT_KEYS and random.random() < 0.025:
				wrong_key = random.choice(ADJACENT_KEYS[key.lower()])
				actions.send_keys(wrong_key)
				# Human realization delay
				actions.pause(random.uniform(0.18, 0.32))
				actions.send_keys(Keys.BACK_SPACE)
				actions.pause(random.uniform(0.08, 0.18))

			actions.send_keys(key)

			interval = random.choices(
				[FIRST_INTERVAL, SECOND_INTERVAL, THIRD_INTERVAL],
				weights=[FIRST_INTERVAL_PROBABILITY, SECOND_INTERVAL_PROBABILITY, THIRD_INTERVAL_PROBABILITY]
			)[0]

			actions.pause(random.uniform(interval[0], interval[1]))

		actions.perform()