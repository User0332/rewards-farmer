import _bootstrap  # noqa: F401

import json
import os
import unittest
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ui.harness import UI_DIR, UiHarness, _Handler


_harness = None
_enabled = os.environ.get("REWARDS_APP_UI_TESTS") == "1"
_original_get = None
FIXTURES = sorted(path.stem for path in (UI_DIR / "mock/overview").glob("*.json"))
TASKS = "daily_set explore_on_bing visual_search misc_cards quests searches bonus".split()


def setUpModule():
	global _harness, _original_get
	if _enabled:
		mutation = os.environ.get("RF_F78_MUTATION")
		if mutation:
			from urllib.parse import urlsplit
			css_mutation = mutation == "f80-columns"
			changes = {
				"bing_app": ('const names = { bing_search:', 'const names = { bing_app: "overview.streaks.bing_search", bing_search:'),
				"stale": ('if (today && known(item.today_done)', 'if (known(item.today_done)'),
				"unknown_stamps": ('if (totalKnown && collectedKnown && bonus.stamps_total > 0)', 'if (totalKnown && bonus.stamps_total > 0)'),
				"f80-stale-row": ('const streaks = todayKnown ? streaksRegion(overview.streaks) : null;', 'if (!account.state_is_today) root.append(c.taskList.create({ tasks: account.tasks_today })); const streaks = todayKnown ? streaksRegion(overview.streaks) : null;'),
				"f80-columns": ('@media (min-width: 1000.01px)', '@media (min-width: 100000px)'),
			}
			if mutation not in changes:
				raise ValueError("Unknown F78 test mutation")
			resource = "css/screens/overview.css" if css_mutation else "js/screens/overview.js"
			source = (UI_DIR / resource).read_text(encoding="utf-8")
			old, new = changes[mutation]
			if source.count(old) != 1:
				raise AssertionError("F78 mutation must hit one real renderer branch")
			payload = source.replace(old, new).encode("utf-8")
			_original_get = _Handler.do_GET
			def mutated_get(handler):
				if urlsplit(handler.path).path == "/" + resource:
					handler.send_response(200)
					handler.send_header("Content-Type", "text/css" if css_mutation else "text/javascript")
					handler.send_header("Content-Length", str(len(payload)))
					handler.end_headers()
					handler.wfile.write(payload)
				else:
					_original_get(handler)
			_Handler.do_GET = mutated_get
		_harness = UiHarness()
		try:
			_harness.start().wait_ready()
			# Freeze only this test browser before each page, never the production code.
			# The literal ready snapshot stays reproducible after the calendar advances.
			ready = json.loads((UI_DIR / "mock/overview/ready.json").read_text(encoding="utf-8"))
			stamp = ready["methods"]["get_overview"]["data"]["generated_at"]
			_harness.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """
				{
					const RealDate=window.Date,fixed=RealDate.parse(""" + json.dumps(stamp) + """);
					class TestDate extends RealDate{constructor(...args){super(...(args.length?args:[fixed]));}static now(){return fixed;}}
					window.Date=TestDate;
				}
			"""})
		except BaseException:
			_harness.stop()
			raise


def tearDownModule():
	if _harness is not None:
		_harness.stop()
	if _original_get is not None:
		_Handler.do_GET = _original_get


class OverviewSourceTests(unittest.TestCase):
	def test_synthetic_fixtures_and_namespaces(self):
		self.assertGreaterEqual(len(FIXTURES), 9)
		defaults = json.loads((UI_DIR / "mock/shell/defaults.json").read_text(encoding="utf-8"))
		for fixture in FIXTURES:
			with self.subTest(fixture=fixture):
				data = json.loads((UI_DIR / "mock/overview" / (fixture + ".json")).read_text(encoding="utf-8"))
				self.assertIs(data["synthetic"], True)
				self.assertTrue(data["note"])
				response = data["methods"].get("get_overview", defaults["methods"]["get_overview"])
				if response["ok"]:
					for account in response["data"]["accounts"]:
						self.assertEqual(TASKS, [row["task"] for row in account["tasks_today"]])
						self.assertEqual(30, len(account["chart"]))
						for row in account["tasks_today"]:
							self.assertIn(row["status"], ("ok", "skip", "fail", "off", "none"))
							self.assertIn(row["still_open"], (True, False, None))
		catalog = json.loads((UI_DIR / "i18n/en/overview.json").read_text(encoding="utf-8"))
		self.assertEqual("overview", catalog["namespace"])
		for key, entry in catalog["messages"].items():
			self.assertTrue(entry["note"].strip(), key)
			self.assertNotRegex(json.dumps(entry, ensure_ascii=False), "[\u2013\u2014]")
		code = (UI_DIR / "js/screens/overview.js").read_text(encoding="utf-8")
		self.assertNotIn("innerHTML", code)
		self.assertNotIn(".style", code)
		self.assertNotRegex(code, r"\b(?:setInterval|pywebview)\b")
		css = (UI_DIR / "css/screens/overview.css").read_text(encoding="utf-8")
		self.assertNotRegex(css, r"(?:margin|padding)-(?:left|right)|(?<![\w-])(?:left|right)\s*:")


@unittest.skipUnless(_enabled, "Set REWARDS_APP_UI_TESTS=1 for guarded mock UI acceptance")
class OverviewTests(unittest.TestCase):
	def setUp(self):
		_harness.viewport(1280, 800)

	def js(self, body):
		result = _harness.driver.execute_async_script("""
			const done=arguments[arguments.length-1];
			(async()=>{
				const assert=(ok,label)=>{if(!ok)throw Error(label);};
				const strip=value=>value.replace(/[\u2068\u2069]/g,'');
				const s=await import('./js/store.js');
				const i=await import('./js/i18n.js');
				const r=await import('./js/router.js');
		""" + body + """
				return {ok:true};
			})().then(done).catch(error=>done({ok:false,error:String(error),stack:error.stack}));
		""")
		self.assertTrue(result["ok"], result)

	def assert_keyboard(self):
		expected = _harness.driver.execute_script("""
			return [...document.querySelectorAll('#view button,#view a[href],#view input,#view select,#view summary,#toasts button')]
				.filter(node=>!node.disabled&&node.tabIndex>=0&&node.getClientRects().length)
				.map((node,index)=>{if(!node.id)node.id='overview-tab-'+index;return node.id;});
		""")
		order = _harness.tab_order(len(expected) + 12)
		self.assertTrue(set(expected).issubset(order), {"expected": expected, "order": order})

	def test_f78_lane_probe(self):
		location = os.environ.get("RF_F78_SCRATCH")
		if not location:
			return
		import importlib.util
		import tempfile
		root = Path(location).resolve()
		self.assertTrue(root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
		spec = importlib.util.spec_from_file_location("f78_lane_probe", root / "probe.py")
		probe = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(probe)
		probe.run(_harness, root)

	def test_f78_streaks_known_or_not_shown(self):
		_harness.open("#/overview", "overview/ready").wait_ready()
		self.js("""
			const base=s.get('overview'),streaks=structuredClone(base.streaks),card=()=>document.querySelector('#overview-streaks');
			const show=value=>s.set('overview',{...base,streaks:value});
			assert(card()&&document.querySelectorAll('#overview-streaks').length===1,'one Streaks card');
			assert(!card().querySelector('[data-streak=bing_app]'),'F78 Bing app stays hidden');
			assert([...card().querySelectorAll('[data-streak]')].map(n=>n.dataset.streak).join(',')==='bing_search,daily_set,visual_search','only the three requested streaks');
			assert(strip(card().querySelector('.overview-bonus-points').textContent)===strip(i.t('common.points.total',{count:1000})),'regional bonus points');
			assert(!card().querySelector('.overview-stamp-grid,.overview-stamp'),'unknown collected count has no grid');
			assert(card().querySelector('.overview-stamps-label').textContent===i.t('overview.streaks.stamps_total',{count:12}),'only the stated stamp total');
			const known=structuredClone(streaks);known.big_bonus.stamps_collected=8;show(known);
			assert(card().querySelectorAll('.overview-stamp.is-collected').length===8,'eight known stamps');
			assert(card().querySelector('.overview-stamps-label').textContent===i.t('overview.streaks.stamps_progress',{collected:i.formatNumber(8),total:i.formatNumber(12)}),'known of total');
			for(const field of ['points','stamps_total','stamps_collected']){
				const sample=structuredClone(known);sample.big_bonus[field]=null;show(sample);
				if(field==='points')assert(!card().querySelector('.overview-bonus-points'),'null bonus points omitted');
				if(field==='stamps_total')assert(!card().querySelector('.overview-stamp-grid')&&card().querySelector('.overview-stamps-label').textContent===i.t('overview.streaks.stamps_collected',{count:8}),'null total no grid or inferred length');
				if(field==='stamps_collected')assert(!card().querySelector('.overview-stamp-grid,.overview-stamp'),'null collected no stamp grid');
			}
			const noBonus=structuredClone(streaks);noBonus.big_bonus={points:null,stamps_total:null,stamps_collected:null};show(noBonus);
			assert(!card().querySelector('.overview-streak-bonus'),'all-null bonus block omitted');
			const zero=structuredClone(streaks);zero.big_bonus={points:0,stamps_total:0,stamps_collected:0};show(zero);
			assert(card().querySelector('.overview-bonus-points')&&!card().querySelector('.overview-stamp'),'known zero not treated as unknown');
			for(const field of ['day','length','today_done','today_total','next_points']){
				const sample=structuredClone(streaks);sample.items.find(n=>n.id==='bing_search')[field]=null;show(sample);
				const row=card().querySelector('[data-streak=bing_search]');
				if(field==='day')assert(!row,'no day drops item');
				else if(field==='length')assert(!row.querySelector('.overview-streak-track')&&row.querySelector('.overview-streak-day').textContent===i.t('overview.streaks.day',{day:i.formatNumber(6)}),'unknown cycle shows only day');
				else if(field.startsWith('today_'))assert(!row.querySelector('.overview-streak-today'),'partial today pair omitted');
				else assert(!row.querySelector('.overview-streak-next'),'unknown next reward omitted');
			}
			const zeroDay=structuredClone(streaks);zeroDay.items.find(n=>n.id==='bing_search').day=0;show(zeroDay);
			assert(card().querySelector('[data-streak=bing_search]')&&!card().querySelector('[data-streak=bing_search] .is-complete'),'day zero is known');
			show({...streaks,items:[]});assert(!card().querySelector('.overview-streak-list'),'empty items no empty list');
			show(null);assert(!card(),'null streaks hides whole card');
			show(streaks);
		""")
		_harness.open("#/overview", "overview/multiple").wait_ready()
		self.js("assert(document.querySelectorAll('.overview-stamp.is-collected').length===8,'known-count mock branch');")
		_harness.open("#/overview", "overview/first-run").wait_ready()
		self.js("assert(!document.querySelector('#overview-streaks'),'null mock branch');")
		self.assertEqual([], _harness.errors())

	def test_f78_streaks_local_day_and_accessibility(self):
		_harness.open("#/overview", "overview/stale").wait_ready()
		self.js("assert(!document.querySelector('.overview-streak-today'),'F78 yesterday capture never says Today');")
		_harness.open("#/overview", "overview/ready").wait_ready()
		self.js("""
			const base=s.get('overview'),original=structuredClone(base.streaks);
			const show=streaks=>s.set('overview',{...base,streaks});
			const now=new Date(),midnight=new Date(now.getFullYear(),now.getMonth(),now.getDate(),0,1),yesterday=new Date(now.getFullYear(),now.getMonth(),now.getDate()-1,23,59);
			show({...original,captured_at:midnight.toISOString()});assert(document.querySelectorAll('.overview-streak-today').length===3,'same local day counts');
			show({...original,captured_at:yesterday.toISOString()});assert(!document.querySelector('.overview-streak-today'),'F78 local calendar boundary not duration');
			assert(document.querySelector('.overview-streak-captured time').getAttribute('datetime')===yesterday.toISOString(),'machine capture time');
			assert(document.querySelector('.overview-streak-captured').textContent===i.t('overview.streaks.as_of',{date:i.formatDate(yesterday),time:i.formatTime(yesterday)}),'older capture shows date and time');
			show(original);
			const card=document.querySelector('#overview-streaks'),title=document.getElementById(card.getAttribute('aria-labelledby'));
			assert(title?.tagName==='H2'&&title.textContent===i.t('overview.streaks.title'),'named Streaks region');
			assert(!card.querySelector('button,a,input,select,[tabindex]'),'read-only information adds no keyboard traps');
			assert(card.querySelectorAll('.overview-streak-captured').length===1,'one quiet capture line');
			for(const row of card.querySelectorAll('[data-streak]')){
				const item=original.items.find(item=>item.id===row.dataset.streak),track=row.querySelector('.overview-streak-track');
				assert(document.getElementById(row.getAttribute('aria-labelledby'))?.textContent.trim(),'named streak row');
				assert(track.getAttribute('role')==='img'&&track.getAttribute('aria-label')===i.t('overview.streaks.day_of',{day:i.formatNumber(item.day),length:i.formatNumber(item.length)}),'accessible Day N of M');
				assert(track.children.length===item.length&&track.querySelectorAll('.is-complete').length===item.day,'short track has exact completed steps');
			}
			assert(!card.querySelector('.overview-stamp-grid'),'unknown stamp count has no image');
			const known=structuredClone(original);known.big_bonus.stamps_collected=8;show(known);
			const stamps=document.querySelector('#overview-streaks .overview-stamp-grid');assert(stamps.getAttribute('role')==='img'&&stamps.getAttribute('aria-label')===i.t('overview.streaks.stamps_progress',{collected:i.formatNumber(8),total:i.formatNumber(12)}),'known stamp grid accessible count');
			show(original);
			const oldText=()=>{const copy=document.querySelector('.overview-screen').cloneNode(true);copy.querySelector('#overview-streaks')?.remove();return copy.textContent;};
			const before=oldText();show(null);assert(oldText()===before,'existing Overview content unaffected by card presence');show(original);
		""")
		self.assert_keyboard()
		self.assertEqual([], _harness.errors())

	def test_f78_s1_stamp_grid_requires_known_collected_count(self):
		_harness.open("#/overview", "overview/ready").wait_ready()
		self.js("""
			const base=s.get('overview'),original=structuredClone(base.streaks);
			const show=collected=>{const streaks=structuredClone(original);streaks.big_bonus.stamps_collected=collected;s.set('overview',{...base,streaks});};
			const block=()=>document.querySelector('.overview-streak-bonus');
			const rect=()=>{const r=block().getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
			show(null);
			assert(!document.querySelector('#overview-streaks .overview-stamp-grid,#overview-streaks .overview-stamp'),'S1 unknown collected has no grid or stamps');
			assert(block().children.length===1&&block().firstElementChild.classList.contains('overview-stamps-copy'),'S1 nothing replaces unknown grid');
			assert(block().querySelector('.overview-bonus-points').textContent===i.t('common.points.total',{count:1000}),'S1 known bonus points remain');
			assert(block().querySelector('.overview-stamps-label').textContent===i.t('overview.streaks.stamps_total',{count:12}),'S1 stated total remains');
			const unknownRect=rect();
			for(const collected of [0,8,12]){
				show(collected);
				const grid=block().querySelector('.overview-stamp-grid');
				assert(grid&&grid.children.length===12,'S1 known count draws twelve stamps');
				assert(grid.querySelectorAll('.is-collected').length===collected,'S1 exact known filled count');
				assert(grid.getAttribute('aria-label')===i.t('overview.streaks.stamps_progress',{collected:i.formatNumber(collected),total:i.formatNumber(12)}),'S1 known grid accessible count');
				const knownRect=rect();for(const key of Object.keys(unknownRect))assert(Math.abs(unknownRect[key]-knownRect[key])<0.5,'S1 steady bonus block '+key);
			}
			show(null);
			assert(!block().querySelector('.overview-stamp-grid'),'S1 known to unknown removes grid');
		""")
		self.assertEqual([], _harness.errors())

	def assert_f80_known_only(self):
		self.js("""
			const overview=s.get('overview'),account=overview.accounts[0],root=document.querySelector('.overview-screen');
			const visibleTexts=[...root.querySelectorAll('*')].filter(node=>node.getClientRects().length&&!node.closest('.sr-only,[hidden]')&&getComputedStyle(node).visibility!=='hidden'&&getComputedStyle(node).display!=='none')
				.flatMap(node=>[...node.childNodes].filter(child=>child.nodeType===Node.TEXT_NODE&&child.textContent.trim()).map(child=>strip(child.textContent.trim())));
			assert(!visibleTexts.includes(strip(i.t('common.value.unknown'))),'F80 no visible Not available');
			assert(!visibleTexts.includes(strip(i.t('common.task_status.none'))),'F80 no visible Not checked yet');
			assert(!root.querySelector('[data-region=tasks],[data-region=earned],#overview-streaks'),'F80 no unknown-day task earned or streak card');
			const known=Number.isFinite(account.state?.balance),balance=root.querySelector('.overview-balance');
			assert(!!balance===known,'F80 balance frame only when known');
			if(known){
				assert(strip(balance.querySelector('.stat-value').textContent)===strip(i.formatNumber(account.state.balance)),'F80 known balance retained');
				const time=balance.querySelector('.overview-balance-captured time');
				assert(time?.getAttribute('datetime')===account.state.at,'F80 exact balance reading timestamp');
				assert(time.textContent===i.t('overview.streaks.as_of',{date:i.formatDate(account.state.at),time:i.formatTime(account.state.at)}),'F80 local date and time from balance reading');
				assert(balance.querySelectorAll('.overview-balance-captured').length===1,'F80 one quiet dated line');
			}
			const goal=root.querySelector('.overview-goal');assert(goal,'F80 goal retained');
			if(account.goal){assert(goal.textContent.includes(i.t('common.goal.remaining',{count:account.goal.remaining})),'F80 remaining points retained');assert(goal.textContent.includes(account.goal.days===null?i.t('common.goal.estimate_unknown'):i.t('common.goal.days',{count:account.goal.days})),'F80 goal estimate unchanged');}
			else assert(goal.textContent.includes(i.t('common.goal.none')),'F80 existing goal line retained');
			const {scheduleTime}=await import('./js/screens/overview.js');
			assert(root.querySelector('.overview-next-link')?.textContent===scheduleTime(overview.schedule,overview.generated_at,i,s.get('appInfo')?.format_locale),'F80 next check remains known schedule');
			assert(root.querySelector('#overview-run-now')?.textContent.includes(i.t('overview.run_now')),'F80 Run now retained');
			assert(root.querySelectorAll('.points-chart tbody tr').length===30&&root.querySelector('[data-region=chart]'),'F80 accessible30-day curve retained');
			const code=account.state?.unreadable,notice=root.querySelector('.overview-notice');
			if(['page_blank','signed_out','terms_of_use'].includes(code)){
				assert(notice?.textContent.includes(i.t('errors.state.'+code,{account:account.label})),'F80 factual unreadable message retained');
				assert(notice.querySelector('button')?.textContent===i.t('overview.accounts'),'F80 factual message account action retained');
			}else assert(!notice,'F80 no stale-warning banner');
			if(['signed_out','terms_of_use'].includes(code))assert(root.querySelector('#overview-run-now').disabled&&root.querySelector('.overview-run-reason a[href="#/accounts"]'),'F80 blocked run has existing recovery action');
			const spread=root.querySelector('.overview-spread'),summary=spread.querySelector('[data-region=summary]');
			assert(spread.children.length===1&&summary,'F80 no empty task frame');
			assert(Math.abs(spread.getBoundingClientRect().width-summary.getBoundingClientRect().width)<1,'F80 summary closes empty column');
			const children=[...summary.querySelector('.card-body').children].filter(node=>!(innerWidth>1000&&known)||!node.classList.contains('overview-balance'));
			for(let index=1;index<children.length;index++)assert(children[index].getBoundingClientRect().top-children[index-1].getBoundingClientRect().bottom<=17,'F80 no empty vertical card space');
			assert(document.querySelector('#view').scrollWidth<=document.querySelector('#view').clientWidth+1&&document.documentElement.scrollWidth<=innerWidth+1,'F80 no horizontal overflow');
		""")

	def test_f80_states_show_only_known_values(self):
		for fixture in ('stale','blank','signed-out','terms','first-run'):
			with self.subTest(fixture=fixture):
				_harness.open('#/overview','overview/'+fixture).wait_ready()
				self.assert_f80_known_only()
				self.assert_keyboard()
				self.assertEqual([],_harness.errors())
		_harness.open('#/overview','overview/stale').wait_ready()
		self.js("""
			const original=s.get('overview'),account=original.accounts[0];
			s.set('overview',{...original,accounts:[{...account,state:{...account.state,balance:0}}]});
			assert(document.querySelector('.stat-value').textContent===i.formatNumber(0),'F80 known zero balance remains');
			s.set('overview',{...original,accounts:[{...account,state_is_today:true,state:{...account.state,earned_today:null}}]});
			assert(!document.querySelector('[data-region=tasks],[data-region=earned],#overview-streaks'),'F80 current-date null figures still omitted');
			s.set('overview',{...original,accounts:[{...account,state_is_today:true,state:{...account.state,earned_today:0}}]});
			assert(document.querySelector('[data-region=tasks]')&&document.querySelector('[data-region=earned]'),'F80 zero today is a known reading');
		""")
		self.assertEqual([],_harness.errors())

	def test_f80_r2_known_balance_columns(self):
		cases=[(1131,643,theme,lang) for theme,lang in (('dark','en'),('light','en'),('dark','de'),('dark','ar'))]
		cases += [(width,height,'dark','en') for width,height in ((1001,643),(1280,800),(1536,1024),(1920,1080))]
		for width,height,theme,lang in cases:
			with self.subTest(width=width,height=height,theme=theme,lang=lang):
				_harness.viewport(width,height)
				_harness.open('#/overview','overview/ready',theme,lang,True).wait_ready()
				ready=_harness.driver.execute_script("const r=document.querySelector('#overview-run-now').getBoundingClientRect();return {width:r.width,height:r.height}")
				ready_width=ready['width']
				_harness.open('#/overview','overview/stale',theme,lang,True).wait_ready()
				self.assert_f80_known_only()
				for reason in (False,True):
					self.js("""
						const base=s.get('overview');s.set('overview',{...base,accounts:[{...base.accounts[0],paused:"""+json.dumps(reason)+"""}]});
						const rect=selector=>document.querySelector(selector).getBoundingClientRect();
						const balance=rect('.overview-balance'),schedule=rect('[data-region=schedule]'),run=rect('#overview-run-now'),goal=rect('[data-region=goal]'),body=rect('.overview-summary>.card-body');
						assert(Math.abs(run.width-"""+json.dumps(ready_width)+""")<=2,'R2 Run now matches ready width '+run.width+' expected '+"""+json.dumps(ready_width)+""");
						assert(Math.abs(run.height-"""+json.dumps(ready['height'])+""")<1,'R2 Run now keeps ready height');
						assert(Math.abs(balance.top-schedule.top)<1,'R2 balance and schedule share first row');
						assert(Math.abs(run.width-schedule.width)<1&&run.top>=schedule.bottom,'R2 action below same-width schedule');
						assert(Math.abs(goal.width-body.width)<1&&goal.top>=Math.max(balance.bottom,run.bottom),'R2 goal spans below both columns');
						assert(document.documentElement.dir==='rtl'?balance.left>schedule.left:balance.left<schedule.left,'R2 logical columns mirror');
						const reason=document.querySelector('.overview-run-reason');assert(!!reason==="""+json.dumps(reason)+"""&&document.querySelector('#overview-run-now').disabled==="""+json.dumps(reason)+""",'R2 paused branch renders reason and disables action');if(reason){const box=reason.getBoundingClientRect();assert(Math.abs(box.width-run.width)<1&&box.top>=run.bottom&&goal.top>=box.bottom,'R2 reason stays below action');}
					""")
				stale_width=_harness.driver.execute_script("return document.querySelector('#overview-run-now').getBoundingClientRect().width")
				print(f'R2 Run now {width}x{height} {theme}/{lang}: ready={ready_width:.3f}px stale={stale_width:.3f}px height={ready["height"]:.3f}px',flush=True)
				self.assertEqual([],_harness.errors())
		for fixture,width,height in (('stale',860,600),('blank',1131,643),('first-run',1131,643)):
			with self.subTest(fixture=fixture,width=width):
				_harness.viewport(width,height);_harness.open('#/overview','overview/'+fixture).wait_ready()
				self.js("""
					const body=document.querySelector('.overview-summary>.card-body'),run=document.querySelector('#overview-run-now').getBoundingClientRect();
					assert(Math.abs(run.width-body.getBoundingClientRect().width)<1,'R2 unchanged single-column action width');
					const balance=document.querySelector('.overview-balance'),schedule=document.querySelector('[data-region=schedule]');if(balance)assert(schedule.getBoundingClientRect().top>=balance.getBoundingClientRect().bottom,'R2 narrow keeps stacked balance');
				""")
				self.assertEqual([],_harness.errors())

	def test_f80_locales_themes_and_sizes(self):
		cases=0
		locales={'en':'en-US','de':'de-DE','es':'es-ES','fr':'fr-FR','it':'it-IT','pt-BR':'pt-BR','ja':'ja-JP','zh-Hans':'zh-CN','ar':'ar-SA','x-long':'en-US','x-rtl':'en-US'}
		for width,height in ((1280,800),(860,600),(1131,643),(1536,1024)):
			_harness.viewport(width,height)
			for theme in ('dark','light'):
				for lang,region in locales.items():
					for fixture in ('stale','blank','signed-out','terms','first-run'):
						with self.subTest(width=width,height=height,theme=theme,lang=lang,fixture=fixture):
							parts=urlsplit(_harness.url('#/overview','overview/'+fixture,theme,lang,True))
							query=dict(parse_qsl(parts.query));query['format']=region
							_harness.driver.get(urlunsplit(parts._replace(query=urlencode(query))))
							_harness.wait_ready()
							self.js("assert(s.get('appInfo').format_locale==="+json.dumps(region)+",'F80 exact requested formatting locale');")
							self.assert_f80_known_only()
							self.assertEqual('rtl' if lang in ('ar','x-rtl') else 'ltr',_harness.driver.execute_script('return document.documentElement.dir'))
							self.assertEqual([],_harness.errors())
							cases+=1
		print(f'F80 known-only matrix: {cases} cases,11locales4sizes2themes5states',flush=True)

	def test_a_fixture_matrix(self):
		cases = 0
		for width, height in ((1280, 800), (860, 600)):
			_harness.viewport(width, height)
			for theme in ("dark", "light"):
				for lang in ("en", "x-long", "x-rtl"):
					for fixture in FIXTURES:
						with self.subTest(width=width, theme=theme, lang=lang, fixture=fixture):
							_harness.open("#/overview", "overview/" + fixture, theme, lang).wait_ready()
							self.js("""
								assert(document.title===i.t('overview.title'),'router title F21');
								assert(document.querySelector('.overview-screen'),'real overview module');
								assert(!document.querySelector('#view [style]'),'CSP no inline styles');
								const view=document.getElementById('view');
								assert(view.scrollWidth<=view.clientWidth+1&&document.documentElement.scrollWidth<=innerWidth+1,'horizontal overflow');
								const overview=s.get('overview');
								if(overview?.accounts.length){const account=overview.accounts[0],today=!!(account.state_is_today&&account.state&&!['signed_out','terms_of_use','page_blank'].includes(account.state.unreadable)&&(account.state.unreadable||Number.isFinite(account.state.earned_today)));assert(document.querySelectorAll('.task-row').length===(today?7:0),'only known today has task rows');assert(document.querySelectorAll('.points-chart tbody tr').length===30,'all chart days accessible');}
								assert(document.documentElement.dir===(new URLSearchParams(location.search).get('lang')==='x-rtl'?'rtl':'ltr'),'direction');
							""")
							if width == 1280:
								shot = _harness.screenshot(f"overview-{fixture}-{theme}-{lang}")
								self.assertTrue(Path(shot).is_file())
							self.assert_keyboard()
							if fixture == "ready":
								self.js("""
									[...document.querySelectorAll('.overview-goal button')].find(node=>node.textContent===i.t('common.goal.edit')).click();
									const picker=document.querySelector('.overview-goal select');picker.value='custom';picker.dispatchEvent(new Event('change'));
									const view=document.querySelector('#view');assert(view.scrollWidth<=view.clientWidth+1,'custom editor wraps');
								""")
								self.assert_keyboard()
							self.assertEqual([], _harness.errors())
							cases += 1
		print(f"Overview fixture matrix: {cases} cases, {len(FIXTURES) * 6} screenshots", flush=True)

	def test_b_measurements_task_states_and_comp_regions(self):
		_harness.open("#/overview", "overview/ready").wait_ready()
		self.js("""
			assert(document.querySelector('.overview-summary .stat-value').textContent==='12,340','measured balance');
			assert(strip(document.querySelector('[data-region=open] strong').textContent)==='2 cards and 15 search points','comp measured open work');
			assert(document.querySelector('[data-task=daily_set]').dataset.status==='ok','done marker');
			assert(document.querySelector('[data-task=visual_search]').textContent.includes(i.t('errors.task.not_available')),'skip reason');
			assert(document.querySelector('[data-task=searches]').dataset.status==='open','open marker');
			assert(strip(document.querySelector('[data-task=searches] .task-state').textContent)==='45 of 60','search count');
			assert(document.querySelector('[data-region=schedule]').textContent.includes(i.t('overview.next_check')),'check not promised run');
			assert(strip(document.querySelector('.overview-next-link').textContent).startsWith('Today, around '),'concise today check');
			assert(!document.querySelector('.overview-account-label')&&!document.querySelector('#overview-account'),'no duplicate single account label');
			const runButton=document.querySelector('#overview-run-now');assert(runButton.classList.contains('button-large')&&runButton.getBoundingClientRect().height>=48&&getComputedStyle(runButton).fontSize==='16px','large primary action F48');
			assert(getComputedStyle(document.querySelector('.open-work p')).fontSize==='14px'&&getComputedStyle(document.querySelector('.task-reason')).fontSize==='14px','readable plan labels');
			const firstRow=document.querySelector('.task-row').getBoundingClientRect();assert(firstRow.height>=40&&firstRow.height<=42,'compact task rows');
			const unit=document.querySelector('.balance-unit').getBoundingClientRect(),balance=document.querySelector('.overview-summary .stat-value').getBoundingClientRect();assert(unit.top-balance.bottom>=0&&unit.top-balance.bottom<=4,'unit belongs to number');
			const tasks=document.querySelector('[data-region=tasks]').getBoundingClientRect(), summary=document.querySelector('[data-region=summary]').getBoundingClientRect(), chart=document.querySelector('[data-region=chart]').getBoundingClientRect();
			assert(tasks.width>summary.width&&tasks.x<summary.x,'approved unequal columns');
			assert(chart.y>=Math.max(tasks.bottom,summary.bottom)&&chart.width>tasks.width+summary.width,'chart follows full spread');
			assert(!document.querySelector('#app-bar'),'F25 no app bar');
			assert(!document.querySelector('[data-region=goal]').textContent.includes('€'),'no comp price claim');
		""")
		_harness.open("#/overview", "overview/stale").wait_ready()
		self.assert_f80_known_only()
		for fixture, key in (("signed-out", "errors.state.signed_out"), ("terms", "errors.state.terms_of_use"), ("blank", "errors.state.page_blank"), ("sections", "errors.state.sections")):
			_harness.open("#/overview", "overview/" + fixture).wait_ready()
			self.js("""
				const account=s.get('overview').accounts[0], code=account.state.unreadable;
				const params=code==='searches,cards'?{sections:i.formatList(['searches','cards'].map(name=>i.t('common.section.'+name)))}:{account:account.label};
				assert(document.querySelector('.overview-notice').textContent.includes(i.t('""" + key + """',params)),'plain state error');
				if(code==='searches,cards')assert(document.querySelector('[data-region=open] strong').textContent===i.t('common.value.unknown'),'sections state stays as it is');
				else assert(!document.querySelector('[data-region=open]'),'unreadable whole page has no unknown open row');
				if(['signed_out','terms_of_use'].includes(code)){assert(document.querySelector('#overview-run-now').disabled,'sign-in blocks run');const reason=document.querySelector('.overview-run-reason');assert(reason.querySelector('a[href="#/accounts"]')?.textContent===i.t('overview.accounts'),'nearby recovery link');let starts=0;window.pywebview.api.start_run=async()=>{starts++;return {ok:true,data:{started:true}};};document.querySelector('#overview-run-now').click();assert(starts===0,'disabled action sends nothing');}
				if(account.state.balance===null)assert(!document.querySelector('.overview-balance,.balance-unit'),'unknown balance is absent');
				if(code==='page_blank')assert(!document.querySelector('.task-state'),'blank page has no unknown task rows');
			""")
		_harness.open("#/overview", "overview/first-run").wait_ready()
		self.js("""
			assert(!document.querySelector('.overview-tasks,.overview-balance,.balance-unit'),'first-run unknown cards are absent');
			assert(document.querySelector('.overview-next-link').textContent===i.t('overview.schedule.manual_next'),'manual next action');
			assert([...document.querySelector('[data-region=schedule]').querySelectorAll('p,a')].filter(node=>node.textContent===i.t('overview.schedule.manual')).length===1,'manual mode once');
			assert(document.querySelectorAll('.chart-line').length===0,'no fabricated history');
			assert(!document.querySelector('[data-region=schedule]').textContent.includes(i.t('overview.schedule.independent')),'manual no unattended promise');
		""")
		self.assertEqual([], _harness.errors())

	def test_e_schedule_dates_and_warning_links(self):
		_harness.open("#/overview", "overview/ready").wait_ready()
		self.js("""
			const {scheduleTime}=await import('./js/screens/overview.js');
			const base={mode:'automatic',next_is_check:true,next_at:'2026-10-14T15:00:00'},now='2026-10-14T09:00:00';
			assert(strip(scheduleTime(base,now,i,'en-US'))==='Today, around '+i.formatTime(base.next_at),'today key');
			for(const [days,suffix]of [[0,'today'],[1,'tomorrow']])for(const check of [true,false]){
				const next_at='2026-10-'+String(14+days)+'T15:00:00',schedule={...base,next_at,next_is_check:check};
				const key='overview.'+(check?'check_around_':'run_at_')+suffix;
				assert(scheduleTime(schedule,now,i,'en-US')===i.t(key,{time:i.formatTime(next_at)}),'calendar key '+key);
			}
			const later=scheduleTime({...base,next_at:'2026-11-20T15:00:00'},now,i,'en-US');assert(strip(later).includes('Nov 20')&&!later.includes('2026'),'same-year date no year');
			assert(scheduleTime({...base,next_at:'2027-01-01T15:00:00'},now,i,'en-US').includes('2027'),'different year present');
			assert(strip(scheduleTime({...base,next_at:'2026-11-01T08:00:00'},'2026-10-31T20:00:00',i,'en-US')).startsWith('Tomorrow'),'local-day rollover independent of DST duration');
			assert(scheduleTime({...base,mode:'manual'},now,i,'en-US')===i.t('overview.schedule.manual_next'),'manual ignores timestamp');
			const overview=s.get('overview'),original=overview.schedule;
			const variants=[['missing',{task_exists:false}],['out_of_sync',{in_sync:false}],['error',{error_code:'synthetic'}],['legacy',{legacy_task:{name:'synthetic',time:'09:45',command:'synthetic'}}]];
			for(const [name,delta]of variants){
				s.set('overview',{...overview,schedule:{...original,...delta}});
				const warning=document.querySelector('.overview-schedule-warning');assert(warning.tagName==='A'&&warning.getAttribute('href')==='#/settings/schedule','actionable schedule '+name);assert(warning.textContent===i.t('overview.schedule.'+name),'F49 '+name);assert(getComputedStyle(warning).fontSize==='14px','warning body size');
			}
		""")
		self.assertEqual([], _harness.errors())

	def test_f_ready_capture_sizes_and_axis_visibility(self):
		for width, height in ((1280, 800), (1536, 1024)):
			_harness.viewport(width, height)
			for theme in ("dark", "light"):
				with self.subTest(width=width, theme=theme):
					_harness.open("#/overview", "overview/first-run", theme).wait_ready()
					_harness.open("#/overview", "overview/ready", theme).wait_ready()
					self.js("""
						const view=document.querySelector('#view');view.scrollTop=0;
						const chartNode=document.querySelector('[data-region=chart]');chartNode.scrollIntoView({block:'center'});
						await new Promise(resolve=>requestAnimationFrame(resolve));
						const chart=chartNode.getBoundingClientRect();
						assert(chart.bottom<=innerHeight&&chart.top>0,'whole chart fits after scroll '+JSON.stringify({width:innerWidth,height:innerHeight,top:chart.top,bottom:chart.bottom,scrollTop:view.scrollTop}));
						const ticks=[...document.querySelectorAll('.chart-tick')];assert(ticks.length===3,'date axis has three ticks');for(const tick of ticks){const box=tick.getBoundingClientRect();assert(box.bottom<=chart.bottom&&box.bottom<=innerHeight&&box.top>=chart.top,'date tick actually inside card and window '+JSON.stringify({width:innerWidth,top:box.top,bottom:box.bottom,card:chart.bottom}));}
					""")
					# F53 makes committed1b23d16, not the comp, the visual reference.
					# Keep its16px group gutter and32px Run-to-goal separation.
					# The grey surface replaces the divider's24px top padding with12px,
					# so earned-to-nextlabel is now28px minimum(16outside+12inside).
					# Its bottom padding is measured separately from the16px button gap.
					# The balance number and unit alone keep their0to4px tight gap.
					gaps = _harness.driver.execute_script("""
						const box=selector=>document.querySelector(selector).getBoundingClientRect();
						const balance=box('.overview-balance'),number=box('.overview-balance .stat-value'),unit=box('.overview-balance .balance-unit');
						const earned=box('[data-region=earned]'),schedule=box('[data-region=schedule]'),nextLabel=box('[data-region=schedule] > .subtle');
						const run=box('#overview-run-now'),goal=box('[data-region=goal] .goal-card');
						return {number_unit:unit.top-number.bottom,balance_earned:earned.top-balance.bottom,earned_next_label:nextLabel.top-earned.bottom,schedule_run:run.top-schedule.bottom,run_goal:goal.top-run.bottom};
					""")
					self.assertGreaterEqual(gaps["number_unit"], 0, gaps)
					self.assertLessEqual(gaps["number_unit"], 4, gaps)
					for group, minimum in (("balance_earned", 16), ("earned_next_label", 28), ("schedule_run", 16), ("run_goal", 32)):
						self.assertGreaterEqual(gaps[group], minimum, {"group": group, "minimum": minimum, "measured": gaps})
					print(f"summary gaps {width}x{height} {theme}: {json.dumps(gaps, sort_keys=True)}", flush=True)
					self.assertTrue(Path(_harness.screenshot(f"overview-ready-{theme}-en-{width}x{height}")).is_file())
					self.assertEqual([], _harness.errors())

	def test_g_today_subtitle_and_earlier_snapshot(self):
		_harness.open("#/overview", "overview/ready").wait_ready()
		self.js("""
			const {snapshotDate}=await import('./js/screens/overview.js');
			const today=new Date();assert(document.querySelector('.screen-header > div > p').textContent===i.t('overview.date_today'),'ready snapshot is Today');
			assert(i.t('overview.date_today')==='Today','source copy');
			assert(snapshotDate(new Date(today.getFullYear(),today.getMonth(),today.getDate(),0,1).toISOString(),i,today)===i.t('overview.date_today'),'same local day at different hour');
			const yesterday=new Date(today.getFullYear(),today.getMonth(),today.getDate()-1,23,59);
			assert(snapshotDate(yesterday.toISOString(),i,today)===i.formatDate(yesterday.toISOString()),'previous local day date');
			const monthEnd=new Date(2026,9,31,23,59),nextMonth=new Date(2026,10,1,0,1);
			assert(snapshotDate(monthEnd.toISOString(),i,nextMonth)===i.formatDate(monthEnd.toISOString()),'month boundary uses calendar day');
		""")
		_harness.open("#/overview", "overview/stale").wait_ready()
		self.js("""
			const data=s.get('overview');assert(document.querySelector('.screen-header > div > p').textContent===i.formatDate(data.generated_at),'earlier snapshot shows regional date');
			assert(document.querySelector('.screen-header > div > p').textContent!==i.t('overview.date_today'),'not Today for earlier fixture');
		""")
		self.assertTrue(Path(_harness.screenshot("overview-earlier-snapshot-dark-en")).is_file())
		self.assertEqual([], _harness.errors())

	def test_h_schedule_surface_and_all_text_contrast(self):
		for theme in ("dark", "light"):
			_harness.open("#/overview", "overview/ready", theme).wait_ready()
			result = _harness.driver.execute_async_script("""
				const done=arguments[arguments.length-1];
				(async()=>{
					const s=await import('./js/store.js'),base=s.get('overview'),original=base.schedule;
					const pixel=value=>{const canvas=document.createElement('canvas');canvas.width=canvas.height=1;const context=canvas.getContext('2d');context.clearRect(0,0,1,1);context.fillStyle=value;context.fillRect(0,0,1,1);return [...context.getImageData(0,0,1,1).data];};
					const luminance=rgba=>{const channels=rgba.slice(0,3).map(channel=>{const n=channel/255;return n<=0.04045?n/12.92:((n+0.055)/1.055)**2.4;});return channels[0]*0.2126+channels[1]*0.7152+channels[2]*0.0722;};
					const reports=[];
					for(const [name,delta]of [['healthy',{}],['missing',{task_exists:false}],['out_of_sync',{in_sync:false}],['error',{error_code:'synthetic'}],['legacy',{legacy_task:{name:'synthetic',time:'09:45',command:'synthetic'}}]]){
						s.set('overview',{...base,schedule:{...original,...delta}});
						const region=document.querySelector('.overview-schedule'),style=getComputedStyle(region),cardStyle=getComputedStyle(region.closest('.card'));
						const surface=pixel(style.backgroundColor),card=pixel(cardStyle.backgroundColor);
						const texts=[...region.querySelectorAll('*')].filter(node=>[...node.childNodes].some(child=>child.nodeType===Node.TEXT_NODE&&child.textContent.trim()));
						const measured=texts.map(node=>{const color=pixel(getComputedStyle(node).color),fg=luminance(color),bg=luminance(surface);return {text:node.textContent,color,ratio:(Math.max(fg,bg)+0.05)/(Math.min(fg,bg)+0.05)};});
						const rect=region.getBoundingClientRect(),padding=[style.paddingBlockStart,style.paddingInlineEnd,style.paddingBlockEnd,style.paddingInlineStart].map(parseFloat);
						const textInsets=texts.map(node=>{const box=node.getBoundingClientRect();return [box.left-rect.left,rect.right-box.right,box.top-rect.top,rect.bottom-box.bottom];});
						reports.push({name,surface,card,texts:measured,lowest:Math.min(...measured.map(item=>item.ratio)),padding,textInsets,radius:style.borderRadius,cardRadius:cardStyle.borderRadius,border:style.borderBlockStartWidth});
					}
					return {ok:true,reports};
				})().then(done).catch(error=>done({ok:false,error:String(error)}));
			""")
			self.assertTrue(result["ok"], result)
			for case in result["reports"]:
				with self.subTest(theme=theme, state=case["name"]):
					self.assertNotEqual(case["surface"], case["card"], case)
					self.assertEqual(255, case["surface"][3], case)
					self.assertEqual(case["radius"], case["cardRadius"], case)
					self.assertEqual("0px", case["border"], case)
					self.assertTrue(all(value >= 12 for value in case["padding"]), case)
					self.assertTrue(all(value >= 11.9 for insets in case["textInsets"] for value in insets), case)
					self.assertGreaterEqual(len(case["texts"]), 4, case)
					for text in case["texts"]:
						self.assertEqual(255, text["color"][3], text)
						self.assertGreaterEqual(text["ratio"], 4.5, {"theme": theme, "state": case["name"], **text})
			print(f"schedule surface {theme}: {json.dumps(result['reports'], sort_keys=True)}", flush=True)
			self.assertEqual([], _harness.errors())

	def test_c_account_chart_goal_and_actions(self):
		_harness.open("#/overview", "overview/multiple").wait_ready()
		self.js("""
			assert(document.querySelector('[data-task=daily_set]').dataset.status==='open','ok still open not checked');
			assert(document.querySelector('[data-task=misc_cards]').dataset.status==='fail','failure not hidden by count');
			assert(document.querySelectorAll('.chart-line').length===2&&document.querySelectorAll('.chart-point').length>=2,'missing days split line and isolated latest point');
			const select=document.querySelector('#overview-account'); select.value='riley'; select.dispatchEvent(new Event('change'));
			assert(document.querySelector('.stat-value').textContent==='2,340','account balance changes');
			assert(document.querySelector('#overview-run-now').disabled,'active or paused run disabled');
			assert(document.querySelector('[data-region=open] strong').textContent===i.t('overview.open.none'),'measured zero is nothing open');
			const radios=[...document.querySelectorAll('#overview-chart-mode [role=radio]')];radios[0].focus();radios[0].dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
			assert(document.querySelector('.points-chart').dataset.mode==='earned','chart choice uses shared arrows');
			const chart=document.querySelector('.points-chart');
			assert(!chart.querySelector('.chart-line,.chart-area'),'daily totals use bars, never an interpolated line');
			const series=s.get('overview').accounts.find(account=>account.account_id==='riley').chart;
			assert(chart.querySelectorAll('.chart-day-bar').length===series.filter(day=>day.earned!==null).length,'only known days have bars');
			assert(chart.querySelector('.chart-day-bar.is-latest') && chart.querySelector('.chart-crosshair'),'latest earned bar and exploration');
		""")
		_harness.open("#/overview", "overview/ready").wait_ready()
		self.js("""
			const calls=[];
			window.pywebview.api.set_goal=async request=>{calls.push(request);return {ok:true,data:{goal:request.target===null?null:{target:request.target,remaining:1000,avg_per_day:null,days:null}}};};
			const firstEdit=[...document.querySelectorAll('.overview-goal button')].find(node=>node.textContent===i.t('common.goal.edit'));firstEdit.click();
			const clear=[...document.querySelectorAll('.overview-goal button')].find(node=>node.textContent===i.t('common.goal.clear'));assert(clear,'shared clear');clear.click();await new Promise(resolve=>setTimeout(resolve,0));
			assert(calls[0].account_id==='jamie'&&calls[0].target===null,'clear exact request');assert(s.get('overview').accounts[0].goal===null,'clear readback');
			const set=[...document.querySelectorAll('.overview-goal button')].find(node=>node.textContent===i.t('common.goal.set'));set.click();
			const form=document.querySelector('.overview-goal form');assert(form,'shared editor');
			const presets=form.querySelector('select');assert(presets,'generic presets');presets.value='6500';presets.dispatchEvent(new Event('change'));
			form.dispatchEvent(new Event('submit',{cancelable:true}));await new Promise(resolve=>setTimeout(resolve,0));
			assert(calls.at(-1).target===6500,'preset request');assert(s.get('overview').accounts[0].goal.target===6500,'saved goal');
			const edit=[...document.querySelectorAll('.overview-goal button')].find(node=>node.textContent===i.t('common.goal.edit'));edit.click();
			const editForm=document.querySelector('.overview-goal form'),customChoice=editForm.querySelector('select');customChoice.value='custom';customChoice.dispatchEvent(new Event('change'));const custom=editForm.querySelector('input[type=number]');assert(custom,'custom goal input');custom.value='17500';custom.dispatchEvent(new Event('input'));editForm.dispatchEvent(new Event('submit',{cancelable:true}));await new Promise(resolve=>setTimeout(resolve,0));
			assert(calls.at(-1).target===17500,'custom exact request');
			window.pywebview.api.set_goal=async()=>({ok:false,error:{code:'accounts.unknown',message_key:'errors.app.accounts_unknown',params:{},detail:null,fields:null}});
			[...document.querySelectorAll('.overview-goal button')].find(node=>node.textContent===i.t('common.goal.edit')).click();document.querySelector('.overview-goal form').dispatchEvent(new Event('submit',{cancelable:true}));await new Promise(resolve=>setTimeout(resolve,0));
			assert(s.get('overview').accounts[0].goal.target===17500,'rejected goal leaves saved data');assert(document.querySelector('#toasts').textContent.includes(i.t('errors.app.accounts_unknown')),'goal bridge error');
			window.pywebview.api.start_run=async request=>{assert(request.account_ids[0]==='jamie'&&Object.keys(request).length===1,'selected run account exact6.3');return {ok:false,error:{code:'run.busy',message_key:'errors.app.run_busy',params:{},detail:null,fields:null}};};
			document.querySelector('#overview-run-now').click();await new Promise(resolve=>setTimeout(resolve,0));
			assert(document.querySelector('#toasts').textContent.includes(i.t('errors.app.run_busy')),'bridge error catalog');assert(!document.querySelector('#overview-run-now').disabled,'retry after rejected run');
			window.pywebview.api.start_run=async()=>({ok:true,data:{started:true}});document.querySelector('#overview-run-now').click();await new Promise(resolve=>setTimeout(resolve,60));assert(location.hash==='#/live','successful start navigates');
		""")
		self.assertEqual([], _harness.errors())

	def test_chart_event_completion_tooltip_and_table_are_preserved(self):
		_harness.open("#/overview", "overview/ready").wait_ready()
		self.js("""
			const chart=document.querySelector('.points-chart');await chart.chartAnimationDone;
			for(const path of chart.querySelectorAll('.chart-line')){
				const style=getComputedStyle(path),length=path.getTotalLength();
				assert(style.strokeDasharray==='none' && style.clipPath==='none' && path.isPointInStroke(path.getPointAtLength(Math.max(0,length-.01))),'completed line reaches segment endpoint');
			}
			assert(chart.querySelectorAll('.chart-table tbody tr').length===30 && !chart.querySelector('table').closest('[aria-hidden=true]'),'accessible native30day table');
			const image=chart.querySelector('.chart-svg');image.focus();image.dispatchEvent(new KeyboardEvent('keydown',{key:'Home',bubbles:true}));const first=chart.dataset.activeDate;
			image.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));assert(chart.dataset.activeDate>first,'keyboard explores next date');
			assert(getComputedStyle(chart.querySelector('.chart-hover-card')).display!=='none' && chart.querySelector('.chart-hover-value').textContent===chart.querySelector('.chart-readout-value').textContent,'focus tooltip matches readout');
		""")
		self.assertEqual([], _harness.errors())

	def test_d_unmount_removes_subscriptions(self):
		_harness.open("#/overview", "overview/ready").wait_ready()
		self.js("""
			r.navigate('#/live');await new Promise(resolve=>setTimeout(resolve,80));
			const root=document.querySelector('#view').firstElementChild;s.set('overview',{...s.get('overview'),generated_at:'2026-10-14T21:00:00Z'});
			assert(!document.querySelector('.overview-screen')&&document.querySelector('#view').firstElementChild===root,'unmounted overview cannot rerender');
		""")
		self.assertEqual([], _harness.errors())
