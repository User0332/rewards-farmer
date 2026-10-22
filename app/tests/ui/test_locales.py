import _bootstrap  # noqa: F401

import json
import os
import unittest
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ui.harness import UI_DIR, UiHarness


_enabled = os.environ.get("REWARDS_APP_UI_TESTS") == "1"
_harness = None
SIZES = ((1280, 800), (860, 600))
REGIONS = {
	"de": "de-DE", "es": "es-ES", "fr": "fr-FR", "it": "it-IT", "pt-BR": "pt-BR",
	"ja": "ja-JP", "zh-Hans": "zh-CN", "ar": "ar-SA", "x-long": "en-US", "x-rtl": "en-US",
}
# Each concrete route gets one text-heavy state, not another full fixture matrix.
# Disclosures reveal existing copy through their normal controls, never test CSS.
ROUTES = (
	("setup-1", "#/setup/1", "setup/driver-mismatch", "setup", ()),
	("setup-2", "#/setup/2", "setup/legacy-task", "setup", (".setup-days > summary",)),
	("setup-3", "#/setup/3", "setup/review", "setup", ()),
	("overview", "#/overview", "overview/multiple", "overview", ()),
	("stats", "#/stats", "stats/today-open", "stats", ()),
	("live", "#/live", "live/failed", "live", (".live-more-details > summary",)),
	("history", "#/history", "history/week", "history", (".history-day-page .history-check-group > summary",)),
	("history-detail", "#/history/synthetic-partial", "history/detail", "history", ("[data-run-id='synthetic-partial'] .history-technical > summary",)),
	("accounts", "#/accounts", "accounts/list", "accounts", (".accounts-entry .accounts-disclosure > summary", ".accounts-list > .accounts-disclosure > summary")),
	("settings", "#/settings", "settings/legacy", "settings", ()),
	("settings-schedule", "#/settings/schedule", "settings/legacy", "settings", ()),
	("settings-run", "#/settings/run", "settings/openrouter", "settings", ()),
	("settings-notifications", "#/settings/notifications", "settings/automatic", "settings", ()),
	("settings-app", "#/settings/app", "settings/automatic", "settings", ()),
	("settings-advanced", "#/settings/advanced", "settings/automatic", "settings", ()),
)


def locales():
	index = json.loads((UI_DIR / "i18n/index.json").read_text(encoding="utf-8"))
	return [(locale["code"], locale["dir"]) for locale in index["locales"] if locale["code"] != "en"] + [("x-long", "ltr"), ("x-rtl", "rtl")]


def setUpModule():
	global _harness
	if _enabled:
		_harness = UiHarness()
		try:
			_harness.start()
		except BaseException:
			_harness.stop()
			raise


def tearDownModule():
	if _harness is not None:
		_harness.stop()


class LocalePassSourceTests(unittest.TestCase):
	def test_fixture_manifest_and_dynamic_locale_inventory(self):
		self.assertEqual(15, len(ROUTES))
		self.assertEqual(len(ROUTES), len({row[0] for row in ROUTES}))
		self.assertEqual(len(ROUTES), len({row[1] for row in ROUTES}))
		self.assertEqual({"setup", "overview", "stats", "live", "history", "accounts", "settings"}, {row[3] for row in ROUTES})
		for _, _, fixture, _, _ in ROUTES:
			with self.subTest(fixture=fixture):
				self.assertIs(json.loads((UI_DIR / "mock" / (fixture + ".json")).read_text(encoding="utf-8"))["synthetic"], True)
		index = json.loads((UI_DIR / "i18n/index.json").read_text(encoding="utf-8"))
		self.assertEqual([(entry["code"], entry["dir"]) for entry in index["locales"] if entry["code"] != "en"], locales()[:-2])
		self.assertEqual([("x-long", "ltr"), ("x-rtl", "rtl")], locales()[-2:])
		# A new registered language must choose a measured region instead of inheriting en-US.
		self.assertEqual({locale for locale, _ in locales()}, set(REGIONS))
		self.assertTrue(all(REGIONS.values()))


@unittest.skipUnless(_enabled, "Set REWARDS_APP_UI_TESTS=1 through the guarded parent overlay for the sequential locale pass")
class LocalePassTests(unittest.TestCase):
	def test_registered_and_pseudo_locales_on_every_route(self):
		self._check_routes(ROUTES, locales())

	def test_f81_history_regional_values(self):
		self._check_routes(tuple(row for row in ROUTES if row[3] == "history"), [("de", "ltr"), ("ar", "rtl")])

	def _check_routes(self, routes, locale_inventory):
		from selenium.webdriver.support.ui import WebDriverWait
		measurements = []
		for lang, direction in locale_inventory:
			region = REGIONS[lang]
			for width, height in SIZES:
				_harness.viewport(width, height)
				for name, route, fixture, screen, disclosures in routes:
					with self.subTest(lang=lang, region=region, size=f"{width}x{height}", route=route, fixture=fixture):
						url = urlsplit(_harness.url(route, fixture, theme="dark", lang=lang, reduced_motion=True))
						query = urlencode([*parse_qsl(url.query), ("format", region)])
						_harness.driver.get(urlunsplit(url._replace(query=query)))
						_harness.wait_ready()
						WebDriverWait(_harness.driver, 10).until(lambda driver: driver.execute_script("return location.hash===arguments[0] && document.querySelector('[data-screen=\"'+arguments[1]+'\"]') && !document.querySelector('#view [aria-busy=true]')", route, screen))
						if name == "setup-1":
							WebDriverWait(_harness.driver, 5).until(lambda driver: driver.execute_script("return document.querySelector('[data-signin-state]')?.dataset.signinState==='failed'"))
						# F81's narrow list is the date rail, so enter its newest day through the real link.
						if name == "history" and width <= 1000:
							_harness.driver.find_element("css selector", ".history-date").click()
							WebDriverWait(_harness.driver, 10).until(lambda driver: driver.execute_script("return document.querySelector('.history-day-page')?.getBoundingClientRect().height>0 && !document.querySelector('#view [aria-busy=true]')"))
						for selector in disclosures:
							_harness.driver.execute_script("const summary=document.querySelector(arguments[0]);if(!summary)throw Error('Missing disclosure '+arguments[0]);if(!summary.parentElement.open)summary.click();", selector)
						if name == "history":
							WebDriverWait(_harness.driver, 10).until(lambda driver: driver.execute_script("const group=document.querySelector('.history-check-group');return group?.open && group.querySelectorAll('.history-run-block').length===Number(group.dataset.checkCount)"))
						result = _harness.driver.execute_async_script(r"""
							const [lang,dir,screen,name,region,width,height,done]=arguments;
							(async()=>{
								const i=await import('./js/i18n.js');
								const store=await import('./js/store.js');
								await document.fonts.ready;
								await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
								const view=document.getElementById('view'),html=document.documentElement;
								const problems=[];
								const selector=node=>{
									if(node.id)return '#'+node.id;
									const parts=[];
									while(node && node!==document.body){
										if(node.id){parts.unshift('#'+node.id);break;}
										parts.unshift(node.tagName.toLowerCase()+':nth-child('+([...node.parentElement.children].indexOf(node)+1)+')');node=node.parentElement;
									}
									return parts.join(' > ')||'body';
								};
								const text=node=>(node?.textContent||'').replace(/[\u2068\u2069]/g,'').replace(/\s+/g,' ').trim();
								const check=(ok,label,node=html,overflow=0,kind='layout')=>{if(!ok)problems.push({message:label,selector:selector(node),text:text(node),overflow_px:Math.round(overflow*100)/100,kind});};
								check(html.lang===lang && html.dir===dir,'html locale '+html.lang+'/'+html.dir);
								check(html.dataset.theme==='dark','dark theme');
								check(innerWidth===width && innerHeight===height,'viewport');
								check(store.get('appInfo')?.format_locale===region,'store format_locale '+store.get('appInfo')?.format_locale,html,0,'format');
								const formatted=[];
								const number=value=>new Intl.NumberFormat(region).format(value);
								const date=value=>new Intl.DateTimeFormat(region,{dateStyle:'medium'}).format(new Date(value));
								const time=value=>new Intl.DateTimeFormat(region,{timeStyle:'short'}).format(new Date(value));
								const strip=value=>value.replace(/[\u2068\u2069]/g,'');
								const formattedValue=(css,expected,contains=false)=>{
									const node=document.querySelector(css),actual=node?.textContent||'';
									formatted.push({selector:css,expected,actual,comparison:contains?'contains':'equals'});
									check(Boolean(node)&&(contains?strip(actual).includes(strip(expected)):actual.trim()===expected),'rendered regional value expected '+expected,node||view,0,'format');
								};
								if(screen==='setup'){
									for(let step=1;step<=3;step++)formattedValue('.setup-steps li:nth-child('+step+') .setup-step-number',number(step));
									check(!document.querySelector('.setup-progress'),'redundant step sentence omitted');
								}else if(screen==='overview'){
									const overview=store.get('overview'),selected=document.querySelector('#overview-account')?.value;
									const account=overview.accounts.find(item=>item.account_id===selected)||overview.accounts[0];
									formattedValue('.overview-balance .stat-value',number(account.state.balance));
									const timestamp=new Date(overview.generated_at),now=new Date();
									formattedValue('.overview-screen .screen-header p',timestamp.toDateString()===now.toDateString()?i.t('overview.date_today'):date(timestamp));
									formattedValue('.overview-next-link',time(overview.schedule.next_at),true);
								}else if(screen==='live'){
									const run=store.get('live').run;
									formattedValue('.live-screen .screen-header > div > p',date(run.started_at)+' '+time(run.started_at));
									if(run.points_earned==null)check(!document.querySelector('.live-summary .stat-value'),'unknown run points omitted');
									else formattedValue('.live-summary .stat-value',number(run.points_earned));
								}else if(screen==='history'){
									const fixture=(await (await fetch('./mock/'+new URLSearchParams(location.search).get('mock')+'.json')).json());
									const answer=fixture.methods.get_history,listed=(Array.isArray(answer)?answer.find(item=>!item.when):answer)?.data.runs||[];
									const linked=name==='history-detail'?fixture.methods.get_run.data:null;
									const day=value=>{const stamp=new Date(value);return[stamp.getFullYear(),String(stamp.getMonth()+1).padStart(2,'0'),String(stamp.getDate()).padStart(2,'0')].join('-')};
									const sorted=[...listed].sort((a,b)=>new Date(b.started_at)-new Date(a.started_at));
									const selected=linked?day(linked.started_at):day((sorted.find(run=>day(run.started_at)===day(new Date()))||sorted[0]).started_at);
									const records=sorted.filter(run=>day(run.started_at)===selected);
									if(linked&&!records.some(run=>run.run_id===linked.run_id))records.push(linked);
									check(document.querySelector('.history-day-page')?.dataset.date===selected,'History selected fixture day '+selected,view,0,'format');
									check(records.length>0&&document.querySelectorAll('.history-day-page .history-run-block').length===records.length,'History renders every selected-day record',view,0,'format');
									formattedValue('.history-day-page .history-day-title',date(records[0].started_at));
									for(const run of records){
										const css='.history-day-page .history-run-block[data-run-id="'+CSS.escape(run.run_id)+'"] > .history-run-summary > time';
										formattedValue(css,time(run.started_at));
										check(document.querySelector(css)?.dateTime===run.started_at,'History run timestamp identity '+run.run_id,document.querySelector(css)||view,0,'format');
									}
								}else if(screen==='accounts'){
									const account=store.get('accounts')[0];
									formattedValue('.account-row > .tabular',number(account.balance),true);
								}else if(name==='settings'||name==='settings-schedule'){
									const schedule=store.get('settings').schedule;
									const {scheduleTime}=await import('./js/schedule-time.js');
									formattedValue('.settings-schedule-status',scheduleTime(schedule,store.get('overview')?.generated_at||new Date().toISOString(),i,region));
								}else{
									// These panels deliberately contain no regional date, clock or count.
									// Their controls and translated text still pass the route/overflow checks.
									check(!document.querySelector('.account-count'),'removed duplicate footer count stays absent');
									check(i.formatNumber(1234567)===number(1234567),'regional formatter binding on non-numeric panel',view,0,'format');
								}
								for(const [label,node]of [['page',html],['body',document.body],['view',view]])check(node.scrollWidth<=node.clientWidth+1,label+' horizontal overflow',node,node.scrollWidth-node.clientWidth,'overflow');
								const cards=[...view.querySelectorAll('.card')];
								for(const [index,card]of cards.entries()){
									const box=card.getBoundingClientRect(),bounds=view.getBoundingClientRect();
									check(card.scrollWidth<=card.clientWidth+1,'card '+index+' content overflow',card,card.scrollWidth-card.clientWidth,'overflow');
									check(box.left>=bounds.left-1 && box.right<=bounds.right+1,'card '+index+' outside view',card,Math.max(0,bounds.left-box.left,box.right-bounds.right),'layout');
									if(card.scrollWidth>card.clientWidth+1){
										for(const child of card.querySelectorAll('*')){
											if(!child.checkVisibility({checkVisibilityCSS:true})||!child.clientWidth)continue;
											const childBox=child.getBoundingClientRect(),overflow=Math.max(child.scrollWidth-child.clientWidth,box.left-childBox.left,childBox.right-box.right);
											if(overflow>1 && text(child))problems.push({message:'overflowing card descendant',selector:selector(child),text:text(child),overflow_px:Math.round(overflow*100)/100,kind:'overflow'});
										}
									}
								}
								const namespaces=(await (await fetch('./i18n/index.json')).json()).namespaces;
								const rawKey=new RegExp('^(?:'+namespaces.join('|')+')\\.[a-z0-9_]+(?:\\.[a-z0-9_]+)*$');
								const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
								let node;
								while(node=walker.nextNode()){
									const parent=node.parentElement;
									if(!parent || parent.closest('script,style,option,.sr-only,[hidden]') || !parent.checkVisibility({checkVisibilityCSS:true,checkOpacity:true}))continue;
									const range=document.createRange();range.selectNodeContents(node);
									if(![...range.getClientRects()].some(box=>box.width>0&&box.height>0))continue;
									const value=node.textContent.replace(/[\u2068\u2069]/g,'').trim();
									check(!rawKey.test(value),'visible raw message key '+value,parent,0,'raw-key');
								}
								if(screen==='setup'){
									const risk=view.querySelector('.risk-notice');
									check(risk?.textContent===i.t('common.risk_notice'),'setup risk notice copy',risk||view);
									if(risk){risk.scrollIntoView({block:'center'});const box=risk.getBoundingClientRect();check(risk.checkVisibility({checkVisibilityCSS:true}) && box.bottom>0 && box.top<innerHeight,'setup risk notice visible',risk);}
								}
								view.scrollTop=0;
								return {problems,cards:cards.length,format_locale:store.get('appInfo')?.format_locale,formatted_values:formatted};
							})().then(done).catch(error=>done({problems:[String(error)],stack:error.stack}));
						""", lang, direction, screen, name, region, width, height)
						capture = _harness.screenshot(f"{name}-{lang}-{width}x{height}.png")
						measurement = {"locale": lang, "region": region, "route": route, "fixture": fixture, "size": f"{width}x{height}", "capture": str(capture), **result, "console_errors": _harness.errors()}
						measurements.append(measurement)
						# Keep each observed case even if the following assertion fails.
						(Path(capture).parent / "locale-measurements.json").write_text(json.dumps(measurements, ensure_ascii=False, indent=2), encoding="utf-8")
						self.assertTrue(Path(capture).is_file(), capture)
						self.assertEqual([], result["problems"], result)
						self.assertEqual([], _harness.errors())
		for locale, _ in locale_inventory:
			cases = [case for case in measurements if case["locale"] == locale]
			failed = [case for case in cases if case["problems"] or case["console_errors"]]
			print(f"Locale {locale}: {len(cases)} cases, {len(failed)} failed cases, {sum(len(case['problems']) for case in cases)} findings", flush=True)
		print(f"Sequential locale pass: {len(locale_inventory)} locales, {len(routes)} routes, {len(SIZES)} sizes, {len(measurements)} cases and captures", flush=True)
