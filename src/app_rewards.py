import time
import json
import secrets
import urllib.parse
import urllib.request
from typing import Optional, Tuple
from selenium import webdriver
from selenium.webdriver.common.by import By

from constants import (
	BING_APP_CLIENT_ID,
	BING_APP_SCOPE,
	BING_APP_USER_AGENT,
	START_APP_USER_AGENT,
	REWARDS_ACTIVITIES_URL,
	OAUTH_AUTHORIZE_URL,
	OAUTH_REDIRECT_URL,
	OAUTH_TOKEN_URL,
)
import element_selectors


def get_mobile_app_access_token(driver: webdriver.Edge) -> Optional[str]:
	"""Acquire OAuth access token for Bing / Microsoft Start App using current authenticated browser session."""
	auth_params = urllib.parse.urlencode({
		"response_type": "code",
		"client_id": BING_APP_CLIENT_ID,
		"redirect_uri": OAUTH_REDIRECT_URL,
		"scope": BING_APP_SCOPE,
		"state": secrets.token_hex(16),
		"access_type": "offline_access",
	})
	auth_url = f"{OAUTH_AUTHORIZE_URL}?{auth_params}"

	main_window = driver.current_window_handle
	driver.switch_to.new_window("tab")
	code = None

	try:
		driver.get(auth_url)
		# Wait up to 12 seconds for redirection to oauth20_desktop.srf
		for _ in range(24):
			curr = driver.current_url
			if "oauth20_desktop.srf" in curr and "code=" in curr:
				parsed = urllib.parse.urlparse(curr)
				qs = urllib.parse.parse_qs(parsed.query)
				code = qs.get("code", [None])[0]
				break
			time.sleep(0.5)
	except Exception as exc:
		print(f"[WARNING] Could not navigate to mobile OAuth endpoint: {exc}")
	finally:
		try:
			driver.close()
		except Exception:
			pass
		driver.switch_to.window(main_window)

	if not code:
		print("[INFO] Mobile OAuth code not resolved (session may need prompt or 2FA).")
		return None

	# Exchange authorization code for access token
	try:
		token_payload = urllib.parse.urlencode({
			"grant_type": "authorization_code",
			"client_id": BING_APP_CLIENT_ID,
			"code": code,
			"redirect_uri": OAUTH_REDIRECT_URL,
			"scope": BING_APP_SCOPE,
		}).encode("utf-8")

		req = urllib.request.Request(
			OAUTH_TOKEN_URL,
			data=token_payload,
			headers={
				"Content-Type": "application/x-www-form-urlencoded",
				"User-Agent": BING_APP_USER_AGENT,
			}
		)
		with urllib.request.urlopen(req, timeout=15) as response:
			data = json.loads(response.read().decode("utf-8"))
			token = data.get("access_token")
			if token:
				print("[INFO] Successfully obtained Bing Mobile App access token.")
				return token
	except Exception as exc:
		print(f"[WARNING] Token exchange error: {exc}")

	return None


def execute_read_to_earn_api(access_token: str, max_articles: int = 10) -> int:
	"""Execute Read to Earn API calls (+3 points per article, up to 30 points)."""
	print(f"[INFO] Starting Read to Earn API tasks (up to {max_articles} articles)...")
	articles_read = 0
	last_balance = None

	for i in range(1, max_articles + 1):
		item_id = secrets.token_hex(32)
		payload = json.dumps({
			"amount": 1,
			"id": item_id,
			"type": 101,
			"attributes": {
				"offerid": "ENUS_readarticle3_30points"
			}
		}).encode("utf-8")

		req = urllib.request.Request(
			REWARDS_ACTIVITIES_URL,
			data=payload,
			headers={
				"Authorization": f"Bearer {access_token}",
				"User-Agent": BING_APP_USER_AGENT,
				"Content-Type": "application/json",
				"X-Rewards-ismobile": "true",
			}
		)

		try:
			with urllib.request.urlopen(req, timeout=15) as resp:
				res_data = json.loads(resp.read().decode("utf-8"))
				curr_balance = res_data.get("response", {}).get("balance")

				if last_balance is not None and curr_balance is not None and curr_balance <= last_balance:
					print(f"[INFO] Article [{i}/{max_articles}]: Daily read quota reached (balance {curr_balance} pts).")
					break

				last_balance = curr_balance
				articles_read += 1
				balance_str = f" (balance: {curr_balance} pts)" if curr_balance else ""
				print(f"[INFO] Article [{i}/{max_articles}] read (+3 pts){balance_str}")

		except Exception as exc:
			print(f"[WARNING] Read to earn error on article {i}: {exc}")
			break

		# Natural reading delay between articles
		time.sleep(4.5 + (secrets.randbelow(30) / 10.0))

	return articles_read


def execute_daily_checkin_api(access_token: str) -> bool:
	"""Execute Mobile App Daily Check-In API call (+5 to +25 points)."""
	item_id = secrets.token_hex(16)
	payload = json.dumps({
		"risk_context": {},
		"type": 103,
		"channel": "SAIOS",
		"attributes": {},
		"id": item_id,
		"amount": 1,
	}).encode("utf-8")

	req = urllib.request.Request(
		REWARDS_ACTIVITIES_URL,
		data=payload,
		headers={
			"Authorization": f"Bearer {access_token}",
			"User-Agent": START_APP_USER_AGENT,
			"Content-Type": "application/json",
			"X-Rewards-AppId": "SAIOS/33.4.440603001",
			"X-Rewards-PartnerId": "startapp",
			"X-Rewards-IsMobile": "true",
		}
	)

	try:
		with urllib.request.urlopen(req, timeout=15) as resp:
			res_data = json.loads(resp.read().decode("utf-8"))
			curr_balance = res_data.get("response", {}).get("balance")
			balance_str = f" (balance: {curr_balance} pts)" if curr_balance else ""
			print(f"[INFO] Mobile App Daily Check-In complete{balance_str}")
			return True
	except Exception as exc:
		print(f"[INFO] Mobile App Daily Check-In not applicable or already claimed today ({exc}).")
		return False


def browse_msn_news_fallback(driver: webdriver.Edge, count: int = 5):
	"""Fallback: Open MSN News in mobile viewport, scroll headlines and view comments."""
	print(f"[INFO] Running browser MSN News reading fallback ({count} articles)...")
	driver.get("https://www.msn.com/en-us/news")
	element_selectors.dismiss_cookie_and_consent_banners(driver)
	time.sleep(3.0)

	try:
		articles = driver.find_elements(By.CSS_SELECTOR, "a[href*='/news/'], a[href*='/article/']")
		valid_links = [a.get_attribute("href") for a in articles if a.get_attribute("href") and "http" in a.get_attribute("href")]
		unique_links = list(dict.fromkeys(valid_links))[:count]

		for i, link in enumerate(unique_links, start=1):
			print(f"[INFO] Reading news article [{i}/{len(unique_links)}]...")
			driver.get(link)
			element_selectors.dismiss_cookie_and_consent_banners(driver)
			time.sleep(2.0)
			# Natural human scrolling
			driver.execute_script("window.scrollBy(0, 450);")
			time.sleep(3.0)
			driver.execute_script("window.scrollBy(0, 500);")
			time.sleep(2.5)
	except Exception as exc:
		print(f"[WARNING] MSN News fallback browsing encountered error: {exc}")
