import re
from typing import Union, Sequence, Optional, List, Tuple

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, WebDriverException
from selenium import webdriver


class Labels:
	"""Multilingual labels the selectors match on.

	The Rewards markup carries no static IDs for several controls, so they are
	discovered via visible labels and accessibility attributes. Multilingual
	tuples support Swedish, English, German, French, Spanish, and Italian variants.
	"""

	POINTS_BREAKDOWN = (
		"points breakdown",
		"poänguppdelning",
		"poängöversikt",
		"poänginformation",
		"punkteübersicht",
		"desglose de puntos",
		"détail des points",
		"dettaglio punti"
	)
	
	READY_TO_CLAIM = (
		"ready to claim",
		"redo att göra anspråk",
		"redo att göra anspråk på",
		"redo att hämta",
		"hämta",
		"gör anspråk",
		"bereit zum einlösen",
		"listo para canjear",
		"prêt à réclamer",
		"pronto per il riscatto"
	)
	
	CLAIM = (
		"claim",
		"hämta",
		"gör anspråk",
		"einlösen",
		"lösa in",
		"canjear",
		"réclamer",
		"riscatta"
	)
	
	DAILY_SET_STREAK = (
		"daily set streak",
		"dagligt set-svit",
		"daglig uppsättning",
		"dagligt set",
		"tägliches set",
		"serie diaria",
		"série quotidienne",
		"serie giornaliera"
	)
	
	CARD_COMPLETED = (
		"completed",
		"slutförd",
		"slutfört",
		"klar",
		"klart",
		"avslutad",
		"avslutat",
		"abgeschlossen",
		"completado",
		"terminé",
		"completato"
	)
	
	VISUAL_SEARCH_STREAK = (
		"visual search streak",
		"visuell sökning",
		"visuell sök-svit",
		"visuelle suche",
		"búsqueda visual",
		"recherche visuelle",
		"ricerca visiva"
	)
	
	SEARCH_NOW = (
		"search now",
		"sök nu",
		"jetzt suchen",
		"buscar ahora",
		"rechercher maintenant",
		"cerca ora"
	)


def dismiss_cookie_and_consent_banners(driver: webdriver.Edge) -> int:
	"""Detect and dismiss localized cookie consent dialogs, sign-in toasts, and promo overlays.

	Returns the count of dismissed overlays. Executes rapidly with zero latency if no
	dialog is present.
	"""
	dismissed_count = 0

	# 1. Fast JS inspection & dismissal for standard Bing consent IDs
	bing_cookie_js = """
	let clicked = 0;
	// Direct standard Bing cookie buttons
	const directButtons = [
		document.getElementById('bnp_btn_accept'),
		document.getElementById('bnp_btn_reject'),
		document.querySelector('#bnp_container button'),
		document.querySelector('.bnp_btn'),
		document.querySelector('#id_d button')
	];
	for (const btn of directButtons) {
		if (btn && btn.offsetParent !== null) {
			btn.click();
			clicked++;
			break;
		}
	}
	return clicked;
	"""
	try:
		if driver.execute_script(bing_cookie_js):
			dismissed_count += 1
			print("[INFO] Dismissed Bing cookie banner via direct button.")
			return dismissed_count
	except Exception:
		pass

	# 2. Text-based consent button matching (Swedish, English, European languages)
	consent_phrases = (
		"godkänn alla", "acceptera alla", "jag godkänner", "godkänn", "acceptera",
		"accept all", "i accept", "accept cookies", "accept", "allow all", "i agree",
		"alle akzeptieren", "akzeptieren", "zustimmen",
		"aceptar todo", "aceptar", "accepter tout", "accepter", "accetta tutti"
	)

	# Search buttons inside container or body
	try:
		candidate_buttons = driver.find_elements(By.CSS_SELECTOR, "#bnp_container button, #id_d button, [role='dialog'] button, button.bnp_btn")
		if not candidate_buttons:
			candidate_buttons = driver.find_elements(By.TAG_NAME, "button")[:15]

		for btn in candidate_buttons:
			try:
				if not btn.is_displayed():
					continue
				btn_text = (btn.text or btn.get_attribute("aria-label") or "").strip().lower()
				for phrase in consent_phrases:
					if phrase in btn_text:
						driver.execute_script("arguments[0].click();", btn)
						dismissed_count += 1
						print(f"[INFO] Dismissed cookie/consent dialog (matched: '{phrase}').")
						return dismissed_count
			except StaleElementReferenceException:
				continue
	except Exception:
		pass

	# 3. Dismiss secondary sign-in prompts & toast overlays ("Not now", "Nej tack", "Maybe later")
	dismiss_phrases = (
		"not now", "nej tack", "kanske senare", "senare", "maybe later",
		"no thanks", "nein danke", "avvisa", "stäng"
	)
	try:
		toasts = driver.find_elements(By.CSS_SELECTOR, "div[role='dialog'] button, div[role='alertdialog'] button, .modal button")
		for btn in toasts:
			try:
				if not btn.is_displayed():
					continue
				btn_text = (btn.text or btn.get_attribute("aria-label") or "").strip().lower()
				# Avoid clicking "learn more" links
				if "learn more" in btn_text or "läs mer" in btn_text:
					continue
				if any(p in btn_text for p in dismiss_phrases):
					driver.execute_script("arguments[0].click();", btn)
					dismissed_count += 1
					print(f"[INFO] Dismissed toast/overlay button ('{btn_text}').")
					break
			except StaleElementReferenceException:
				continue
	except Exception:
		pass

	# 4. Auto-accept Microsoft 'Stay signed in?' (KMSI) prompt if already authenticated
	try:
		current_url = driver.current_url.lower()
		if "login.live.com" in current_url or "login.microsoftonline.com" in current_url:
			# Check "Don't show this again" checkbox
			checkboxes = driver.find_elements(By.CSS_SELECTOR, "#KmsiCheckboxField, input[name='DontShowAgain']")
			if checkboxes and not checkboxes[0].is_selected():
				try:
					driver.execute_script("arguments[0].click();", checkboxes[0])
				except Exception:
					pass

			# Click "Yes" / "Accept" button
			kmsi_buttons = driver.find_elements(By.CSS_SELECTOR, "input#acceptButton, button#acceptButton, input#idSIButton9, input[type='submit'][value*='Yes' i], input[type='submit'][value*='Ja' i]")
			for k_btn in kmsi_buttons:
				if k_btn.is_displayed():
					driver.execute_script("arguments[0].click();", k_btn)
					dismissed_count += 1
					print("[INFO] Auto-confirmed Microsoft 'Stay signed in' (Yes).")
					break
	except Exception:
		pass

	return dismissed_count


class ElementSelectionUtils:
	"""Resilient semantic and multi-attribute selectors for the Microsoft Rewards & Bing UI."""

	def __init__(self, driver: webdriver.Edge):
		self.driver = driver

	# ------------------------------------------------------------------
	# Helpers & Matchers
	# ------------------------------------------------------------------

	def resolve(self, xpath: str) -> WebElement:
		return self.driver.find_element(By.XPATH, xpath)

	def _matches_any(self, text: str, needles: Union[str, Sequence[str]]) -> bool:
		"""Case-insensitive check if any needle is contained in text."""
		if not text:
			return False
		text_lower = text.lower()
		if isinstance(needles, str):
			return needles.lower() in text_lower
		return any(n.lower() in text_lower for n in needles)

	def _container_by_id(self, element_id: str) -> WebElement:
		"""Return the usable copy of an ID that may be present more than once in responsive layout."""
		matches = self.driver.find_elements(By.ID, element_id)

		if not matches:
			# Fallback: search by data-bi-name or class
			matches = self.driver.find_elements(By.CSS_SELECTOR, f"[data-bi-name*='{element_id}'], [class*='{element_id}']")
			if not matches:
				raise NoSuchElementException(f"no element with id {element_id!r}")

		for match in matches:
			try:
				if match.is_displayed() and match.find_elements(By.TAG_NAME, "a"):
					return match
			except StaleElementReferenceException:
				continue

		raise NoSuchElementException(
			f"{element_id!r} is present but no visible copy has clickable content yet"
		)

	def _button_containing(self, needles: Union[str, Sequence[str]], root: Optional[WebElement] = None) -> WebElement:
		"""First button whose text or aria-label matches any of the target needles."""
		scope = self.driver if root is None else root

		for button in scope.find_elements(By.TAG_NAME, "button"):
			try:
				if not button.is_displayed():
					continue
				text = button.text or ""
				aria = button.get_attribute("aria-label") or ""
				title = button.get_attribute("title") or ""
				if self._matches_any(text, needles) or self._matches_any(aria, needles) or self._matches_any(title, needles):
					return button
			except StaleElementReferenceException:
				continue

		needle_repr = needles if isinstance(needles, str) else list(needles)
		raise NoSuchElementException(f"no button containing any of {needle_repr!r}")

	def _link_containing(self, needles: Union[str, Sequence[str]], root: Optional[WebElement] = None) -> WebElement:
		"""First link whose text or aria-label matches any of the target needles."""
		scope = self.driver if root is None else root

		for link in scope.find_elements(By.TAG_NAME, "a"):
			try:
				if not link.is_displayed():
					continue
				text = link.text or ""
				aria = link.get_attribute("aria-label") or ""
				if self._matches_any(text, needles) or self._matches_any(aria, needles):
					return link
			except StaleElementReferenceException:
				continue

		needle_repr = needles if isinstance(needles, str) else list(needles)
		raise NoSuchElementException(f"no link containing any of {needle_repr!r}")

	# ------------------------------------------------------------------
	# Navigation Tabs & Points Balance
	# ------------------------------------------------------------------

	def get_total_points_balance(self) -> Optional[int]:
		"""Extract current total available rewards points balance from header or page state."""
		# 1. Comprehensive JS inspection of header/nav elements and raw numbers (e.g. '6,302')
		js_extractor = r"""
		// Strategy A: Check top navigation / header bar for raw formatted balance (e.g. '6,302')
		const headerContainers = [
			document.querySelector('header'),
			document.querySelector('nav'),
			document.querySelector('[role="banner"]'),
			document.querySelector('#header'),
			document.querySelector('div[class*="header" i]'),
			document.querySelector('div[class*="nav" i]'),
			document.body
		];

		for (const container of headerContainers) {
			if (!container) continue;
			// Check leaf text nodes inside header
			const elements = container.querySelectorAll('span, div, p, a, button, b, strong');
			for (const el of elements) {
				if (el.children.length === 0) {
					const text = (el.innerText || el.textContent || '').trim();
					// Match comma-separated numbers like "6,302" or "15,400"
					if (/^[0-9]{1,3}(,[0-9]{3})+$/.test(text)) {
						return parseInt(text.replace(/,/g, ''), 10);
					}
				}
			}
		}

		// Strategy B: Check elements with ID or class containing point/balance
		const pointSelectors = [
			'#userPoints',
			'[id*="userPoint" i]',
			'[id*="balance" i]',
			'[class*="user-points" i]',
			'[class*="pointsValue" i]',
			'[class*="points-balance" i]',
			'[data-bi-name*="points" i]',
			'header [aria-label*="point" i]',
			'header [aria-label*="poäng" i]',
		];
		for (const sel of pointSelectors) {
			const matches = document.querySelectorAll(sel);
			for (const el of matches) {
				const text = (el.innerText || el.getAttribute('aria-label') || '').trim();
				const m = text.match(/([0-9,]+)/);
				if (m) {
					const val = parseInt(m[1].replace(/,/g, ''), 10);
					if (val > 0) return val;
				}
			}
		}

		// Strategy C: Check React / global window preloaded state
		if (window.__INITIAL_STATE__ && window.__INITIAL_STATE__.dashboard) {
			const p = window.__INITIAL_STATE__.dashboard.userStatus?.availablePoints;
			if (p && p > 0) return p;
		}
		if (window.rewardsUser && window.rewardsUser.availablePoints) {
			return window.rewardsUser.availablePoints;
		}

		return null;
		"""
		try:
			js_result = self.driver.execute_script(js_extractor)
			if js_result is not None and int(js_result) > 0:
				return int(js_result)
		except Exception:
			pass

		# 2. Python Selenium fallback
		selectors = [
			"#userPoints",
			"[class*='user-points']",
			"[class*='pointsValue']",
			"[class*='points-balance']",
			"[data-bi-name*='points']",
			"header [aria-label*='points' i]",
			"header [aria-label*='poäng' i]",
			"a[href*='rewards.bing.com'] [class*='point']",
		]
		for selector in selectors:
			for elem in self.driver.find_elements(By.CSS_SELECTOR, selector):
				try:
					if elem.is_displayed():
						text = elem.text or elem.get_attribute("aria-label") or ""
						match = re.search(r"([\d,]+)", text)
						if match:
							val = int(match.group(1).replace(",", ""))
							if val > 0:
								return val
				except StaleElementReferenceException:
					continue

		return None

	def get_today_points(self) -> Optional[int]:
		"""Extract today's points earned from the 'Today's points' card on the page."""
		js_today_extractor = r"""
		const allDivs = document.querySelectorAll('div, section, a');
		for (const card of allDivs) {
			const text = (card.innerText || card.textContent || '').trim();
			if (/(?:today'?s\s+points|dagens\s+poäng|heutige\s+punkte|puntos\s+de\s+hoy|points\s+du\s+jour)/i.test(text)) {
				const lines = text.split('\n');
				for (const line of lines) {
					const clean = line.trim();
					if (/^[0-9]+$/.test(clean)) {
						return parseInt(clean, 10);
					}
					const m = clean.match(/(?:today'?s\s+points|dagens\s+poäng|heutige\s+punkte)\D*?([0-9]+)/i);
					if (m) {
						return parseInt(m[1], 10);
					}
				}
			}
		}
		return null;
		"""
		try:
			res = self.driver.execute_script(js_today_extractor)
			if res is not None:
				return int(res)
		except Exception:
			pass
		return None

	def get_earn_tab(self) -> WebElement:
		"""Earn page tab with multi-attribute fallbacks."""
		selectors = [
			(By.CSS_SELECTOR, '[id$="-tab-/earn"]'),
			(By.CSS_SELECTOR, 'a[href*="/earn"]'),
			(By.XPATH, "//a[contains(@href, '/earn') or contains(translate(text(), 'EARN', 'earn'), 'earn')]"),
			(By.XPATH, "//a[contains(translate(text(), 'TJÄNA', 'tjäna'), 'tjäna')]"),
			(By.CSS_SELECTOR, '[data-bi-name*="earn"]')
		]
		for by, selector in selectors:
			try:
				elem = self.driver.find_element(by, selector)
				if elem.is_displayed():
					return elem
			except NoSuchElementException:
				continue
		raise NoSuchElementException("Earn tab not found")

	def get_dashboard_tab(self) -> WebElement:
		"""Dashboard page tab with multi-attribute fallbacks."""
		selectors = [
			(By.CSS_SELECTOR, '[id$="-tab-/dashboard"]'),
			(By.CSS_SELECTOR, 'a[href*="/dashboard"]'),
			(By.XPATH, "//a[contains(@href, '/dashboard') or contains(translate(text(), 'DASHBOARD', 'dashboard'), 'dashboard')]"),
			(By.XPATH, "//a[contains(translate(text(), 'ÖVERSIKT', 'översikt'), 'översikt')]"),
			(By.CSS_SELECTOR, '[data-bi-name*="dashboard"]')
		]
		for by, selector in selectors:
			try:
				elem = self.driver.find_element(by, selector)
				if elem.is_displayed():
					return elem
			except NoSuchElementException:
				continue
		raise NoSuchElementException("Dashboard tab not found")

	def get_sidebar_section(self) -> WebElement:
		"""Locate active sliding sidebar section."""
		for section in self.driver.find_elements(By.TAG_NAME, "section"):
			try:
				sid = section.get_dom_attribute("id") or ""
				aria_role = section.get_dom_attribute("role") or ""
				if sid.startswith("react-aria") or aria_role == "dialog" or "sidebar" in sid.lower():
					if section.is_displayed():
						return section
			except StaleElementReferenceException:
				continue

		raise NoSuchElementException("sidebar section not found")

	# ------------------------------------------------------------------
	# Daily Set
	# ------------------------------------------------------------------

	def _streaks_button(self, index: int) -> WebElement:
		"""Positional fallback inside the streaks section."""
		streaks = self.driver.find_element(By.ID, "streaks")
		return streaks.find_element(By.XPATH, f".//button[{index}]")

	def get_open_daily_set_button(self) -> WebElement:
		try:
			return self._button_containing(Labels.DAILY_SET_STREAK)
		except NoSuchElementException:
			try:
				return self._streaks_button(3)
			except NoSuchElementException:
				# General fallback in streaks
				streaks = self.driver.find_element(By.ID, "streaks")
				buttons = [b for b in streaks.find_elements(By.TAG_NAME, "button") if b.is_displayed()]
				if buttons:
					return buttons[0]
				raise NoSuchElementException("Open daily set button not found")

	def get_daily_set_elements(self) -> List[WebElement]:
		sidebar = self.get_sidebar_section()
		links = sidebar.find_elements(By.TAG_NAME, "a")
		# The first link is typically the progress/streak header row
		return links[1:] if len(links) > 1 else links

	# ------------------------------------------------------------------
	# Explore on Bing
	# ------------------------------------------------------------------

	def get_explore_on_bing_elements(self) -> List[WebElement]:
		try:
			container = self._container_by_id("exploreonbing")
		except NoSuchElementException:
			return []

		return [a for a in container.find_elements(By.TAG_NAME, "a") if a.is_displayed()]

	# ------------------------------------------------------------------
	# Visual Search
	# ------------------------------------------------------------------

	def get_open_visual_search_sidebar(self) -> WebElement:
		"""Locate visual search entry button if present in active streaks."""
		return self._button_containing(Labels.VISUAL_SEARCH_STREAK)

	def get_search_now_link_from_visual_search_sidebar(self) -> WebElement:
		sidebar = self.get_sidebar_section()
		try:
			return self._link_containing(Labels.SEARCH_NOW, sidebar)
		except NoSuchElementException:
			links = [a for a in sidebar.find_elements(By.TAG_NAME, "a") if a.is_displayed()]
			if len(links) < 2:
				raise NoSuchElementException("visual search sidebar has no usable link")
			return links[1]

	def get_visual_search_button(self) -> WebElement:
		selectors = [
			(By.CSS_SELECTOR, "#sb_form > div.camera.icon"),
			(By.CSS_SELECTOR, "#sb_form .camera"),
			(By.CSS_SELECTOR, "[aria-label*='Visual' i]"),
			(By.CSS_SELECTOR, "[aria-label*='Visuell' i]"),
			(By.CSS_SELECTOR, ".camera.icon"),
		]
		for by, selector in selectors:
			try:
				elem = self.driver.find_element(by, selector)
				if elem.is_displayed():
					return elem
			except NoSuchElementException:
				continue
		raise NoSuchElementException("Visual search camera button not found")

	def get_visual_search_file_input(self) -> WebElement:
		selectors = [
			(By.CSS_SELECTOR, "#sb_fileinput"),
			(By.CSS_SELECTOR, "input[type='file'][name*='image']"),
			(By.CSS_SELECTOR, "input[type='file']"),
		]
		for by, selector in selectors:
			try:
				return self.driver.find_element(by, selector)
			except NoSuchElementException:
				continue
		raise NoSuchElementException("Visual search file input not found")

	# ------------------------------------------------------------------
	# Cards & Activities
	# ------------------------------------------------------------------

	def get_all_misc_cards(self) -> List[WebElement]:
		try:
			container = self._container_by_id("moreactivities")
			return container.find_elements(By.TAG_NAME, "a")
		except NoSuchElementException:
			candidates = self.driver.find_elements(By.CSS_SELECTOR, "section[id*='activity' i] a, section[id*='more' i] a")
			return [c for c in candidates if c.is_displayed()]

	def extract_card_descriptions(self, card: WebElement) -> str:
		try:
			return card.find_element(By.CSS_SELECTOR, "p:nth-child(2)").text
		except NoSuchElementException:
			paragraphs = card.find_elements(By.TAG_NAME, "p")
			return paragraphs[1].text if len(paragraphs) > 1 else (card.text or "")

	def _card_status_element(self, card: WebElement) -> WebElement:
		selectors = [
			(By.CSS_SELECTOR, "div.flex.w-full.items-center.gap-2"),
			(By.CSS_SELECTOR, "[class*='items-center'][class*='gap-']"),
			(By.XPATH, ".//div[contains(@class, 'items-center')]")
		]
		for by, selector in selectors:
			try:
				return card.find_element(by, selector)
			except NoSuchElementException:
				continue
		return card

	def card_is_complete(self, card: WebElement) -> bool:
		try:
			status = self._card_status_element(card).text or card.text or ""
		except NoSuchElementException:
			return False
		return self._matches_any(status, Labels.CARD_COMPLETED)

	def get_card_point_value(self, card: WebElement) -> int:
		try:
			elem = self._card_status_element(card).find_element(By.TAG_NAME, "p")
			text = elem.text or ""
		except NoSuchElementException:
			text = card.text or ""

		digits = re.search(r"\d+", text)
		return int(digits.group()) if digits else 0

	def element_is_fully_in_viewport(self, elem: WebElement) -> bool:
		js_viewport_check = """
		var elem = arguments[0];
		var box = elem.getBoundingClientRect();
		return (
			box.top >= 0 &&
			box.left >= 0 &&
			box.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
			box.right <= (window.innerWidth || document.documentElement.clientWidth)
		);
		"""
		try:
			return self.driver.execute_script(js_viewport_check, elem)
		except Exception:
			return False

	# ------------------------------------------------------------------
	# Points Breakdown
	# ------------------------------------------------------------------

	def get_points_breakdown_button(self) -> WebElement:
		try:
			return self._button_containing(Labels.POINTS_BREAKDOWN)
		except NoSuchElementException:
			# Fallback: look for button with points icon or data attribute
			buttons = self.driver.find_elements(By.CSS_SELECTOR, "[data-bi-name*='breakdown'], button[id*='breakdown']")
			for b in buttons:
				if b.is_displayed():
					return b
			raise NoSuchElementException("Points breakdown button not found")

	def get_close_button_on_points_breakdown(self) -> WebElement:
		return self.get_generic_sidebar_close_button()

	def get_points_earned_from_searches_on_points_breakdown(self, search_type: str = "pc") -> Tuple[int, int]:
		"""Parse (earned, max) for Bing search row in breakdown panel.
		
		search_type: 'pc' (Desktop searches) or 'mobile' (Mobile searches).
		"""
		sidebar = self.get_sidebar_section()
		text = sidebar.text or ""
		lines = [line.strip() for line in text.splitlines()]

		def parse(candidate: str):
			match = re.fullmatch(r"([\d,]+)\s*/\s*([\d,]+)", candidate)
			if not match:
				return None
			return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))

		if search_type.lower() == "mobile":
			mobile_keywords = (
				"mobile search", "mobilsökning", "mobila sökningar", "mobil sökning",
				"suche auf mobilen geräten", "búsqueda en el móvil", "recherche sur mobile",
				"ricerca su cellulare"
			)
			for index, line in enumerate(lines):
				if any(k in line.lower() for k in mobile_keywords):
					for candidate in lines[index + 1:index + 4]:
						parsed = parse(candidate)
						if parsed:
							return parsed
			# If mobile search is not in breakdown sidebar (e.g. Level 1 accounts where mobile is locked)
			raise NoSuchElementException("Mobile search quota row not found in breakdown sidebar")

		# Default: PC / standard search
		pc_keywords = (
			"pc search", "datorsökning", "pc-suche", "búsqueda en pc",
			"recherche sur pc", "bing search", "bingsökning", "search", "sökning"
		)
		for index, line in enumerate(lines):
			line_lower = line.lower()
			if any(k in line_lower for k in pc_keywords):
				# Exclude lines that explicitly mention mobile
				if any(m in line_lower for m in ("mobile", "mobil", "móvil")):
					continue
				for candidate in lines[index + 1:index + 4]:
					parsed = parse(candidate)
					if parsed:
						return parsed

		# Fallback: find any fraction anywhere in panel
		match = re.search(r"([\d,]+)\s*/\s*([\d,]+)", text)
		if match:
			return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))

		raise NoSuchElementException("no points fraction found in the breakdown sidebar")

	# ------------------------------------------------------------------
	# Bonus Points
	# ------------------------------------------------------------------

	def get_bonus_button_on_dashboard(self) -> WebElement:
		return self._button_containing(Labels.READY_TO_CLAIM)

	def get_claim_bonus_points_button(self) -> WebElement:
		sidebar = self.get_sidebar_section()
		buttons = sidebar.find_elements(By.TAG_NAME, "button")

		# Exact match preferred
		for button in buttons:
			try:
				if not button.is_displayed():
					continue
				text = (button.text or "").strip().lower()
				if any(text == c.lower() for c in Labels.CLAIM):
					return button
			except StaleElementReferenceException:
				continue

		# Substring match
		for button in buttons:
			try:
				if not button.is_displayed():
					continue
				if self._matches_any(button.text or "", Labels.CLAIM):
					return button
			except StaleElementReferenceException:
				continue

		if len(buttons) >= 3:
			return buttons[2]

		raise NoSuchElementException("bonus sidebar has no claim button")

	def get_generic_sidebar_close_button(self) -> WebElement:
		sidebar = self.get_sidebar_section()
		close_selectors = [
			"button[aria-label*='lose' i]",
			"button[aria-label*='täng' i]",
			"button[aria-label*='chließen' i]",
			"button[aria-label*='errar' i]",
			"button[aria-label*='ermer' i]",
			"button[data-bi-name*='close' i]"
		]
		for selector in close_selectors:
			try:
				btn = sidebar.find_element(By.CSS_SELECTOR, selector)
				if btn.is_displayed():
					return btn
			except NoSuchElementException:
				continue

		buttons = [b for b in sidebar.find_elements(By.TAG_NAME, "button") if b.is_displayed()]
		if buttons:
			return buttons[0]

		raise NoSuchElementException("sidebar has no buttons")

	# ------------------------------------------------------------------
	# Bing Search Page
	# ------------------------------------------------------------------

	def get_bing_search_bar(self) -> WebElement:
		selectors = [
			(By.CSS_SELECTOR, "textarea#sb_form_q"),
			(By.CSS_SELECTOR, "textarea[name='q']"),
			(By.CSS_SELECTOR, "input#sb_form_q"),
			(By.CSS_SELECTOR, "input[name='q']"),
			(By.TAG_NAME, "textarea"),
		]
		for by, selector in selectors:
			try:
				elem = self.driver.find_element(by, selector)
				if elem.is_displayed():
					return elem
			except NoSuchElementException:
				continue
		raise NoSuchElementException("Bing search bar not found")

	def get_clear_bing_search_query_button(self) -> WebElement:
		selectors = [
			(By.ID, "sw_clx"),
			(By.CSS_SELECTOR, ".b_searchboxForm .b_clx"),
			(By.CSS_SELECTOR, "[aria-label*='Clear' i]"),
			(By.CSS_SELECTOR, "[aria-label*='Rensa' i]"),
			(By.CSS_SELECTOR, "[title*='Clear' i]"),
			(By.CSS_SELECTOR, "#sb_form .b_clx"),
		]
		for by, selector in selectors:
			try:
				elem = self.driver.find_element(by, selector)
				if elem.is_displayed():
					return elem
			except NoSuchElementException:
				continue
		raise NoSuchElementException("Bing clear query button not found")
