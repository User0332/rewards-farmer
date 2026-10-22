import _bootstrap  # noqa: F401

import http.client
import json
import os
import re
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ui.harness import SCRATCH, SCREENSHOT_DIR, UI_DIR, UiHarness


_harness = None
_enabled = os.environ.get("REWARDS_APP_UI_TESTS") == "1"
COMPONENTS = ("button iconButton switchControl select textField segmented timeInput timeRange weekdayPicker card stat taskList pointsChart goalCard runRow logPanel banner toast dialog emptyState progressRing accountRow riskNotice spinner icon").split()
ROUTES = {"setup": "#/setup/1", "overview": "#/overview", "stats": "#/stats", "live": "#/live", "history": "#/history", "accounts": "#/accounts", "settings": "#/settings"}


def setUpModule():
	global _harness
	if _enabled:
		_harness = UiHarness()
		try:
			_harness.start()
			_harness.wait_ready()
		except BaseException:
			_harness.stop()
			raise


def tearDownModule():
	if _harness is not None:
		_harness.stop()


def _luminance(value):
	parts = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
	parts = [n / 12.92 if n <= 0.04045 else ((n + 0.055) / 1.055) ** 2.4 for n in parts]
	return sum(n * weight for n, weight in zip(parts, (0.2126, 0.7152, 0.0722)))


def _contrast(first, second):
	a, b = sorted((_luminance(first), _luminance(second)))
	return (b + 0.05) / (a + 0.05)


def _computed_hex(color):
	channels = [round(float(value)) for value in re.findall(r"[\d.]+", color)[:3]]
	if len(channels) != 3:
		raise ValueError("Expected computed RGB color: " + color)
	return "#" + "".join(f"{value:02x}" for value in channels)


def _main_scroll_extent(browser):
	return browser.execute_script("""
		const view=document.querySelector('.app-main'),screen=view.querySelector('[data-screen]');
		view.scrollTop=0;
		const box=node=>{if(!node)return null;const r=node.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom};};
		const vb=view.getBoundingClientRect(),style=getComputedStyle(view),padding=Number.parseFloat(style.paddingBlockEnd)||0;
		const cards=[...screen.querySelectorAll('.card')].filter(node=>!node.closest('[hidden],.sr-only') && node.getClientRects().length);
		const lastCard=cards.sort((a,b)=>a.getBoundingClientRect().bottom-b.getBoundingClientRect().bottom).at(-1);
		const table=screen.querySelector('.points-chart table');
		const bottom=screen.getBoundingClientRect().bottom;
		return {screen:screen.dataset.screen,language:document.documentElement.lang,viewport:{width:innerWidth,height:innerHeight},
			clientHeight:view.clientHeight,scrollHeight:view.scrollHeight,scrollRange:view.scrollHeight-view.clientHeight,
			expectedRange:Math.max(0,Math.ceil(bottom-vb.bottom+padding)),visibleContent:box(screen),lastVisibleCard:box(lastCard),
			table:table?{box:box(table),scrollHeight:table.scrollHeight,scrollWidth:table.scrollWidth,rows:table.rows.length,
				wrapper:box(table.closest('.chart-table')),wrapperContain:table.closest('.chart-table')?getComputedStyle(table.closest('.chart-table')).contain:null,
				firstRow:box(table.rows[0]),lastRow:box(table.rows[table.rows.length-1]),position:getComputedStyle(table).position,
				display:getComputedStyle(table).display,visibility:getComputedStyle(table).visibility,ariaHidden:table.getAttribute('aria-hidden'),
				closestHidden:!!table.closest('[hidden],[aria-hidden=true]'),clipPath:getComputedStyle(table).clipPath,contain:getComputedStyle(table).contain}:null};
	""")


class SourceTests(unittest.TestCase):
	def test_selection_intent_is_in_own_stylesheet_and_initial_is_shared(self):
		base = (UI_DIR / "css/base.css").read_text(encoding="utf-8")
		components = (UI_DIR / "css/components.css").read_text(encoding="utf-8")
		history = (UI_DIR / "css/screens/history.css").read_text(encoding="utf-8")
		body = re.search(r"(?m)^body\s*\{([^}]+)\}", base).group(1)
		self.assertRegex(body, r"user-select\s*:\s*none\s*;")
		self.assertRegex(body, r"cursor\s*:\s*default\s*;")
		rules = [(selectors.split(","), declarations) for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]+)\}", base)]
		for selector in ("input", "textarea", ".log-text", ".history-technical pre"):
			with self.subTest(selector=selector):
				self.assertTrue(any(selector in [item.strip() for item in selectors] and re.search(r"user-select\s*:\s*text\s*;", declarations) for selectors, declarations in rules))
		self.assertRegex(components, r"(?m)^\.account-row-email\s*\{[^}]*user-select:\s*text\s*;")
		self.assertRegex(history, r"\.history-technical pre\s*\{[^}]*user-select:\s*text\s*;")
		page = (UI_DIR / "index.html").read_text(encoding="utf-8")
		self.assertIn("style-src 'self'", page)
		self.assertNotIn("'unsafe-inline'", page)
		main = (UI_DIR / "js/main.js").read_text(encoding="utf-8")
		kit = (UI_DIR / "js/components/kit.js").read_text(encoding="utf-8")
		self.assertIn('import { accountAvatar } from "./components/kit.js";', main)
		self.assertIn("accountAvatar(first)", main)
		self.assertIn("export function accountInitial(account)", kit)
		self.assertIn("text: accountInitial(account)", kit)
		self.assertIn("accountAvatar(a)", kit)
		self.assertNotIn("[...first.label]", main)

	def test_token_slot_and_contrast(self):
		text = (UI_DIR / "css/tokens.css").read_text(encoding="utf-8")
		properties = "c-bg c-surface c-surface-2 c-surface-hover c-avatar c-avatar-text c-avatar-border c-border c-text c-text-2 c-text-3 c-accent c-accent-hover c-on-accent c-success c-warning c-danger c-info c-focus c-chart-1 c-chart-2 c-scrim font-sans font-mono fs-1 fs-2 fs-3 fs-4 fs-5 fs-6 fs-7 fw-regular fw-medium fw-bold lh-tight lh-normal sp-1 sp-2 sp-3 sp-4 sp-5 sp-6 sp-7 sp-8 r-s r-m r-l r-pill shadow-1 shadow-2 dur-fast dur-normal ease nav-width content-max".split()
		self.assertEqual({"--" + name for name in properties}, set(re.findall(r"(--[\w-]+)\s*:", text)))
		for theme in ("dark", "light"):
			block = re.search(r'\[data-theme="' + theme + r'"\]\s*\{([^}]+)', text).group(1)
			colors = dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", block))
			for foreground in ("c-text", "c-text-2", "c-text-3"):
				for background in ("c-bg", "c-surface", "c-surface-2", "c-surface-hover"):
					self.assertGreaterEqual(_contrast(colors[foreground], colors[background]), 4.5, (theme, foreground, background))
			self.assertGreaterEqual(_contrast(colors["c-on-accent"], colors["c-accent"]), 4.5)
			self.assertGreaterEqual(_contrast(colors["c-focus"], colors["c-surface"]), 3)
			for background in ("c-bg", "c-surface"):
				self.assertGreaterEqual(_contrast(colors["c-avatar-border"], colors[background]), 3, (theme, "avatar boundary", background))
			self.assertGreaterEqual(_contrast(colors["c-avatar-text"], colors["c-avatar"]), 4.5, (theme, "avatar initials, normal-text AA"))
			if theme == "light":
				for background in ("c-bg", "c-surface"):
					self.assertGreaterEqual(_contrast(colors["c-avatar"], colors[background]), 3)

	def test_structure_catalogs_and_component_modules(self):
		page = (UI_DIR / "index.html").read_text(encoding="utf-8")
		self.assertIn("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'", page)
		for identity in ("app", "app-notices", "app-nav", "view", "toasts", "dialog"):
			self.assertEqual(1, page.count('id="' + identity + '"'))
		self.assertNotIn("app-bar", page)
		self.assertRegex(page, r'class="app-content">\s*<div id="app-notices"[^>]*></div>\s*<main id="view"')
		self.assertNotIn("<style", page)
		self.assertNotIn("onclick=", page)
		for component in COMPONENTS:
			self.assertTrue((UI_DIR / "js/components" / (component + ".js")).is_file(), component)
		for namespace in ("common", "shell"):
			catalog = json.loads((UI_DIR / "i18n/en" / (namespace + ".json")).read_text(encoding="utf-8"))
			self.assertEqual(namespace, catalog["namespace"])
			for key, entry in catalog["messages"].items():
				self.assertTrue(entry["note"].strip(), key)
				self.assertNotRegex(json.dumps(entry, ensure_ascii=False), "[\u2013\u2014]")
		for name in set(ROUTES) - {"stats"}:
			self.assertTrue((UI_DIR / "mock/shell" / (name + ".json")).is_file())
		for path in (UI_DIR / "mock/shell").glob("*.json"):
			self.assertIsInstance(json.loads(path.read_text(encoding="utf-8"))["methods"], dict)
		defaults = json.loads((UI_DIR / "mock/shell/defaults.json").read_text(encoding="utf-8"))
		errors = json.loads((UI_DIR / "i18n/en/errors.json").read_text(encoding="utf-8"))["messages"]
		for account in defaults["methods"]["get_overview"]["data"]["accounts"]:
			for task in account["tasks_today"]:
				if task["reason_key"]:
					self.assertIn(task["reason_key"], errors)
		for page in json.loads((UI_DIR / "mock/shell/component-cases.json").read_text(encoding="utf-8"))["cases"]["log_pages"]:
			self.assertEqual(page["data"]["next_offset"] - page["offset"], len(page["data"]["text"].encode("utf-8")))
		common = json.loads((UI_DIR / "i18n/en/common.json").read_text(encoding="utf-8"))["messages"]
		self.assertEqual("Not checked yet", common["common.task_status.none"]["text"])
		self.assertEqual("Signed out", common["common.account.signed_out"]["text"])


@unittest.skipUnless(_enabled, "Set REWARDS_APP_UI_TESTS=1 for isolated loopback shell acceptance")
class ShellTests(unittest.TestCase):
	def test_r3_capture_measure_and_raster(self):
		requested = os.environ.get("RF_F76_R3_SCRATCH")
		if requested is None:
			self.js("const shared=await import('./js/schedule-time.js'),overview=await import('./js/screens/overview.js');assert(shared.scheduleTime===overview.scheduleTime,'R3 shared formatter remains wired');")
			return
		root = Path(requested).resolve()
		self.assertTrue(root.is_relative_to(Path(tempfile.gettempdir()).resolve()), "Capture injection is restricted to test scratch")
		self.assertTrue((root / "capture.py").is_file(), "Capture entrypoint must exist in test scratch")
		import importlib.util
		spec = importlib.util.spec_from_file_location("f76_r3_capture", root / "capture.py")
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		module.run(_harness, root)

	def setUp(self):
		_harness.viewport(1280, 800)
		_harness.open("#/overview", "shell/overview").wait_ready()

	def js(self, body):
		result = _harness.driver.execute_async_script("""
			const done = arguments[arguments.length - 1];
			(async () => {
				const assert = (ok, label) => { if (!ok) throw Error(label); };
				const strip = value => value.replace(/[\u2068\u2069]/g, '');
				const c = await import('./js/components/index.js');
				const b = await import('./js/bridge.js');
				const s = await import('./js/store.js');
				const r = await import('./js/router.js');
				const i = await import('./js/i18n.js');
			""" + body + """
				return {ok: true};
			})().then(done).catch(error => done({ok:false,error:String(error),stack:error.stack}));
		""")
		self.assertTrue(result["ok"], result)

	def test_scroll_measurement_overview_hidden_table(self):
		rows = []
		for width, height in ((1920, 1080), (1440, 900)):
			_harness.viewport(width, height)
			for lang in ("en", "x-rtl"):
				_harness.open("#/overview", "overview/multiple", lang=lang).wait_ready()
				from selenium.webdriver.support.ui import WebDriverWait
				WebDriverWait(_harness.driver, 10).until(lambda browser: browser.execute_script("return !!document.querySelector('[data-screen=overview] .points-chart table')"))
				row = _main_scroll_extent(_harness.driver)
				rows.append(row)
				print("overview scroll measurement=" + json.dumps(row), flush=True)
				self.assertGreater(row["table"]["rows"], 1)
				self.assertEqual([], _harness.errors())
		SCRATCH.mkdir(parents=True, exist_ok=True)
		(SCRATCH / "overview-scroll-measurements.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

	def test_real_content_bounds_scroll_and_chart_table_accessibility(self):
		rows = []
		fixtures = (("overview", "overview/multiple"), ("live", "live/running"), ("history", "history/mixed"), ("accounts", "accounts/list"), ("settings", "settings/automatic"))
		from selenium.webdriver.support.ui import WebDriverWait
		for width, height in ((1920, 1080), (1440, 900)):
			_harness.viewport(width, height)
			for language in ("en", "x-rtl"):
				for screen, fixture in fixtures:
					with self.subTest(screen=screen, size=(width, height), language=language):
						_harness.open("#/" + screen, fixture, lang=language).wait_ready()
						WebDriverWait(_harness.driver, 10).until(lambda browser: browser.execute_script("return document.querySelector('[data-screen='+arguments[0]+']') && !document.querySelector('[data-screen='+arguments[0]+'] .spinner')", screen))
						row = _main_scroll_extent(_harness.driver)
						rows.append(row)
						self.assertLessEqual(row["scrollRange"], row["expectedRange"] + 2, row)
						if row["expectedRange"] == 0:
							self.assertEqual(0, row["scrollRange"], "A screen whose content fits must not scroll")
						if screen == "overview":
							table = row["table"]
							self.assertEqual(31, table["rows"])
							self.assertEqual("table", table["display"])
							self.assertEqual("visible", table["visibility"])
							self.assertIsNone(table["ariaHidden"])
							self.assertFalse(table["closestHidden"])
							self.assertLessEqual(table["wrapper"]["height"], 1)
							self.assertLessEqual(table["wrapper"]["width"], 1)
							self.assertIn("strict", table["wrapperContain"])
							document = _harness.driver.execute_cdp_cmd("DOM.getDocument", {})
							node = _harness.driver.execute_cdp_cmd("DOM.querySelector", {"nodeId":document["root"]["nodeId"], "selector":".points-chart table"})
							ax = _harness.driver.execute_cdp_cmd("Accessibility.getPartialAXTree", {"nodeId":node["nodeId"], "fetchRelatives":True})
							self.assertTrue(any(item.get("role", {}).get("value") == "table" and not item.get("ignored", True) for item in ax["nodes"]), ax)
							row["accessibilityTablePresent"] = True
						print("content scroll proof=" + json.dumps(row), flush=True)
						self.assertEqual([], _harness.errors())
		SCRATCH.mkdir(parents=True, exist_ok=True)
		(SCRATCH / "all-screen-scroll-extents.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
		_harness.viewport(1920, 1080)
		_harness.open("#/overview", "overview/multiple").wait_ready()
		path = _harness.screenshot("overview-contained-table-dark-en-1920x1080")
		print("Contained table screenshot=" + path, flush=True)

	def test_f60_own_selection_styles_and_control_cursors(self):
		_harness.open("#/overview", "shell/components").wait_ready()
		self.js("""
			const body=getComputedStyle(document.body);
			assert(body.userSelect==='none' && body.cursor==='default','own body selection intent without injected CSS');
			assert(document.querySelector('link[href="css/base.css"]') && !document.querySelector('style'),'loaded self stylesheet, no vendor inline style needed');
			const host=document.createElement('section');document.querySelector('#view').append(host);
			const row=c.accountRow.create({account:{id:'selection-proof',label:'Synthetic account',name:'Alex',email:'alex@example.com',balance:null,signed_in:true,paused:false}});
			const textarea=document.createElement('textarea');textarea.value='Synthetic editable text';
			const technical=document.createElement('details');technical.className='history-technical';technical.open=true;
			const summary=document.createElement('summary');summary.textContent='Synthetic details';
			const pre=document.createElement('pre');pre.textContent='Synthetic diagnostic text';technical.append(summary,pre);
			const disabledInput=document.createElement('input');disabledInput.disabled=true;
			host.append(row,textarea,technical,disabledInput);
			const nodes=[document.querySelector('.log-text'),row.querySelector('.account-row-email'),row.querySelector('.account-row-email bdi'),document.querySelector('#demo-text'),textarea,pre];
			for(const node of nodes)assert(getComputedStyle(node).userSelect==='text','explicit text selection on '+node.className+' '+node.tagName);
			assert(getComputedStyle(document.querySelector('#demo-text')).cursor==='text' && getComputedStyle(textarea).cursor==='text','editable control cursors');
			assert(getComputedStyle(document.querySelector('[data-component=button] button:not(:disabled)')).cursor==='pointer','button cursor preserved');
			assert(getComputedStyle(document.querySelector('[data-component=button] button:disabled')).cursor==='not-allowed','disabled button cursor preserved');
			assert(getComputedStyle(disabledInput).cursor==='not-allowed','disabled input cursor preserved');
			assert(getComputedStyle(document.querySelector('.nav-link')).cursor==='pointer' && getComputedStyle(summary).cursor==='pointer','link and summary cursors preserved');
			assert(getComputedStyle(document.querySelector('#demo-select')).cursor==='default','select keeps native default cursor');
			assert(getComputedStyle(row.querySelector('.account-row-label')).userSelect==='none','ordinary text stays unselectable');
			assert(!document.querySelector('#app [style]'),'no CSP inline-style escape');
		""")
		self.assertEqual([], _harness.errors())

	def test_http_reuses_connection_for_two_page_loads(self):
		before = _harness.connection_stats()
		with closing(http.client.HTTPConnection("127.0.0.1", _harness.server.server_port, timeout=10)) as client:
			client.request("GET", "/index.html?reuse=first")
			first = client.getresponse()
			self.assertEqual(200, first.status)
			first_page = first.read()
			self.assertEqual(int(first.getheader("Content-Length")), len(first_page))
			self.assertIn(b'<main id="view"', first_page)
			first_counts = _harness.connection_stats()
			new_connections = set(first_counts["requests_by_connection"]) - set(before["requests_by_connection"])
			self.assertEqual(1, len(new_connections), "One test client must open one accepted connection")
			identity = new_connections.pop()
			client.request("GET", "/index.html?reuse=second")
			second = client.getresponse()
			self.assertEqual(200, second.status)
			self.assertEqual(first_page, second.read())
			second_counts = _harness.connection_stats()
			self.assertEqual(first_counts["requests_by_connection"][identity] + 1,
				second_counts["requests_by_connection"][identity], "Second page load must reuse the first accepted connection")
			self.assertEqual(first_counts["connections"], second_counts["connections"], "Second page load must not accept another connection")
			print(f"page_reuse_connection={identity} page_reuse_requests={second_counts['requests_by_connection'][identity]}", flush=True)
		self.assertEqual([], _harness.errors())

	def test_http_every_response_path_has_length_or_closes(self):
		with closing(http.client.HTTPConnection("127.0.0.1", _harness.server.server_port, timeout=10)) as client:
			client.request("GET", "/index.html")
			response = client.getresponse()
			modified = response.getheader("Last-Modified")
			self.assertEqual(int(response.getheader("Content-Length")), len(response.read()))
			cases = [
				("GET", "/css/screens/__harness_missing__.css", {}, 200),
				("HEAD", "/index.html", {}, 200),
				("GET", "/img", {}, 301),
				("GET", "/img/", {}, 200),
				("GET", "/__harness_missing__.txt", {}, 404),
				("HEAD", "/__harness_missing__.txt", {}, 404),
				("POST", "/index.html", {"Content-Length":"0"}, 501),
				("GET", "/index.html", {"If-Modified-Since":modified}, 304),
			]
			for method, path, headers, status in cases:
				with self.subTest(method=method, path=path, status=status):
					client.request(method, path, headers=headers)
					response = client.getresponse()
					self.assertEqual(status, response.status)
					length = response.getheader("Content-Length")
					self.assertTrue(length is not None or response.will_close, "Final response needs a length or Connection: close")
					body = response.read()
					if method == "HEAD" or status == 304:
						self.assertEqual(b"", body)
					elif length is not None:
						self.assertEqual(int(length), len(body))
		self.assertEqual([], _harness.errors())

	def test_f68_sidebar_picture_cache_fallback_and_revision(self):
		_harness.open("#/accounts", "pictures/accounts").wait_ready()
		self.js("""
			const wait=async fn=>{for(let n=0;n<200;n++){if(fn())return;await new Promise(resolve=>setTimeout(resolve,10));}throw Error('picture timeout');};
			const data=await (await fetch('./mock/pictures/accounts.json')).json(),photo=data.methods.get_account_picture[0].data.data_url;
			await wait(()=>document.querySelector('.nav-account .avatar img')?.naturalWidth>0);
			const image=document.querySelector('.nav-account .avatar img');
			assert(image.alt==='' && image.getAttribute('src')===photo && image.naturalWidth===64,'fixture picture decoded, decorative and synthetic');
			const original=s.get('accounts')[0],calls=[],pending=[];
			window.pywebview.api.get_account_picture=req=>{calls.push(req);return new Promise(resolve=>pending.push(resolve));};
			const account={...original,id:'cache-check',picture_rev:'r1'},set=value=>s.set('accounts',[value]);
			set(account);const row=c.accountRow.create({account});document.querySelector('#view').append(row);
			assert(document.querySelector('.nav-account .avatar').textContent==='T' && !row.querySelector('img'),'initial during load');
			await wait(()=>calls.length===1);assert(JSON.stringify(calls[0])===JSON.stringify({account_id:'cache-check'}),'one exact request shared by sidebar and row');
			pending.shift()({ok:true,data:{account_id:'cache-check',rev:'r1',data_url:photo}});
			await wait(()=>document.querySelector('.nav-account .avatar img')?.naturalWidth>0 && row.querySelector('img')?.naturalWidth>0);
			const avatar=document.querySelector('.nav-account .avatar'),box=avatar.getBoundingClientRect(),pic=avatar.querySelector('img').getBoundingClientRect();
			assert(Math.abs(box.width-48)<1 && Math.abs(box.height-48)<1 && pic.width<=box.width,'existing round avatar size stays');
			set({...account,label:'Renamed'});await wait(()=>document.querySelector('.nav-account .avatar img')?.naturalWidth>0);assert(calls.length===1,'same rev cache survives rerender');
			set({...account,picture_rev:null});assert(!document.querySelector('.nav-account img') && document.querySelector('.nav-account .avatar').textContent==='T' && calls.length===1,'null rev initial with no request');
			set({...account,picture_rev:'r2'});await wait(()=>calls.length===2);assert(!document.querySelector('.nav-account img'),'changed rev keeps initial while loading');
			pending.shift()({ok:true,data:{account_id:'cache-check',rev:'r2',data_url:photo}});await wait(()=>document.querySelector('.nav-account .avatar img')?.naturalWidth>0);
			set({...account,picture_rev:'missing'});await wait(()=>calls.length===3);pending.shift()({ok:true,data:{account_id:'cache-check',rev:'missing',data_url:null}});await new Promise(resolve=>setTimeout(resolve,0));
			set({...account,picture_rev:'missing'});await new Promise(resolve=>setTimeout(resolve,0));assert(calls.length===3 && !document.querySelector('.nav-account img'),'negative picture cached');
			set({...account,picture_rev:'failed'});await wait(()=>calls.length===4);pending.shift()({ok:false,error:{code:'internal',message_key:'errors.app.internal',params:{},detail:null,fields:null}});await new Promise(resolve=>setTimeout(resolve,0));
			assert(document.querySelector('.nav-account .avatar').textContent==='T' && !document.querySelector('#toasts .toast'),'failed picture silent fallback');
			set({...account,picture_rev:'stale'});await wait(()=>calls.length===5);const late=pending.shift();
			set({...account,picture_rev:'fresh'});await wait(()=>calls.length===6);late({ok:true,data:{account_id:'cache-check',rev:'stale',data_url:photo}});await new Promise(resolve=>setTimeout(resolve,0));
			assert(!document.querySelector('.nav-account img'),'old revision cannot replace new initial');
			pending.shift()({ok:true,data:{account_id:'different-account',rev:'fresh',data_url:photo}});await new Promise(resolve=>setTimeout(resolve,0));assert(!document.querySelector('.nav-account img'),'identity mismatch rejected');
			set({...account,picture_rev:'wrong-rev'});await wait(()=>calls.length===7);pending.shift()({ok:true,data:{account_id:'cache-check',rev:'old',data_url:photo}});await new Promise(resolve=>setTimeout(resolve,0));assert(!document.querySelector('.nav-account img'),'reply rev mismatch rejected');
			set({...account,picture_rev:'external-url'});await wait(()=>calls.length===8);pending.shift()({ok:true,data:{account_id:'cache-check',rev:'external-url',data_url:'https://example.com/portrait.png'}});await new Promise(resolve=>setTimeout(resolve,0));assert(!document.querySelector('.nav-account img'),'only PNG data URLs accepted');
		""")
		self.assertEqual([], _harness.errors())

	def test_avatar_computed_text_and_boundary_contrast(self):
		def values():
			return _harness.driver.execute_script("""
				const root=getComputedStyle(document.documentElement);
				return [...document.querySelectorAll('.avatar')].map(node=>{
					const style=getComputedStyle(node);
					return {text:style.color,disc:style.backgroundColor,border:style.borderTopColor,
						background:root.getPropertyValue('--c-bg').trim(),surface:root.getPropertyValue('--c-surface').trim()};
				});
			""")

		def letter_contrast(avatar):
			return _contrast(_computed_hex(avatar["text"]), _computed_hex(avatar["disc"]))

		def assert_letter(avatar):
			self.assertGreaterEqual(letter_contrast(avatar), 4.5, "computed avatar letter/disc must meet normal-text AA")

		for theme in ("dark", "light"):
			_harness.open("#/overview", "shell/components", theme).wait_ready()
			computed = values()
			self.assertTrue(computed)
			for index, avatar in enumerate(computed):
				with self.subTest(theme=theme, avatar=index):
					text, disc, border = (_computed_hex(avatar[key]) for key in ("text", "disc", "border"))
					ratios = {"letter":_contrast(text, disc), "disc_bg":_contrast(disc, avatar["background"]), "disc_surface":_contrast(disc, avatar["surface"]), "border_bg":_contrast(border, avatar["background"]), "border_surface":_contrast(border, avatar["surface"])}
					assert_letter(avatar)
					self.assertGreaterEqual(max(ratios["disc_bg"], ratios["border_bg"]), 3)
					self.assertGreaterEqual(max(ratios["disc_surface"], ratios["border_surface"]), 3)
					print("avatar computed contrast " + theme + ": " + json.dumps(ratios), flush=True)
			if theme == "light":
				original = _harness.driver.execute_script("""
					const sheet=[...document.styleSheets].find(sheet=>sheet.href?.endsWith('/css/tokens.css'));
					const rule=[...sheet.cssRules].find(rule=>rule.selectorText==='[data-theme="light"]');
					const prior=rule.style.getPropertyValue('--c-avatar');
					rule.style.setProperty('--c-avatar','#888888');return prior;
				""")
				try:
					mutated = values()[0]
					with self.assertRaisesRegex(AssertionError, "computed avatar letter/disc"):
						assert_letter(mutated)
					print(f"avatar mutation #888888: expected computed assertion failure at {letter_contrast(mutated):.4f}:1", flush=True)
				finally:
					_harness.driver.execute_script("""
						const sheet=[...document.styleSheets].find(sheet=>sheet.href?.endsWith('/css/tokens.css'));
						[...sheet.cssRules].find(rule=>rule.selectorText==='[data-theme="light"]').style.setProperty('--c-avatar',arguments[0]);
					""", original)
				assert_letter(values()[0])
				self.assertEqual("#707070", _computed_hex(values()[0]["disc"]).upper())
			self.assertEqual([], _harness.errors())

	def test_bridge_store_routes_focus_and_errors(self):
		self.js("""
			assert(r.parse('#/setup/3').params.step === 3, 'setup params');
			assert(r.parse('#/history/run-7').params.runId === 'run-7', 'history params');
			assert(r.parse('#/settings/schedule').params.section === 'schedule', 'settings params');
			for(const hash of ['#/setup/0','#/settings/no','#/bogus','#/overview/extra','#/history/%']) assert(r.parse(hash)===null,'reject '+hash);
			let count=0; const off=s.on('test',()=>count++); s.set('test',1); off(); s.set('test',2); assert(count===1 && s.get('test')===2,'store unsubscribe');
			let pushed=0; const unsub=b.on('test',d=>pushed=d); window.rf.receive([{topic:'test',data:3}]); unsub(); window.rf.receive([{topic:'test',data:4}]); assert(pushed===3,'bridge unsubscribe');
			const original=window.pywebview.api.pick_path;
			window.pywebview.api.pick_path=async()=>({ok:false,error:{code:'bad_request',message_key:'errors.app.bad_request',params:{},detail:'synthetic',fields:{path:'bad_request'}}});
			try { await b.call('pick_path',{}); assert(false,'must reject'); } catch(error) { assert(error instanceof b.BridgeError && error.code==='bad_request' && error.detail==='synthetic' && error.fields.path==='bad_request','BridgeError shape'); }
			window.pywebview.api.pick_path=original;
			assert(document.activeElement.id==='view','navigation focus');
			assert(document.title===i.t('shell.route.overview.title'),'mock title');
			assert(document.querySelectorAll('.nav-link[aria-current="page"]').length===1,'one selected route');
			assert(document.querySelectorAll('.nav-link').length===6 && document.querySelector('.nav-link[data-screen=stats]').getAttribute('href')==='#/stats','F67six nav routes include Stats');
			assert(!document.querySelector('.nav-account .account-count'),'F76 footer keeps identity without redundant count');
			assert(r.parse('#/stats').screen==='stats' && r.parse('#/stats/extra')===null,'F67 Stats has no route param');
			assert(document.querySelectorAll('.task-row').length===7,'seven rows');
			assert(!document.getElementById('app-bar'),'no app bar');
			assert(document.querySelectorAll('#app img[src="img/app-icon.svg"]').length===1 && document.querySelector('.nav-brand img'),'one nav mark');
			assert(document.querySelector('.app-content').firstElementChild.id==='app-notices','content notice placement');
			assert(document.querySelector('#app-notices').getBoundingClientRect().height===0,'empty notices no height');
			assert(getComputedStyle(document.querySelector('.button')).outlineStyle!=='none' || getComputedStyle(document.documentElement).getPropertyValue('--c-focus').trim(),'focus token');
		""")
		self.assertEqual([], _harness.errors())

	def test_live_push_invariants_and_overview_refresh(self):
		self.js("""
			const run={run_id:'synthetic-push',active:true,accounts:[]};
			window.rf.receive([{topic:'run.started',data:run}]);
			assert(s.get('live').run.run_id===run.run_id && s.get('live').last===null && s.get('live').log.next_offset===0,'start shape');
			assert(!document.querySelector('[data-screen=live] .nav-active').hidden,'active nav dot');
			assert(document.querySelector('[data-screen=live] .sr-only').textContent===i.t('shell.nav.live_active'),'active nav accessible label');
			window.rf.receive([{topic:'run.log',data:{run_id:'other',offset:0,next_offset:7,text:'foreign'}}]); assert(s.get('live').log.text==='','foreign log');
			window.rf.receive([{topic:'run.log',data:{run_id:run.run_id,offset:0,next_offset:8,text:'own text'}}]); await new Promise(resolve=>setTimeout(resolve,0));
			window.rf.receive([{topic:'run.snapshot',data:{...run,points_earned:50}}]); assert(s.get('live').log.text==='own text','snapshot keeps log');
			let tailCall;
			window.pywebview.api.get_run_log=async request=>{tailCall=request;return {ok:true,data:{text:'é'.repeat(262144),next_offset:700008,eof:true,size:700008}};};
			window.rf.receive([{topic:'run.log',data:{run_id:run.run_id,offset:8,next_offset:700008,text:'é'.repeat(350000)}}]); await new Promise(resolve=>setTimeout(resolve,0));
			assert(tailCall.offset===700008-524288 && tailCall.max_bytes===524288 && tailCall.run_id===run.run_id,'server-backed cap read');
			assert(s.get('live').log.text==='é'.repeat(262144) && s.get('live').log.next_offset===700008,'cap returns exact server text');
			tailCall=null;window.rf.receive([{topic:'run.log',data:{run_id:run.run_id,offset:700008,next_offset:700010,text:'é'}}]);await new Promise(resolve=>setTimeout(resolve,0));assert(tailCall===null && s.get('live').log.next_offset===700010,'hysteresis no reread on following push');
			const final={...run,active:false,points_earned:150};
			let called=0; window.pywebview.api.get_live=async()=>{throw Error('get_live forbidden on finish');};
			window.pywebview.api.get_run=async request=>{called++; assert(request.run_id===run.run_id,'final id'); return {ok:true,data:final};};
			window.rf.receive([{topic:'run.finished',data:{run_id:run.run_id,result:'ok'}}]);
			await new Promise(resolve=>setTimeout(resolve,0));
			assert(called===1 && s.get('live').run.active===false && s.get('live').last.result==='ok','finish final detail');
			assert(document.querySelector('[data-screen=live] .nav-active').hidden,'finished nav dot');
			const data={...s.get('overview'),generated_at:'2026-10-14T22:00:00Z'};
			window.pywebview.api.get_overview=async()=>({ok:true,data}); window.rf.receive([{topic:'overview.changed',data:{}}]); await new Promise(resolve=>setTimeout(resolve,0)); assert(s.get('overview').generated_at===data.generated_at,'overview refresh');
			window.rf.receive([{topic:'theme.changed',data:{theme:'light'}}]); assert(document.documentElement.dataset.theme==='light','theme push');
			window.rf.receive([{topic:'navigate',data:{route:'#/history'}}]); await new Promise(resolve=>setTimeout(resolve,20)); assert(window.location.hash==='#/history' && document.querySelector('#view').dataset.route==='#/history','canonical navigate push');
		""")
		self.assertEqual([], _harness.errors())

	def test_update_banner_actions_and_empty_layout(self):
		self.js("""
			let opened=null, dismissed=0;
			window.pywebview.api.open_external=async request=>{opened=request.url;return {ok:true,data:{}};};
			window.pywebview.api.dismiss_update=async request=>{assert(request.version==='0.2.0','dismiss exact version');dismissed++;return {ok:true,data:{}};};
			const update={available:true,latest:'0.2.0',url:'https://github.com/User0332/rewards-farmer/releases/tag/v0.2.0'};
			window.rf.receive([{topic:'update.available',data:update}]);
			const region=document.getElementById('app-notices');
			assert(region.querySelector('.banner') && region.getBoundingClientRect().height>0,'visible shared update banner');
			assert(region.textContent.includes('0.2.0'),'update version');
			region.querySelector('.button:not(.icon-button)').click(); await new Promise(resolve=>setTimeout(resolve,0));
			assert(opened===update.url,'exact open_external URL');
			region.querySelector('.icon-button').click(); await new Promise(resolve=>setTimeout(resolve,0));
			assert(dismissed===1 && s.get('update')===null && region.children.length===0 && region.getBoundingClientRect().height===0,'dismiss and collapse');
		""")
		self.assertEqual([], _harness.errors())

	def test_component_gallery_keyboard_updates_and_data_safety(self):
		_harness.open("#/overview", "shell/components").wait_ready()
		self.js("""
			assert(document.querySelectorAll('[data-component]').length===25,'25 demo fixtures');
			const control=document.querySelector('#demo-switch'); control.click(); assert(control.getAttribute('aria-checked')==='false' && document.querySelector('#view').dataset.switchValue==='false','switch');
			const radio=document.querySelector('#demo-segmented [role=radio]'); radio.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true})); assert(document.querySelector('#demo-segmented [aria-checked=true]').dataset.value==='b','segmented arrows');
			const label='shell.demo.label'; const host=document.createElement('div'); document.querySelector('#view').append(host);
			let n=0; let button=c.button.create({labelKey:label,onClick:()=>n++}); host.append(button); button=c.button.update(button,{labelKey:label,onClick:()=>n+=10}); button.click(); assert(n===10 && button.textContent,'updated control listener');
			const tasks=c.taskList.create({tasks:[{task:'daily_set',status:'ok',still_open:true}]}); assert(tasks.querySelector('.task-row').dataset.status==='open' && tasks.textContent.includes(i.t('common.task_status.open')),'open not checked');
			const chart=document.querySelector('[data-component=pointsChart]'); assert(chart.querySelectorAll('.chart-line').length===2,'missing-day gaps'); assert(chart.querySelectorAll('tbody tr').length===30,'accessible every-day table');
			assert(chart.querySelector('.chart-tick').textContent==='Sep 15','month-day tick, no year');
			assert(chart.querySelector('.chart-latest').textContent===i.formatNumber(12340),'latest value label');
			const prior=s.get('appInfo'); s.set('appInfo',{...prior,format_locale:'de-DE'});
			const regional=c.pointsChart.create({series:[{date:'2026-09-15',balance:100},{date:'2026-09-16',balance:null}],mode:'balance'});
			assert(regional.querySelector('.chart-tick').textContent===new Intl.DateTimeFormat('de-DE',{month:'short',day:'numeric'}).format(new Date(2026,8,15)),'format locale governs tick');
			assert(regional.querySelector('.chart-latest').textContent===i.formatNumber(100),'latest measured point with trailing gap');
			s.set('appInfo',prior);
			const log=document.querySelector('.log-panel'); assert(log.dir==='ltr' && log.querySelector('pre').dir==='ltr','LTR logs');
			const labelData='<script>synthetic</script>'; const account=c.accountRow.create({account:{id:'test',label:labelData,balance:null,paused:false,signed_in:null}}); assert(!account.querySelector('script') && account.textContent.includes(labelData),'user label is text');
			const icon=c.icon.create({name:'back'}); assert(icon.classList.contains('icon-directional'),'directional registry');
			const invoker=document.querySelector('[data-component=dialog] button'); invoker.focus(); invoker.click(); assert(document.querySelector('#dialog').open,'dialog open');
		""")
		from selenium.webdriver.common.keys import Keys
		_harness.driver.switch_to.active_element.send_keys(Keys.ESCAPE)
		self.js("""
			assert(!document.querySelector('#dialog').open,'Escape closes');
			assert(document.activeElement.closest('[data-component=dialog]'),'focus restored');
			document.querySelector('[data-component=toast] button').click(); assert(document.querySelector('#toasts .toast'),'toast shown'); document.querySelector('#toasts .icon-button').click(); assert(!document.querySelector('#toasts .toast'),'toast dismiss');
		""")
		self.assertEqual([], _harness.errors())

	def test_dialog_real_tab_wrap_escape_and_removed_opener(self):
		self.js("""
			const opener=document.createElement('button');opener.id='dialog-opener';opener.textContent=i.t('common.action.edit');document.querySelector('#view').append(opener);opener.focus();
			window.__dialogResult=null;
			c.dialog.open({titleKey:'shell.demo.dialog.title',bodyKey:'shell.demo.dialog.body',actions:[{id:'first',labelKey:'common.action.cancel'},{id:'disabled',labelKey:'common.action.save',disabled:true},{id:'last',labelKey:'common.action.remove'}]}).then(result=>{window.__dialogResult=result;});
			const buttons=[...document.querySelectorAll('#dialog button')];buttons[0].id='dialog-first';buttons[1].id='dialog-disabled';buttons[2].id='dialog-last';
			window.__dialogTabEvents=[];
			window.__dialogTabSpy=event=>{if(event.key==='Tab')window.__dialogTabEvents.push(event.defaultPrevented);};document.addEventListener('keydown',window.__dialogTabSpy);
			assert(document.activeElement.id==='dialog-first','initial dialog focus');
		""")
		from selenium.webdriver.common.keys import Keys
		for expected in ("dialog-last", "dialog-first", "dialog-last", "dialog-first"):
			_harness.driver.switch_to.active_element.send_keys(Keys.TAB)
			self.assertEqual(expected, _harness.driver.execute_script("return document.activeElement.id"))
		for expected in ("dialog-last", "dialog-first", "dialog-last", "dialog-first"):
			_harness.driver.switch_to.active_element.send_keys(Keys.SHIFT, Keys.TAB)
			self.assertEqual(expected, _harness.driver.execute_script("return document.activeElement.id"))
		self.js("assert(window.__dialogTabEvents.length===8 && window.__dialogTabEvents.every(Boolean),'real Tab defaults suppressed at modal boundary');document.querySelector('#dialog-opener').remove();")
		_harness.driver.switch_to.active_element.send_keys(Keys.ESCAPE)
		from selenium.webdriver.support.ui import WebDriverWait
		WebDriverWait(_harness.driver, 5).until(lambda browser: browser.execute_script("return window.__dialogResult==='cancel' && !document.getElementById('dialog').open"))
		self.js("assert(document.activeElement.id==='view','removed opener falls back to view');document.removeEventListener('keydown',window.__dialogTabSpy);")
		self.assertEqual([], _harness.errors())

	def test_dialog_queued_opens_and_pending_close_have_own_results(self):
		self.js("""
			const props=id=>({titleKey:'shell.demo.dialog.title',bodyKey:'shell.demo.dialog.body',actions:[{id,labelKey:'common.action.continue'}]});
			const first=c.dialog.open(props('first-result'));
			const second=c.dialog.open(props('second-result'));
			assert(document.querySelector('#dialog').open && document.querySelectorAll('#dialog button').length===1,'second open waits');
			document.querySelector('#dialog button').click();
			assert(await first==='first-result','first promise resolves own result');
			await new Promise(resolve=>setTimeout(resolve,0));assert(document.querySelector('#dialog').open,'queued second opened after close');
			document.querySelector('#dialog button').click();assert(await second==='second-result','second promise resolves own result');
			const pending=c.dialog.open(props('pending-result'));document.querySelector('#dialog button').click();
			const replacement=c.dialog.open(props('replacement-result'));
			assert(await pending==='pending-result','pending close result');
			await new Promise(resolve=>setTimeout(resolve,0));assert(document.querySelector('#dialog').open,'replacement opened after pending close');
			document.querySelector('#dialog button').click();assert(await replacement==='replacement-result','replacement has own result');
		""")
		self.assertEqual([], _harness.errors())

	def test_mock_request_lists_match_json_values_and_fail_closed(self):
		_harness.open("#/overview", "shell/request-list").wait_ready()
		self.js("""
			const request={filter:{ids:['x','y'],meta:{a:1,b:2}},before:'second',extra:true};
			const selected=await window.pywebview.api.get_history(request);
			assert(selected.ok && selected.data.next_before==='matched-second' && !Object.hasOwn(selected,'when'),'nested-keyorder independent matching and stripped when');
			assert((await b.call('get_history',{before:'third'})).next_before==='matched-third','second conditional entry');
			assert((await b.call('get_history',{})).next_before==='default-first','final default');
			assert((await b.call('get_history',{before:'second',filter:{ids:['y','x'],meta:{a:1,b:2}}})).next_before==='default-first','array order differs');
			const mock=await import('./js/mock-bridge.js');
			const absent=mock.resolveEnvelope('get_history',[{when:{before:null},ok:true,data:{runs:[],next_before:null}}],{});
			assert(!absent.ok && absent.error.code==='internal' && absent.error.detail.includes('get_history'),'missing is not null; no-match names method');
			window.pywebview.api.get_history=async request=>mock.resolveEnvelope('get_history',[{when:{before:'known'},ok:true,data:{runs:[],next_before:null}}],request);
			try{await b.call('get_history',{before:'unknown'});assert(false,'unmatched must fail');}catch(error){assert(error instanceof b.BridgeError && error.code==='internal' && error.detail.includes('get_history'),'unmatched bridge internal');}
			const ordered=mock.resolveEnvelope('get_history',[{when:{before:'known'},ok:true,data:{runs:[],next_before:'first-match'}},{when:{before:'known'},ok:true,data:{runs:[],next_before:'later-match'}},{ok:true,data:{runs:[],next_before:null}}],{before:'known'});
			assert(ordered.data.next_before==='first-match','first matching list order');
		""")
		self.assertEqual([], _harness.errors())

	def test_mock_unknown_locale_omits_direction_and_pseudo_rtl_stays(self):
		for fixture, route in (("shell/overview", "#/overview"), ("shell/setup", "#/setup/1")):
			for language in ("zz", "x-rtl", "en"):
				with self.subTest(fixture=fixture, language=language):
					_harness.open(route, fixture, lang=language).wait_ready()
					self.js("""
						const requested=new URLSearchParams(window.location.search).get('lang');
						const answer=await window.pywebview.api.app_info({});
						assert(answer.ok && answer.data.lang===requested,'mock keeps requested language');
						const html=document.documentElement;
						if(requested==='zz') {
							assert(!Object.hasOwn(answer.data,'dir'),'unknown mock locale leaves direction unset');
							const index=await (await fetch('./i18n/index.json')).json();
							assert(!index.locales.some(locale=>locale.code==='zz'),'zz is not a registered catalog');
							const source=index.locales.find(locale=>locale.code===index.source);
							assert(index.source==='en' && html.lang==='en' && html.dir===source.dir && html.dir==='ltr','page falls back to source locale direction');
						} else if(requested==='x-rtl') {
							assert(answer.data.dir==='rtl' && html.lang==='x-rtl' && html.dir==='rtl','pseudo RTL remains explicit');
						} else {
							assert(answer.data.dir==='ltr' && html.lang==='en' && html.dir==='ltr','known English direction retained');
						}
					""")
					self.assertEqual([], _harness.errors())

	def test_mock_format_override_localizes_values_independently_of_language(self):
		cases = (("de", "de-DE", "de-DE"), ("ar", "ar-SA", "ar-SA"), ("en", "ar-SA", "ar-SA"),
			("ar", "de-DE", "de-DE"), ("de", "not_a_tag", "en-US"), ("de", "", "en-US"), ("de", None, "en-US"))
		for language, override, expected in cases:
			with self.subTest(language=language, format=override):
				url = urlsplit(_harness.url("#/overview", "shell/overview", lang=language))
				query = dict(parse_qsl(url.query))
				if override is not None:
					query["format"] = override
				self.assertEqual("127.0.0.1", url.hostname)
				_harness.driver.get(urlunsplit(url._replace(query=urlencode(query))))
				_harness.wait_ready()
				result = _harness.driver.execute_async_script("""
					const expected=arguments[0],requested=arguments[1],done=arguments[arguments.length-1];
					(async()=>{
						const i=await import('./js/i18n.js');
						const response=await window.pywebview.api.app_info({});
						const assert=(ok,message)=>{if(!ok)throw Error(message);};
						assert(response.ok && response.data.format_locale===expected,'validated format override or fixture fallback');
						assert(response.data.lang===requested && document.documentElement.lang===requested,'format never changes language');
						assert(document.documentElement.dir===(requested==='ar'?'rtl':'ltr'),'format never changes direction');
						const number=new Intl.NumberFormat(expected).format(12340);
						const date=new Intl.DateTimeFormat(expected,{dateStyle:'medium'}).format(new Date(2026,9,14));
						const time=new Intl.DateTimeFormat(expected,{timeStyle:'short'}).format(new Date(2000,0,1,11,45));
						assert(i.formatNumber(12340)===number && i.formatDate('2026-10-14')===date && i.formatTime('11:45')===time,'runtime uses region for number/date/time');
						assert(document.querySelector('.stat-value').textContent===number,'visible balance uses region');
						assert([...document.querySelectorAll('.points-chart tbody th')].some(cell=>cell.textContent===date),'rendered chart table uses regional date');
						const tick=new Intl.DateTimeFormat(expected,{month:'short',day:'numeric'}).format(new Date(2026,8,15));
						assert(document.querySelector('.chart-tick').textContent===tick,'visible chart ticks use region');
						if(expected==='de-DE') assert(number==='12.340' && date==='14.10.2026' && time==='11:45','German formats exercised');
						if(expected==='ar-SA') assert(/[\u0660-\u0669]/.test(number) && /[\u0660-\u0669]/.test(date) && /[\u0660-\u0669]/.test(time),'Arabic-Indic digits exercised');
						return {ok:true,lang:requested,format:expected,number,date,time,dir:document.documentElement.dir};
					})().then(done).catch(error=>done({ok:false,error:String(error),stack:error.stack}));
				""", expected, language)
				self.assertTrue(result["ok"], result)
				print("mock format proof=" + json.dumps(result), flush=True)
				self.assertEqual([], _harness.errors())

	def test_text_field_focus_caret_errors_and_password(self):
		_harness.open("#/overview", "shell/components").wait_ready()
		self.js("""
			let inputValue,commitValue,commits=0,resolveCommit;
			let field=c.textField.create({id:'focus-field',labelKey:'shell.demo.label',descriptionKey:'shell.demo.description',type:'text',value:'abcdef',maxLength:40,placeholderKey:'common.goal.target',autocomplete:'off',onInput:value=>{inputValue=value;},onCommit:value=>{commitValue=value;commits++;resolveCommit?.(value);}});
			document.querySelector('#view').append(field);
			const input=field.querySelector('input'); input.focus(); input.setSelectionRange(2,4,'backward');
			const before=field;
			field=c.textField.update(field,{error:{messageKey:'errors.settings.out_of_range',params:{min:100,max:1000000}}});
			assert(field===before && field.querySelector('input')===input && document.activeElement===input,'stable field/input/focus');
			assert(input.selectionStart===2 && input.selectionEnd===4 && input.selectionDirection==='backward','selection retained');
			assert(input.getAttribute('aria-invalid')==='true','invalid flag');
			assert(input.getAttribute('aria-describedby')==='focus-field-description focus-field-error','description and error wiring');
			assert(document.getElementById('focus-field-error').textContent===i.t('errors.settings.out_of_range',{min:100,max:1000000}),'translated error');
			input.value='edited'; input.dispatchEvent(new Event('input',{bubbles:true})); input.setSelectionRange(3,3);
			field=c.textField.update(field,{error:null}); assert(input.value==='edited' && input.selectionStart===3 && document.activeElement===input,'error update keeps user edit/focus');
			assert(input.getAttribute('aria-invalid')==='false' && input.getAttribute('aria-describedby')==='focus-field-description','error cleared');
			input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true})); assert(inputValue==='edited' && commitValue==='edited' && commits===1,'input and Enter callbacks');
			// Exercise the registered blur handler directly. Native window focus is
			// independent of document.activeElement in headless Edge.
			const blurValue=await new Promise(resolve=>{resolveCommit=resolve;input.dispatchEvent(new FocusEvent('blur'));});
			resolveCommit=null;
			assert(commits===2 && commitValue==='edited' && blurValue==='edited','blur callback acknowledged');
			const url=c.textField.create({id:'url-field',labelKey:'shell.demo.label',type:'url',value:'https://example.test'});document.querySelector('#view').append(url);url.querySelector('input').focus();c.textField.update(url,{error:{messageKey:'errors.settings.url_invalid'}});assert(document.activeElement===url.querySelector('input'),'URL update focus without invalid selection API');
			const secret='Synthetic-secret-123'; const password=c.textField.create({id:'password-field',labelKey:'shell.demo.label',type:'password',value:secret,autocomplete:'new-password'}); document.querySelector('#view').append(password);
			assert(password.querySelector('input').value===secret && password.querySelector('input').type==='password','password property');
			assert(!password.textContent.includes(secret) && !password.outerHTML.includes(secret) && !password.querySelector('input').hasAttribute('value'),'secret never text/HTML attribute');
		""")
		self.assertEqual([], _harness.errors())

	def test_canonical_theme_changed_topic(self):
		self.js("window.rf.receive([{topic:'theme.changed',data:{theme:'light'}}]);assert(document.documentElement.dataset.theme==='light' && s.get('appInfo').theme==='light','canonical theme.changed');")
		self.assertEqual([], _harness.errors())

	def test_canonical_navigate_topic(self):
		self.js("window.rf.receive([{topic:'navigate',data:{route:'#/history'}}]);await new Promise(resolve=>setTimeout(resolve,20));assert(window.location.hash==='#/history' && document.querySelector('#view').dataset.route==='#/history','canonical navigate');")
		self.assertEqual([], _harness.errors())

	def test_canonical_update_available_topic(self):
		self.js("const update={available:true,latest:'0.2.1',url:'https://github.com/User0332/rewards-farmer/releases/tag/v0.2.1'};window.rf.receive([{topic:'update.available',data:update}]);assert(s.get('update').latest==='0.2.1' && document.querySelector('#app-notices .banner'),'canonical update.available');")
		self.assertEqual([], _harness.errors())

	def test_goal_presets_custom_and_clear_use_set_goal(self):
		_harness.open("#/overview", "shell/components").wait_ready()
		self.js("""
			const calls=[];
			window.pywebview.api.set_goal=async request=>{calls.push(request); return {ok:true,data:{goal:request.target===null?null:{target:request.target,remaining:request.target,days:null,avg_per_day:null}}};};
			const host=document.querySelector('[data-component=goalCard]');
			host.querySelector('button').click();
			let form=host.querySelector('form'), select=form.querySelector('select');
			assert(JSON.stringify([...select.options].map(option=>option.value))===JSON.stringify(['5000','6500','10000','15000','25000','custom']),'exact presets');
			select.value='6500'; select.dispatchEvent(new Event('change',{bubbles:true}));
			form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})); await new Promise(resolve=>setTimeout(resolve,0));
			assert(calls[0].account_id==='jamie' && calls[0].target===6500,'preset set_goal');
			host.querySelector('button').click(); form=host.querySelector('form'); select=form.querySelector('select');
			select.value='custom'; select.dispatchEvent(new Event('change',{bubbles:true}));
			const number=form.querySelector('input'); number.value='12345'; number.dispatchEvent(new Event('input',{bubbles:true}));
			assert(number.min==='100' && number.max==='1000000','custom range');
			form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})); await new Promise(resolve=>setTimeout(resolve,0));
			assert(calls[1].target===12345,'custom set_goal');
			host.querySelector('button').click(); form=host.querySelector('form');
			[...form.querySelectorAll('button')].find(button=>button.textContent===i.t('common.goal.clear')).click(); await new Promise(resolve=>setTimeout(resolve,0));
			assert(calls[2].target===null && calls[2].account_id==='jamie','clear set_goal null');
			assert(host.textContent.includes(i.t('common.goal.none')),'no goal after clear');
		""")
		self.assertEqual([], _harness.errors())

	def test_live_log_duplicate_append_gap_overlap_and_queue(self):
		self.js("""
			const fixture=await (await fetch('./mock/shell/component-cases.json')).json();
			const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
			for(const sample of fixture.cases.joins) {
				const requests=[]; s.set('live',{run:{run_id:'join-test',active:true},log:sample.prior,last:null});
				window.pywebview.api.get_run_log=async request=>{requests.push(request); return {ok:true,data:sample.page};};
				window.rf.receive([{topic:'run.log',data:sample.push}]); await settle();
				assert(JSON.stringify(requests)===JSON.stringify(sample.requests),sample.name+' exact reads');
				assert(s.get('live').log.text===sample.expected.text && s.get('live').log.next_offset===sample.expected.next_offset,sample.name+' exact joined result');
			}
			let release; s.set('live',{run:{run_id:'join-test',active:true},log:{text:'ab',next_offset:2},last:null});
			const calls=[];
			window.pywebview.api.get_run_log=request=>{calls.push(request);return new Promise(resolve=>{release=()=>resolve({ok:true,data:{text:'cdef',next_offset:6,eof:true,size:6}});});};
			window.rf.receive([{topic:'run.log',data:{run_id:'join-test',offset:4,next_offset:6,text:'ef'}},{topic:'run.log',data:{run_id:'join-test',offset:6,next_offset:8,text:'gh'}}]);
			await settle(); release(); await settle(); await settle();
			assert(calls.length===1 && s.get('live').log.text==='abcdefgh' && s.get('live').log.next_offset===8,'serialized gap then contiguous');
			window.pywebview.api.get_run_log=()=>new Promise(resolve=>{release=()=>resolve({ok:true,data:{text:'ij',next_offset:10,eof:true,size:10}});});
			window.rf.receive([{topic:'run.log',data:{run_id:'join-test',offset:9,next_offset:10,text:'j'}}]); await settle();
			window.rf.receive([{topic:'run.started',data:{run_id:'new-run',active:true}}]); release(); await settle();
			assert(s.get('live').run.run_id==='new-run' && s.get('live').log.text==='' && s.get('live').log.next_offset===0,'old async read cannot attach to new run');
		""")
		self.assertEqual([], _harness.errors())

	def test_log_panel_paging_and_offset_join(self):
		self.js("""
			const fixture=await (await fetch('./mock/shell/component-cases.json')).json();
			const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
			const calls=[];
			window.pywebview.api.get_run_log=async request=>{calls.push(request); const page=fixture.cases.log_pages.find(page=>page.offset===request.offset); assert(page,'expected page offset'); return {ok:true,data:page.data};};
			const panel=c.logPanel.create({runId:'paging',open:true}); document.querySelector('#view').append(panel); await settle();
			assert(JSON.stringify(calls[0])===JSON.stringify({run_id:'paging',offset:0}),'initial offset zero');
			assert(panel.querySelector('pre').textContent==='first é\\n' && !panel.querySelector('.log-more').hidden,'first page visible/more');
			panel.querySelector('.log-more').click(); await settle();
			assert(calls[1].offset===9 && panel.querySelector('pre').textContent==='first é\\nsecond\\n' && !panel.querySelector('.log-more').hidden,'second page append');
			panel.querySelector('.log-more').click(); await settle();
			assert(calls[2].offset===16 && panel.querySelector('pre').textContent==='first é\\nsecond\\nthird\\n' && panel.querySelector('.log-more').hidden,'third page eof');
			c.dispose(panel);panel.remove();
			for(const sample of fixture.cases.joins) {
				const requests=[];
				window.pywebview.api.get_run_log=async request=>{requests.push(request); if(request.offset===0 && !Object.hasOwn(request,'max_bytes')) return {ok:true,data:{...sample.prior,eof:true,size:sample.prior.next_offset}}; return {ok:true,data:sample.page};};
				const part=c.logPanel.create({runId:'join-test',open:true}); document.querySelector('#view').append(part); await settle(); requests.length=0;
				window.rf.receive([{topic:'run.log',data:sample.push}]); await settle();
				assert(JSON.stringify(requests)===JSON.stringify(sample.requests),'panel '+sample.name+' exact reads');
				assert(part.querySelector('pre').textContent===sample.expected.text,'panel '+sample.name+' exact text');
				c.dispose(part);part.remove();
			}
			const requests=[]; let release;
			window.pywebview.api.get_run_log=request=>{requests.push(request);return new Promise(resolve=>{release=()=>resolve({ok:true,data:{text:'ab',next_offset:2,eof:true,size:2}});});};
			const part=c.logPanel.create({runId:'queued-panel',open:true}); document.querySelector('#view').append(part); await settle();
			window.rf.receive([{topic:'run.log',data:{run_id:'queued-panel',offset:0,next_offset:2,text:'ab'}},{topic:'run.log',data:{run_id:'queued-panel',offset:2,next_offset:4,text:'cd'}}]);
			release();await settle();await settle();assert(part.querySelector('pre').textContent==='abcd' && requests.length===1,'pushes during initial page deduped');
			c.dispose(part);part.remove();
			window.rf.receive([{topic:'run.log',data:{run_id:'queued-panel',offset:4,next_offset:6,text:'ef'}}]);await settle();assert(requests.length===1,'disposed panel no reads');
		""")
		self.assertEqual([], _harness.errors())

	def test_settings_changed_reloads_only_language_or_direction(self):
		_harness.open("#/overview", "shell/reload").wait_ready()
		self.js("""
			const running=s.get('appInfo'); let calls=0; let response={...running};
			window.__rfReloaded=0;
			window.pywebview.api.app_info=async()=>{calls++;return {ok:true,data:response};};
			const push=async()=>{window.rf.receive([{topic:'settings.changed',data:{}}]);await new Promise(resolve=>setTimeout(resolve,0));};
			await push();assert(calls===1 && window.__rfReloaded===0,'same language no reload');
			response={...running,theme:'light',reduced_motion:true};await push();assert(calls===2 && window.__rfReloaded===0,'other settings no reload');
			response={...running,lang:'de'};await push();assert(calls===3 && window.__rfReloaded===1,'language reload');
			response={...running,dir:'rtl'};await push();assert(calls===4 && window.__rfReloaded===2,'direction reload');
			assert(document.querySelector('#app').dataset.ready==='true' && document.querySelector('#view').dataset.route==='#/overview','stub did not navigate');
		""")
		self.assertEqual([], _harness.errors())

	def test_reduced_motion(self):
		_harness.open("#/overview", "shell/components", reduced_motion=True).wait_ready()
		self.assertEqual("none", _harness.driver.execute_script("return getComputedStyle(document.querySelector('.spinner-disc')).animationName"))
		self.assertEqual([], _harness.errors())

	def test_wave3_chart_labels_keep_aspect_nice_ticks_and_capture(self):
		_harness.driver.refresh()
		_harness.wait_ready()
		self.js("""
			const card=document.querySelector('.overview-chart');const chart=card.querySelector('.points-chart');
			assert(chart.querySelectorAll('.chart-svg text').length===0 && !chart.querySelector('.chart-labels').hasAttribute('viewBox'),'labels outside scaled SVG, native glyph units');
			assert(!chart.querySelector('[style]'),'no inline styles');
			assert(JSON.stringify([...chart.querySelectorAll('.chart-y')].map(node=>strip(node.textContent)))===JSON.stringify(['9,000','10,500','12,000']),'nice y ticks');
			const label=chart.querySelector('.chart-tick');const latest=chart.querySelector('.chart-latest');
			const box=label.getBoundingClientRect();window.__wideLabel={w:box.width,h:box.height,aspect:box.width/box.height,font:getComputedStyle(label).fontSize,card:card.getBoundingClientRect().width};
			assert(label.textContent==='Sep 15' && latest.textContent===i.formatNumber(12340),'month/day and latest value preserved');
		""")
		_harness.viewport(860, 600)
		self.js("""
			const label=document.querySelector('.overview-chart .chart-tick'),box=label.getBoundingClientRect(),first=window.__wideLabel;
			assert(first.card>document.querySelector('.overview-chart').getBoundingClientRect().width,'two card widths measured');
			assert(first.w>0 && first.h>0 && Math.abs(first.aspect-box.width/box.height)<0.01 && Math.abs(first.w-box.width)<0.1 && first.font===getComputedStyle(label).fontSize,'label rendered aspect constant at two card widths');
			const matrix=label.getScreenCTM();assert(Math.abs(matrix.a-matrix.d)<0.001,'uniform label transform');
		""")
		captures = []
		for width, height in ((1280, 800), (860, 600)):
			_harness.viewport(width, height)
			for theme in ("dark", "light"):
				_harness.open("#/overview", "shell/overview", theme).wait_ready()
				_harness.driver.refresh()
				_harness.wait_ready()
				self.js("""
					const card=document.querySelector('.overview-chart').getBoundingClientRect(),plot=document.querySelector('.chart-plot').getBoundingClientRect();
					for(const node of document.querySelectorAll('.chart-svg,.chart-labels')) { const rect=node.getBoundingClientRect(); assert(Math.abs(rect.height-plot.height)<1 && rect.bottom<=plot.bottom+1,'SVG viewport stays inside its actual plot'); }
					for(const node of document.querySelectorAll('.chart-tick,.chart-y')) { const rect=node.getBoundingClientRect(); assert(rect.top>=card.top && rect.bottom<=card.bottom+1,'actual labels stay inside chart card'); }
				""")
				_harness.driver.execute_script("document.querySelector('.overview-chart').scrollIntoView({block:'end'});document.activeElement.blur();")
				captures.append(_harness.screenshot(f"chart-{theme}-{width}x{height}"))
				self.assertEqual([], _harness.errors())
		print("wave3 chart captures=" + json.dumps(captures), flush=True)

	def test_wave3_large_button_avatar_and_weekday_hierarchy(self):
		for theme in ("dark", "light"):
			_harness.open("#/overview", "shell/components", theme).wait_ready()
			self.js("""
				const normal=document.querySelector('[data-component=button] .button:not(.button-large)');
				const large=document.querySelector('[data-component=button] .button-large');
				assert(getComputedStyle(normal).minBlockSize==='40px' && getComputedStyle(normal).fontSize==='14px','default button unchanged');
				assert(getComputedStyle(large).minBlockSize==='48px' && getComputedStyle(large).fontSize==='16px' && large.getBoundingClientRect().height>=48,'large button size and label');
				const picker=document.querySelector('.weekday[aria-pressed=true]'),primary=document.querySelector('[data-component=button] .button-primary');
				const selected=getComputedStyle(picker),root=getComputedStyle(document.documentElement),solid=getComputedStyle(primary);
				assert(selected.color===root.getPropertyValue('--c-text').trim() || selected.color===(document.documentElement.dataset.theme==='light'?'rgb(36, 36, 36)':'rgb(255, 255, 255)'),'weekday uses foreground text');
				assert(selected.backgroundColor!==solid.backgroundColor && selected.borderColor===solid.borderColor,'weekday tint with accent boundary, primary solid');
				const avatar=document.querySelector('.nav-account .avatar');
				assert(getComputedStyle(avatar).fontWeight==='600' && getComputedStyle(avatar).fontSize==='20px','avatar typography unchanged, normal text contrast tested separately');
			""")
			self.assertEqual([], _harness.errors())

	def test_wave3_mount_error_is_shown_once_and_background_errors_remain(self):
		for screen, route in (("overview", "#/overview"), ("accounts", "#/accounts"), ("setup", "#/setup/1")):
			_harness.open(route, screen + "/load-error").wait_ready()
			self.js("""
				assert(!document.querySelector('#toasts .toast'),'mount owns its error, no shell duplicate');
				const screen=document.querySelector('#view [data-screen]');
				assert(screen && (screen.querySelector('.empty-state') || screen.querySelector('.banner[role=alert]')),'screen error rendered');
			""")
			self.assertEqual([], _harness.errors())
		_harness.open("#/overview", "shell/overview").wait_ready()
		self.js("""
			window.pywebview.api.get_overview=async()=>({ok:false,error:{code:'internal',message_key:'errors.app.internal',params:{},detail:'Synthetic background error',fields:null}});
			window.rf.receive([{topic:'overview.changed',data:{}}]);await new Promise(resolve=>setTimeout(resolve,0));
			assert(document.querySelectorAll('#toasts .toast').length===1,'background error still announced once');
		""")
		self.assertEqual([], _harness.errors())

	def test_wave3_mock_save_keeps_secrets_out_of_later_reads(self):
		_harness.open("#/overview", "shell/redacted-settings").wait_ready()
		self.js("""
			const changes={'notify.policy':'errors','run.openrouter_key':'synthetic-private-key','notify.webhook_url':'https://example.test/synthetic-secret'};
			const saved=await b.call('save_settings',{changes});const next=await b.call('get_settings');
			for(const response of [saved,next]) {
				assert(!Object.hasOwn(response.values,'run.openrouter_key') && !Object.hasOwn(response.values,'notify.webhook_url'),'secret keys excluded');
				assert(!JSON.stringify(response).includes(changes['run.openrouter_key']) && !JSON.stringify(response).includes(changes['notify.webhook_url']),'secret values never echoed');
				assert(response.secrets_set['run.openrouter_key'] && response.secrets_set['notify.webhook_url'],'saved secret flags');
			}
			assert(next.values['notify.policy']==='errors','nonsecret state comes from saved response');
		""")
		self.assertEqual([], _harness.errors())

	def test_gallery_no_overflow(self):
		for width, height in ((1280, 800), (860, 600)):
			_harness.viewport(width, height)
			for lang in ("en", "x-long", "x-rtl"):
				for theme in ("dark", "light"):
					_harness.open("#/overview", "shell/components", theme, lang).wait_ready()
					result = _harness.driver.execute_script("const view=document.getElementById('view'); return {rootOverflow:document.documentElement.scrollWidth>innerWidth+1,viewOverflow:view.scrollWidth>view.clientWidth+1,offenders:[...document.querySelectorAll('[data-component]')].filter(node=>node.scrollWidth>node.clientWidth+1).map(node=>node.dataset.component)};")
					self.assertEqual({"rootOverflow":False,"viewOverflow":False,"offenders":[]}, result, (theme, lang, width))
					self.assertEqual([], _harness.errors())

	def test_z_route_matrix_and_confirmation(self):
		SCRATCH.mkdir(parents=True, exist_ok=True)
		rows = []
		failures = []
		for width, height in ((1280, 800), (860, 600)):
			_harness.viewport(width, height)
			for lang in ("en", "x-long", "x-rtl"):
				for theme in ("dark", "light"):
					for screen, route in ROUTES.items():
						_harness.open(route, "shell/overview" if screen == "stats" else "shell/" + screen, theme, lang).wait_ready()
						name = f"{screen}-{theme}-{lang}-{width}x{height}"
						result = _harness.driver.execute_script("""
							const view=document.getElementById('view'); const app=document.getElementById('app');
							return {width:innerWidth,height:innerHeight,rootOverflow:document.documentElement.scrollWidth>innerWidth+1,viewOverflow:view.scrollWidth>view.clientWidth+1,navCount:document.querySelectorAll('.nav-link').length,dir:document.documentElement.dir,theme:document.documentElement.dataset.theme,focus:document.activeElement.id,title:document.title,ready:app.dataset.ready};
						""")
						result.update(case=name, errors=_harness.errors())
						rows.append(result)
						if result["rootOverflow"] or result["viewOverflow"] or result["errors"] or result["focus"] != "view" or result["theme"] != theme or result["dir"] != ("rtl" if lang == "x-rtl" else "ltr"):
							failures.append(result)
					print(f"route checks {width}x{height} {lang} {theme}: 7", flush=True)
			for theme in ("dark", "light"):
				for lang in ("en", "x-long", "x-rtl"):
					_harness.open("#/overview", "shell/components", theme, lang).wait_ready()
					gallery = _harness.driver.execute_script("const view=document.getElementById('view'); return {rootOverflow:document.documentElement.scrollWidth>innerWidth+1,viewOverflow:view.scrollWidth>view.clientWidth+1};")
					if gallery["rootOverflow"] or gallery["viewOverflow"] or _harness.errors():
						failures.append({"gallery":f"{theme}-{lang}-{width}x{height}",**gallery})
		(SCRATCH / "revision-functional-results.json").write_text(json.dumps({"cases":rows,"signed_in":_harness.signed_in,"inprivate":_harness.inprivate,"failures":failures}, indent=2), encoding="utf-8")
		_harness.viewport(1280, 800)
		_harness.open("#/overview", "shell/overview").wait_ready()
		order = _harness.tab_order(1)
		self.assertEqual(["Run now"], order)
		print(f"Functional route matrix: {len(rows)} cases; failures={len(failures)}; no visual capture in components bundle", flush=True)
		self.assertEqual([], failures)

	def test_shared_gutter_keeps_real_screen_headings_aligned(self):
		rows = []
		routes = (("overview", "#/overview"), ("live", "#/live"), ("history", "#/history"), ("accounts", "#/accounts"), ("settings", "#/settings/schedule"))
		try:
			for width, height in ((1440, 640), (1440, 900), (1920, 1080), (1920, 2000)):
				_harness.viewport(width, height)
				for lang in ("en", "x-rtl"):
					# A non-shell fixture mounts the five real modules in one document.
					_harness.open("#/settings/schedule", "settings/manual", "dark", lang).wait_ready()
					case = []
					for screen, route in routes:
						with self.subTest(width=width, height=height, lang=lang, screen=screen):
							self.js("""
								const route=""" + json.dumps(route) + """, screen=""" + json.dumps(screen) + """;
								r.navigate(route);
								for(let n=0;n<200;n++){
									const view=document.getElementById('view'),root=view.querySelector('[data-screen="'+screen+'"]');
									if(view.dataset.route===route && view.getAttribute('aria-busy')==='false' && root?.querySelector('h1') && root.getAttribute('aria-busy')!=='true')break;
									if(n===199)throw Error('real screen did not settle: '+screen);
									await new Promise(resolve=>setTimeout(resolve,10));
								}
								await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
							""")
							geometry = _harness.driver.execute_script("""
								const view=document.getElementById('view'),heading=view.querySelector('h1'),box=heading.getBoundingClientRect(),root=document.documentElement,body=document.body;
								const table=view.querySelector('.points-chart .chart-table'),tableBox=table?.getBoundingClientRect(),parent=table?.offsetParent;
								const visibleBottom=view.querySelector('[data-screen]').getBoundingClientRect().bottom;
								const realContentRange=Math.max(0,Math.ceil(visibleBottom-view.getBoundingClientRect().bottom+Number.parseFloat(getComputedStyle(view).paddingBlockEnd)));
								return {leading:root.dir==='rtl'?box.right:box.left,left:box.left,right:box.right,dir:root.dir,viewClientWidth:view.clientWidth,viewOffsetWidth:view.offsetWidth,viewClientHeight:view.clientHeight,viewScrollHeight:view.scrollHeight,visibleBottom,realContentRange,overflow:view.scrollHeight>view.clientHeight+1,horizontalOverflow:view.scrollWidth>view.clientWidth+1,rootScrollWidth:root.scrollWidth,rootClientWidth:root.clientWidth,rootScrollHeight:root.scrollHeight,rootClientHeight:root.clientHeight,bodyWidth:body.getBoundingClientRect().width,gutter:getComputedStyle(view).scrollbarGutter,position:getComputedStyle(view).position,chartTable:table?{top:tableBox.top,bottom:tableBox.bottom,height:tableBox.height,offsetParent:parent?.id||parent?.tagName||null}:null};
							""")
							row = {"viewport":[width,height],"lang":lang,"screen":screen,**geometry}
							rows.append(row)
							case.append(row)
							print("Shared gutter geometry " + json.dumps(row, sort_keys=True), flush=True)
							if width == 1920 and height == 1080 and screen in ("overview", "settings"):
								_harness.driver.execute_script("document.getElementById('view').scrollTop=0")
								_harness.screenshot(f"shared-gutter-{screen}-dark-{lang}-{width}x{height}")
							self.assertEqual([], _harness.errors())
							self.assertFalse(geometry["horizontalOverflow"], row)
							self.assertLessEqual(geometry["rootScrollHeight"], geometry["rootClientHeight"] + 1, row)
							if geometry["chartTable"] is not None:
								self.assertEqual("view", geometry["chartTable"]["offsetParent"], row)
					if len(case) == len(routes):
						with self.subTest(width=width, height=height, lang=lang, invariant="five headings share leading edge"):
							self.assertLessEqual(max(row["leading"] for row in case) - min(row["leading"] for row in case), 1, case)
			self.assertTrue(any(row["overflow"] for row in rows), "The test must exercise a real overflowing page")
			self.assertTrue(any(row["viewport"]==[1440,640] and row["overflow"] and row["realContentRange"]>0 for row in rows), "Short viewport must overflow because visible content extends below the fold")
			self.assertTrue(any(not row["overflow"] for row in rows), "The test must exercise a page without overflow")
			for lang in ("en", "x-rtl"):
				self.assertTrue(any(row["viewport"]==[1920,2000] and row["lang"]==lang and not row["overflow"] for row in rows), (lang, "tall no-overflow case"))
		finally:
			print(f"Shared gutter measured screens={len(rows)}", flush=True)
			_harness.viewport(1280, 800)
