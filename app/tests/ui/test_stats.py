import _bootstrap  # noqa: F401
import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from selenium.webdriver.support.ui import WebDriverWait
from ui.harness import SCRATCH, SCREENSHOT_DIR, UI_DIR, UiHarness

_enabled=os.environ.get("REWARDS_APP_UI_TESTS")=="1"
_harness=None


def setUpModule():
	global _harness
	if _enabled:
		_harness=UiHarness()
		try:
			_harness.start().wait_ready()
		except BaseException:
			_harness.stop()
			raise


def tearDownModule():
	if _harness:_harness.stop()


VISUAL_RULES="""
 const root=document.querySelector('#view>.screen'),width=document.documentElement.clientWidth;
 const status=['--c-danger','--c-success','--c-warning','--c-info','--c-accent','--c-on-accent'].map(key=>{const n=document.createElement('span');n.style.color='var('+key+')';root.append(n);const c=getComputedStyle(n).color;n.remove();return c});
 assert(document.documentElement.scrollWidth<=width+1,'F75 no page horizontal clipping');
 for(const node of root.querySelectorAll('*')){
  if(!node.getClientRects().length||node.closest('[hidden]')||node.closest('details:not([open])')&&!node.closest('summary'))continue;
  const style=getComputedStyle(node);if(style.position==='absolute')continue;
  const texts=[...node.childNodes].filter(n=>n.nodeType===3&&n.textContent.trim());if(!texts.length)continue;
  const box=node.getBoundingClientRect();assert(box.left>=-1&&box.right<=width+1,'F75 text inside viewport '+node.className);
  if(!node.closest('pre'))for(const text of texts){const range=document.createRange();range.selectNodeContents(text);for(const glyph of range.getClientRects())if(glyph.width&&glyph.height)assert(glyph.left>=box.left-1&&glyph.right<=box.right+1,'F75 glyphs not clipped '+node.className);}
  assert(allowed.fonts.includes(style.fontSize),'F75 Overview font subset '+style.fontSize+' '+node.className);
  assert(allowed.colors.includes(style.color)||status.includes(style.color),'F75 Overview text color subset '+style.color+' '+node.className);
  if(single&&!node.closest('.reason,pre,.banner,.empty-state')){const tops=[];for(const n of texts){const r=document.createRange();r.selectNodeContents(n);for(const b of r.getClientRects())if(b.width&&b.height&&!tops.some(t=>Math.abs(t-b.top)<1))tops.push(b.top)}assert(tops.length<=1,'F75 text one line '+node.className);}
  if(!node.closest('pre')&&style.display!=='inline')assert(node.scrollWidth<=node.clientWidth+2,'F75 text not clipped '+node.className);
 }
  for(const row of root.querySelectorAll('.run-row.is-one-line')){const label=row.querySelector('.run-when'),when=label.getBoundingClientRect();assert(when.height<=parseFloat(getComputedStyle(label).lineHeight)+1,'F75 run label stays one line');assert(row.getBoundingClientRect().height<=Math.max(48,when.height+24)+1,'F75 run stays one line');for(const child of row.children){const box=child.getBoundingClientRect();assert(box.top>=when.top-2&&box.bottom<=when.bottom+2,'F75 run one-line children');}}
"""


def open_local(harness,route,fixture,theme,lang,region):
	parts=urlsplit(harness.url(route,fixture,theme,lang,True));query=dict(parse_qsl(parts.query));query["format"]=region
	harness.driver.get(urlunsplit(parts._replace(query=urlencode(query))));harness.wait_ready();harness.driver.refresh();harness.wait_ready()


class StatsSourceTests(unittest.TestCase):
	def test_f75_fixtures_match_named_producer_and_are_synthetic(self):
		import test_mock_shapes
		paths = sorted((UI_DIR / "mock/stats").glob("*.json"))
		self.assertLess(sum(path.stat().st_size for path in paths), 350000)
		for path in paths:
			fixture = json.loads(path.read_text(encoding="utf-8"))
			self.assertIs(fixture["synthetic"], True, path.name)
			answers = fixture["methods"]["get_stats"]
			for answer in answers if isinstance(answers, list) else [answers]:
				if answer.get("ok"):
					self.assertEqual([], test_mock_shapes.stats_problems(answer["data"]), path.name)
					data=answer["data"]
					for day in data["days"]:
						self.assertIn("sources",day)
						self.assertLessEqual(sum(day["sources"]["points"].values()),day["earned"] or 0,path.name)

	def test_f77_profile_cleanup_is_scoped_retried_and_nonblocking(self):
		from unittest.mock import patch
		from ui import harness
		profile = SCRATCH / "edge-shell-synthetic-cleanup"
		owned = UiHarness()
		owned.profile = profile
		with patch.object(harness.shutil,"rmtree",side_effect=[PermissionError("synthetic busy"),None]) as remove, patch.object(harness.time,"sleep") as delay:
			owned.stop()
			self.assertEqual(2,remove.call_count)
			delay.assert_called_once_with(0.2)
			self.assertIsNone(owned.profile)
		owned.profile = profile
		with patch.object(harness.shutil,"rmtree",side_effect=PermissionError("synthetic locked")), patch.object(harness.time,"monotonic",side_effect=[0,6]), patch("builtins.print") as output:
			owned.stop()
			self.assertTrue(any("harness_profile_cleanup=failed" in str(call) for call in output.call_args_list))
		for path in (SCRATCH.parent / "edge-shell-foreign",SCRATCH,SCRATCH / "not-a-profile"):
			owned.profile = path
			with patch.object(harness.shutil,"rmtree") as remove, patch("builtins.print") as output:
				owned.stop()
				remove.assert_not_called()
				self.assertTrue(any("harness_profile_cleanup=refused" in str(call) for call in output.call_args_list))
		owned.profile = profile
		with patch.object(Path,"is_symlink",return_value=True), patch.object(harness.shutil,"rmtree") as remove:
			owned.stop()
			remove.assert_not_called()
		owned.profile = profile
		with patch.object(Path,"is_junction",return_value=True), patch.object(harness.shutil,"rmtree") as remove:
			owned.stop()
			remove.assert_not_called()
		owned.profile = profile
		owned.driver = type("SyntheticDriver",(),{"quit":lambda self: (_ for _ in ()).throw(RuntimeError("synthetic quit failure"))})()
		with patch.object(harness.shutil,"rmtree") as remove:
			with self.assertRaisesRegex(RuntimeError,"synthetic quit failure"):
				owned.stop()
			remove.assert_called_once_with(profile)

	def test_f75_no_legacy_attribution_or_new_data_owner(self):
		code = (UI_DIR / "js/screens/stats.js").read_text(encoding="utf-8")
		self.assertNotIn("innerHTML", code)
		self.assertFalse((UI_DIR / "js/screens/stats-data.js").exists())
		for legacy in ("sources.parts", "sources.other", "sources.combined", "sources.complete"):
			self.assertNotIn(legacy, code)
		for relative in ("js/screens/stats.js", "css/screens/stats.css", "i18n/en/stats.json"):
			self.assertNotRegex((UI_DIR / relative).read_text(encoding="utf-8"), "[\u2013\u2014]")


@unittest.skipUnless(_enabled,"Run through guarded parent UI overlay")
class StatsTests(unittest.TestCase):
	def test_f77_capture(self):
		requested = os.environ.get("RF_F77_CAPTURE")
		if not requested:
			return
		import importlib.util
		import tempfile
		root = Path(requested).resolve()
		self.assertTrue(root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
		spec = importlib.util.spec_from_file_location("f77_capture", root / "capture.py")
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		module.run(_harness,root)

	def test_f77_selection_tracks_day_and_range(self):
		from selenium.webdriver.common.keys import Keys
		self.open()
		self.js("""
		 const data=s.get('stats'),today=data.days.find(day=>day.date===data.today),panel=document.querySelector('.stats-sources-panel');
		 window.f77Height=panel.getBoundingClientRect().height;
		 const actual=()=>Object.fromEntries([...document.querySelectorAll('.stats-source-row')].map(row=>[row.dataset.task,row.querySelector('dd').textContent]));
		 const expected=day=>Object.fromEntries(Object.entries(day.sources.points).map(([key,value])=>[key,i.formatNumber(value)]));
		 window.f77ReadSources=actual;window.f77ExpectedSources=expected;
		 assert(panel.dataset.date===data.today&&panel.querySelector('h2').textContent===i.t('stats.sources.day',{date:i.formatDate(data.today)}),'F77 preselected today heading');
		 """)
		if os.environ.get("RF_F77_MUTATION") == "range":
			_harness.driver.execute_script("import('./js/store.js').then(s=>{for(const row of document.querySelectorAll('.stats-source-row'))row.querySelector('dd').textContent=s.get('stats').sources.points[row.dataset.task]})")
		self.js("assert(JSON.stringify(window.f77ReadSources())===JSON.stringify(window.f77ExpectedSources(s.get('stats').days.at(-1))),'F77 task panel shows selected day not range')")
		self.js("""
		 document.querySelector('[data-date="2026-10-16"]').click();
		 const day=s.get('stats').days.find(day=>day.date==='2026-10-16');
		 assert(document.querySelector('.stats-sources-panel').dataset.date===day.date&&JSON.stringify(window.f77ReadSources())===JSON.stringify(window.f77ExpectedSources(day)),'F77 click updates day task values');
		 assert(document.querySelectorAll('.stats-day-run').length===1,'F77 click updates run list together');
		 """)
		_harness.driver.find_element("id","stats-day-2026-10-16").send_keys(Keys.ARROW_DOWN)
		self.assertEqual("2026-10-17",_harness.driver.switch_to.active_element.get_attribute("data-date"))
		self.js("assert(document.querySelector('.stats-sources-panel').dataset.date==='2026-10-16','F77 arrows focus previews do not select task panel')")
		_harness.driver.switch_to.active_element.send_keys(Keys.ENTER)
		self.js("""
		 const panel=document.querySelector('.stats-sources-panel'),day=s.get('stats').days.find(day=>day.date==='2026-10-17');
		 assert(panel.dataset.date===day.date&&JSON.stringify(window.f77ReadSources())===JSON.stringify(window.f77ExpectedSources(day)),'F77 Enter commits tasks');
		 assert(document.querySelectorAll('.stats-day-run').length===2,'F77 Enter commits runs');
		 const data=structuredClone(s.get('stats'));window.pywebview.api.get_stats=async request=>({ok:true,data:{...structuredClone(data),range:request.range}});
		 document.querySelector('#stats-range [data-value="7"]').click();await pause(150);
		 assert(document.querySelectorAll('.stats-day').length===7&&document.querySelector('.stats-sources-panel').dataset.date===day.date&&JSON.stringify(window.f77ReadSources())===JSON.stringify(window.f77ExpectedSources(day)),'F77 range switch preserves selected-day panel');
		 document.querySelector('[data-date="2026-10-14"]').click();
		 assert(document.querySelectorAll('.stats-source-empty').length===1&&!document.querySelector('.stats-source-row'),'F77 no counted runs one line no task rows');
		 assert(Math.abs(document.querySelector('.stats-sources-panel').getBoundingClientRect().height-window.f77Height)<1,'F77 empty panel keeps seven-row height');
		 """)
		self.open("young")
		self.js("assert(s.get('stats').days.at(-1).earned>0&&s.get('stats').days.at(-1).sources.runs===0&&!document.querySelector('.stats-source-row')&&document.querySelectorAll('.stats-source-empty').length===1,'F77 known earnings with no counted task rows')")
		self.assertEqual([],_harness.errors())

	def test_f77_hover_and_focus_preview_only(self):
		from selenium.webdriver.common.action_chains import ActionChains
		self.open()
		self.js("window.f77Panel=document.querySelector('.stats-sources-panel').outerHTML;window.f77Runs=document.querySelector('.stats-day-runs-host').innerHTML")
		if os.environ.get("RF_F77_MUTATION") == "hover":
			_harness.driver.execute_script("document.querySelector('[data-date=\"2026-10-17\"]').addEventListener('pointerenter',event=>event.currentTarget.click())")
		cell=_harness.driver.find_element("id","stats-day-2026-10-17")
		ActionChains(_harness.driver).move_to_element(cell).perform()
		self.js("""
		 assert(document.querySelector('.stats-calendar-detail h3').textContent===i.formatDate('2026-10-17'),'F77 hover previews date');
		 assert(document.querySelector('.stats-sources-panel').outerHTML===window.f77Panel&&document.querySelector('.stats-day-runs-host').innerHTML===window.f77Runs,'F77 hover keeps selected task panel and runs');
		 """)
		_harness.driver.execute_script("document.querySelector('[data-date=\"2026-10-18\"]').focus()")
		self.js("""
		 assert(document.querySelector('.stats-calendar-detail h3').textContent===i.formatDate('2026-10-18'),'F77 focus previews date');
		 assert(document.querySelector('.stats-sources-panel').outerHTML===window.f77Panel&&document.querySelector('.stats-day-runs-host').innerHTML===window.f77Runs,'F77 focus keeps selected task panel and runs');
		 """)
		self.assertEqual([],_harness.errors())

	def test_f77_probe(self):
		requested = os.environ.get("RF_F77_SCRATCH")
		if not requested:
			return
		import importlib.util
		import tempfile
		root = Path(requested).resolve()
		self.assertTrue(root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
		spec = importlib.util.spec_from_file_location("f77_probe", root / "probe.py")
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		module.run(_harness, root)

	def open(self,fixture="progress",theme="dark",lang="en",region="en-AT"):
		open_local(_harness,"#/stats","stats/"+fixture,theme,lang,region)
		WebDriverWait(_harness.driver,8).until(lambda d:d.execute_script("return document.querySelector('.stats-screen')?.getAttribute('aria-busy')==='false'"))

	def js(self,body,allowed=None,single=True):
		mutation=os.environ.get("RF_F75_UI_MUTATION")
		if mutation:
			self.assertIn(mutation,("legend","explanation","secondline","unknown-glyph"))
			_harness.driver.execute_script("""
			 const mutation=arguments[0],screen=document.querySelector('.stats-screen');
			 window.__f75MutateRows=()=>{for(const row of document.querySelectorAll('.stats-screen .run-row.is-one-line')){
			  const when=row.querySelector('.run-when');if(when.querySelector('[data-f75-mutant]'))continue;
			  const line=document.createElement('span');line.dataset.f75Mutant='true';line.textContent='synthetic second line';line.style.display='block';when.append(line);
			 }};
			 if(mutation==='secondline')window.__f75MutateRows();
			 else if(mutation==='unknown-glyph'){
			  const cell=screen.querySelector('.stats-day.state-no_check,.stats-day.state-before_tracking');
			  const glyph=screen.querySelector('.stats-day.state-done .icon');if(cell&&glyph)cell.append(glyph.cloneNode(true));
			 }
			 else if(screen&&!screen.querySelector('[data-f75-mutant]')){
			  const extra=document.createElement(mutation==='explanation'?'p':'div');extra.dataset.f75Mutant='true';extra.textContent='Synthetic forbidden explanation';
			  if(mutation==='legend')extra.className='stats-calendar-key';screen.append(extra);
			 }
			 """,mutation)
		result=_harness.driver.execute_async_script("const done=arguments[arguments.length-1],allowed=arguments[0],single=arguments[1];(async()=>{const assert=(v,m)=>{if(!v)throw Error(m)},pause=ms=>new Promise(r=>setTimeout(r,ms)),s=await import('./js/store.js'),i=await import('./js/i18n.js'),r=await import('./js/router.js');"+body+";return{ok:true}})().then(done).catch(e=>done({ok:false,error:String(e),stack:e.stack}))",allowed,single)
		self.assertTrue(result["ok"],result)

	def test_f75_minimal_known_values(self):
		self.open();self.js("""
		 const root=document.querySelector('.stats-screen');
		 assert(!root.querySelector('.stats-calendar-key,.stats-related,details,.stats-footnote,.stats-sources-explanation,.stats-check-group,.chart-explore'),'F75 no legend explanation or attribution controls');
		 assert(!root.textContent.includes('*')&&!root.textContent.includes(i.t('common.value.unknown')),'F75 unknown and marker omitted');
		 assert(root.querySelectorAll('.stats-source-row').length===7,'F75 seven measured task rows');
		 assert(root.querySelectorAll('.stats-metric').length===3,'F75 three large figures');
		 assert(root.querySelectorAll('p').length===6,'F75 no extra explanation sentence');
		 """)
		self.open("young");self.js("assert(!document.querySelector('.stats-source-row')&&document.querySelectorAll('.stats-source-empty').length===1,'F75 runs zero one empty line')")
		self.assertEqual([],_harness.errors())

	def test_f75_day_runs_and_ranges(self):
		self.open();self.js("""
		 const data=structuredClone(s.get('stats'));assert(document.querySelector('.stats-day.is-selected').dataset.date===data.today,'F75 today selected');
		 for(const range of [7,30,90]){const x=structuredClone(data);x.range=range;window.pywebview.api.get_stats=async()=>({ok:true,data:x});window.rf.receive([{topic:'overview.changed',data:{}}]);await pause(120);assert(document.querySelectorAll('.stats-day').length===range,'F75 exact range');}
		 document.querySelector('[data-date="2026-10-17"]').click();if(window.__f75MutateRows)window.__f75MutateRows();const rows=[...document.querySelectorAll('.stats-day-run')];assert(rows.length===2,'F75 no Stats checks');
		 assert(document.querySelector('.stats-day-progress').textContent.includes('100'),'F75 known percentage');
		 for(const row of rows){const label=row.querySelector('.run-when'),when=label.getBoundingClientRect();assert(when.height<=parseFloat(getComputedStyle(label).lineHeight)+1,'F75 run label stays one line');assert(row.getBoundingClientRect().height<=Math.max(48,when.height+24)+1,'F75 run stays one line');for(const n of row.children){const b=n.getBoundingClientRect();assert(b.top>=when.top-2&&b.bottom<=when.bottom+2,'F75 run one-line children');}}
		 assert(document.querySelectorAll('.stats-run-reason').length===1,'F75 failure reason only');
		 rows[0].click();await pause(150);assert(location.hash==='#/history/synthetic-partial','F75 opens exact History run');
		 """);self.assertEqual([],_harness.errors())

	def test_f75_unknown_day_omits_values(self):
		self.open();self.js("document.querySelector('[data-date=\"2026-10-14\"]').click();assert(!document.querySelector('.stats-day-progress,.stats-day-run,.stats-run-reason'),'F75 unknown date only');assert(!document.querySelector('.stats-calendar-detail').textContent.includes(i.t('common.value.unknown')),'F75 no unavailable')")
		self.js("""
		 const data=structuredClone(s.get('stats'));data.totals.week_earned=null;data.totals.month_earned=null;data.streak.current=null;
		 data.days.at(-1).earned=null;data.days.at(-1).possible=null;data.days.at(-1).runs.forEach(run=>run.points_earned=null);
		 window.pywebview.api.get_stats=async()=>({ok:true,data});window.rf.receive([{topic:'overview.changed',data:{}}]);await pause(150);
		 document.querySelector('[data-date="2026-10-19"]').click();
		 assert(!document.querySelector('.stats-metric,.stats-day-progress,.run-points'),'F75 null period day and run values omitted');
		 assert(!document.querySelector('.stats-screen').textContent.includes(i.t('common.value.unknown')),'F75 no null replacement label');
		 """)

	def test_f75_unknown_cells_are_blank_known_outcomes_keep_glyphs(self):
		self.open();self.js("""
		 const check=()=>{
		  for(const cell of document.querySelectorAll('.stats-day.state-no_check,.stats-day.state-before_tracking'))assert(cell.children.length===0,'G3 unknown calendar cell has no glyph');
		  for(const cell of document.querySelectorAll('.stats-day.state-done,.stats-day.state-missed'))assert(cell.querySelector('.icon'),'G3 known outcome glyph retained');
		 };check();
		 const data=structuredClone(s.get('stats')),prior=data.days.at(-2);prior.state='missed';
		 data.days.at(-3).state='no_check';data.days.at(-3).earned=null;data.days.at(-3).possible=null;
		 data.days.at(-1).state='today_open';window.pywebview.api.get_stats=async()=>({ok:true,data});window.rf.receive([{topic:'overview.changed',data:{}}]);await pause(150);check();
		 assert(document.querySelector('.stats-day.is-today .stats-day-open'),'G3 today open look retained');
		 document.querySelector('.stats-day.state-no_check').click();assert(!document.querySelector('.stats-day-progress'),'G3 unknown selected day still omits progress');
		 """);self.assertEqual([],_harness.errors())

	def test_f75_zero_denominator_keyboard_and_regional_requests(self):
		self.open();self.js("""
		 const data=structuredClone(s.get('stats'));data.days.at(-1).earned=0;data.days.at(-1).possible=0;
		 data.accounts.push({account_id:'synthetic-second',label:'Second synthetic account',is_root:false});
		 const calls=[];window.pywebview.api.get_stats=async request=>{calls.push(request);return{ok:true,data:{...structuredClone(data),range:request.range,account_id:request.account_id}}};
		 window.rf.receive([{topic:'overview.changed',data:{}}]);await pause(150);
		 assert(!document.querySelector('.stats-day-progress strong'),'F75 zero denominator no fabricated percent');
		 assert(document.querySelector('.stats-day-progress').textContent.includes('0'),'F75 known zero stays visible');
		 document.querySelector('#stats-range [data-value="7"]').click();await pause(150);
		 assert(calls.at(-1).range===7&&calls.at(-1).week_start===1&&calls.at(-1).account_id===null,'F75 regional request contract');
		 const account=document.querySelector('#stats-account');account.value='synthetic-second';account.dispatchEvent(new Event('change'));await pause(150);
		 assert(calls.at(-1).account_id==='synthetic-second','F75 account selector uses stable ID');
		 const before=document.querySelector('[data-date="2026-10-18"]');before.focus();before.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));
		 assert(document.activeElement.dataset.date==='2026-10-19','F75 keyboard day navigation');
		 document.activeElement.click();assert(document.activeElement.getAttribute('aria-pressed')==='true','F75 keyboard selection');
		 """);self.assertEqual([],_harness.errors())

	def test_f75_locale_typography_matrix(self):
		from ui.test_locales import REGIONS,locales
		count=0
		for lang,direction in [("en","ltr")]+locales():
			for width,height in ((1000,700),(1131,643),(1440,900),(1920,1000),(860,600)):
				for theme in ("dark","light"):
					with self.subTest(lang=lang,width=width,theme=theme):
						_harness.viewport(width,height);open_local(_harness,"#/overview","overview/ready",theme,lang,REGIONS.get(lang,"en-AT"))
						allowed=_harness.driver.execute_script("const n=[...document.querySelectorAll('.overview-screen *')].filter(n=>[...n.childNodes].some(x=>x.nodeType===3&&x.textContent.trim()));return{fonts:[...new Set(n.map(x=>getComputedStyle(x).fontSize))],colors:[...new Set(n.map(x=>getComputedStyle(x).color))]}")
						self.open(theme=theme,lang=lang,region=REGIONS.get(lang,"en-AT"));self.js("document.querySelector('[data-date=\"2026-10-17\"]').click();"+VISUAL_RULES,allowed,lang in ("en","de"));self.assertEqual([],_harness.errors());count+=1
		print(f"F75 Stats typography/layout matrix {count}cases",flush=True)
