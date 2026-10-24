import _bootstrap  # noqa: F401
import json
import os
import unittest
from pathlib import Path
from selenium.webdriver.support.ui import WebDriverWait
from ui.harness import SCRATCH, SCREENSHOT_DIR, UI_DIR, UiHarness, _Handler
from ui.test_stats import open_local

_enabled=os.environ.get("REWARDS_APP_UI_TESTS")=="1"
_harness=None
_original_get=None

F81_SHADE_PROBE="""
const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
const context=canvas.getContext('2d',{willReadFrequently:true});
const rgb=color=>{context.clearRect(0,0,1,1);context.fillStyle=color;context.fillRect(0,0,1,1);return [...context.getImageData(0,0,1,1).data].slice(0,3)};
const hex=color=>'#'+rgb(color).map(value=>value.toString(16).padStart(2,'0')).join('').toUpperCase();
const luminance=color=>rgb(color).map(value=>{const s=value/255;return s<=.04045?s/12.92:((s+.055)/1.055)**2.4}).reduce((sum,value,index)=>sum+value*[.2126,.7152,.0722][index],0);
const contrast=(a,b)=>(Math.max(luminance(a),luminance(b))+.05)/(Math.min(luminance(a),luminance(b))+.05);
const oklab=color=>{const [r,g,b]=rgb(color).map(value=>{const s=value/255;return s<=.04045?s/12.92:((s+.055)/1.055)**2.4}),l=Math.cbrt(.4122214708*r+.5363325363*g+.0514459929*b),m=Math.cbrt(.2119034982*r+.6806995451*g+.1073969566*b),s=Math.cbrt(.0883024619*r+.2817188376*g+.6299787005*b);return[.2104542553*l+.793617785*m-.0040720468*s,1.9779984951*l-2.428592205*m+.4505937099*s,.0259040371*l+.7827717662*m-.808675766*s]};
const distance=(a,b)=>Math.hypot(...oklab(a).map((value,index)=>value-oklab(b)[index]));
const background=node=>{for(let item=node;item;item=item.parentElement){const color=getComputedStyle(item).backgroundColor;if(color!=='rgba(0, 0, 0, 0)'&&color!=='transparent')return color}return getComputedStyle(document.documentElement).backgroundColor};
const shadeReport=block=>{
 const surface=background(block),band=block.querySelector('.history-points-band');
 const rows=[...band.querySelectorAll('rect')].map(segment=>{
  const task=segment.dataset.task,fill=getComputedStyle(segment).fill,tile=block.querySelector('.history-task-list [data-task="'+task+'"]')?.closest('.history-task-entry'),icon=tile?.querySelector('.history-task-icon'),iconColor=icon?getComputedStyle(icon).color:null;
  return{task,hex:hex(fill),segmentContrast:contrast(fill,surface),iconHex:iconColor?hex(iconColor):null,iconContrast:tile?contrast(iconColor,background(tile)):null,divider:getComputedStyle(segment).stroke,dividerWidth:parseFloat(getComputedStyle(segment).strokeWidth)};
 });
 const labels=[...band.querySelectorAll('.history-band-value')].filter(label=>getComputedStyle(label).visibility==='visible').map(label=>({task:label.dataset.task,hex:hex(getComputedStyle(label).fill),contrast:contrast(getComputedStyle(label).fill,surface)}));
 rows.forEach((row,index)=>{row.neighbourDistance=index?distance(rows[index-1].hex,row.hex):null});
 return{surface:hex(surface),tileSurface:hex(background(block.querySelector('.history-task-entry'))),rows,labels};
};
"""


def setUpModule():
	global _harness,_original_get
	if _enabled:
		if os.environ.get("RF_F81_MUTATION"):
			from urllib.parse import urlsplit
			mutation=os.environ["RF_F81_MUTATION"]
			target="/js/screens/history-points.js"
			source=(UI_DIR/"js/screens/history-points.js").read_text(encoding="utf-8")
			if mutation=="band":
				old='const total = known.reduce((sum, task) => sum + task.points, 0);'
				new='const total = 187;'
			elif mutation=="attention":
				old='export function needsAttention(run) {'
				new='export function needsAttention(run) { return false;'
			elif mutation=="zero-tile":
				old='const tile = task => ["ok", "running"].includes(task.status) && ((Number.isFinite(task.points) && task.points > 0) || (withoutReadings && task.status === "ok"));'
				new=old.replace('task.points > 0','task.points >= 0')
			elif mutation=="same-shade":
				target='/css/screens/history.css'
				source=(UI_DIR/'css/screens/history.css').read_text(encoding='utf-8')
				old='.history-source-explore_on_bing { --history-source: color-mix(in oklab, var(--history-ramp-start) 83.333333%, var(--history-ramp-end)); }'
				new=old.replace('83.333333%','100%')
			elif mutation=="latest-run":
				target='/js/screens/history.js'
				source=(UI_DIR/'js/screens/history.js').read_text(encoding='utf-8')
				old='dayRuns.forEach(run => pageHost.append(runBlock(run, run !== expandedRun)));'
				new='dayRuns.forEach((run, index) => pageHost.append(runBlock(run, index > 0)));'
			elif mutation=="state-notice":
				target='/js/screens/history.js'
				source=(UI_DIR/'js/screens/history.js').read_text(encoding='utf-8')
				old='            const unreadable = stateNotice(account);\n            if (unreadable) body.append(unreadable);'
				new='            if (!checkOnly) {\n                const unreadable = stateNotice(account);\n                if (unreadable) body.append(unreadable);\n            }'
			else:raise ValueError("Unknown F81 test mutation")
			if source.count(old)!=1:raise AssertionError("F81 mutation must hit one actual production helper branch")
			payload=source.replace(old,new).encode("utf-8")
			_original_get=_Handler.do_GET
			def mutated_get(handler):
				if urlsplit(handler.path).path==target:
					handler.send_response(200);handler.send_header("Content-Type","text/css" if target.endswith('.css') else "text/javascript")
					handler.send_header("Content-Length",str(len(payload)));handler.end_headers();handler.wfile.write(payload)
				else:_original_get(handler)
			_Handler.do_GET=mutated_get
		_harness=UiHarness()
		try:
			_harness.start().wait_ready()
		except BaseException:
			_harness.stop()
			raise


def tearDownModule():
	if _harness:_harness.stop()
	if _original_get is not None:_Handler.do_GET=_original_get


class HistorySourceTests(unittest.TestCase):
	def test_f81_r3_band_label_boundary_without_browser(self):
		import shutil
		import subprocess
		node=shutil.which('node')
		if node is None:self.skipTest('Node.js is not on PATH; this source test executes the actual JavaScript label helper')
		module=(UI_DIR/'js/screens/history-points.js').as_uri()
		program="import {bandLabelFits} from "+json.dumps(module)+";const cases=[[.1,200,8,true],[.1,200,8.01,false],[1,0,8,false],[1,200,0,false],[NaN,200,8,false],[1,200,180,true]];for(const [s,w,t,expected] of cases)if(bandLabelFits(s,w,t)!==expected)throw Error('label fit boundary');console.log('6 actual helper boundaries passed');"
		result=subprocess.run([node,'--input-type=module','-e',program],capture_output=True,text=True,encoding='utf-8',timeout=10)
		self.assertEqual(0,result.returncode,result.stdout+result.stderr)
		self.assertIn('6 actual helper boundaries passed',result.stdout)

	def test_f81_week_has_four_checks_each_day_and_canonical_tasks(self):
		from rfapp.runs import _summary
		fixture=json.loads((UI_DIR/'mock/history/week.json').read_text(encoding='utf-8'))
		runs=fixture['methods']['get_history']['data']['runs'];answers={answer['data']['run_id']:answer['data'] for answer in fixture['methods']['get_run'] if answer.get('ok')}
		for day in ('2026-10-15','2026-10-16','2026-10-17','2026-10-18','2026-10-19','2026-10-20','2026-10-21'):
			items=[run for run in runs if run['started_at'].startswith(day)]
			self.assertEqual(4,sum(run['kind']=='check' for run in items),day)
			self.assertEqual(2 if day in ('2026-10-19','2026-10-20') else 1,sum(run['kind']=='run' for run in items),day)
			for run in items:
				self.assertIn(run['run_id'],answers)
				self.assertEqual(run,_summary(answers[run['run_id']]),run['run_id'])
		self.assertEqual(87,sum(task['points'] or 0 for task in answers['week-today']['accounts'][0]['tasks']))
		self.assertEqual(187,answers['week-today']['points_earned'])
		self.assertEqual(20,answers['week-app']['points_earned'])
		self.assertEqual(6,sum(task['points']==0 for task in answers['week-app']['accounts'][0]['tasks']))
		self.assertTrue(all(task['points'] is None for task in answers['week-failure']['accounts'][0]['tasks']))
		self.assertEqual(['searches'],[task['task'] for task in answers['week-failure']['accounts'][0]['tasks'] if task['status']=='fail'])

	def test_f79_r2_unknown_task_points_are_not_invented(self):
		fixture=json.loads((UI_DIR/"mock/history/partial-unknown.json").read_text(encoding="utf-8"))
		run=fixture["methods"]["get_run"]["data"];tasks=run["accounts"][0]["tasks"]
		self.assertEqual(87,run["points_earned"])
		self.assertTrue(all(task["points"] is None and task["points_before"] is None and task["points_after"] is None for task in tasks))
		self.assertEqual(4,sum(task["status"]=="ok" for task in tasks))
		self.assertEqual(2,sum(task["status"]=="skip" for task in tasks))
		self.assertEqual(["searches"],[task["task"] for task in tasks if task["status"]=="fail"])
		full=json.loads((UI_DIR/"mock/history/full.json").read_text(encoding="utf-8"))["methods"]["get_run"]["data"]
		self.assertEqual(105,sum(task["points"] for task in full["accounts"][0]["tasks"]))

	def test_f75_synthetic_detail_and_log_offsets_are_coherent(self):
		for path in (UI_DIR / "mock/history").glob("*.json"):
			self.assertIs(json.loads(path.read_text(encoding="utf-8"))["synthetic"], True, path.name)
		fixture=json.loads((UI_DIR / "mock/history/detail.json").read_text(encoding="utf-8"))
		run=fixture["methods"]["get_run"]["data"];account=run["accounts"][0]
		self.assertEqual(run["points_earned"], sum(task["points"] for task in account["tasks"] if task["points"] is not None))
		self.assertNotEqual(run["points_earned"], sum(task["points_after"]-task["points_before"] for task in account["tasks"]))
		self.assertEqual(account["points_earned"],account["state_after"]["balance"]-account["state_before"]["balance"])
		for path in (UI_DIR / "mock/history").glob("*.json"):
			answers=json.loads(path.read_text(encoding="utf-8"))["methods"].get("get_run",[])
			for answer in answers if isinstance(answers,list) else [answers]:
				if answer.get("ok"):
					for account in answer["data"]["accounts"]:
						for task in account["tasks"]:
							self.assertIn("points",task,path.name)
							self.assertTrue(task["points"] is None or type(task["points"]) is int,path.name)
		self.assertNotIn("points_before",(UI_DIR/"js/screens/history.js").read_text(encoding="utf-8"))
		offset=0
		for page in sorted(fixture["methods"]["get_run_log"],key=lambda entry:entry.get("when",{}).get("offset",0)):
			offset+=len(page["data"]["text"].encode("utf-8"));self.assertEqual(offset,page["data"]["next_offset"])
		self.assertEqual(offset,run["log_size"])

	def test_f75_catalog_notes_and_logical_layout(self):
		for relative in ("js/screens/history.js","css/screens/history.css","i18n/en/history.json"):
			text=(UI_DIR/relative).read_text(encoding="utf-8");self.assertNotRegex(text,"[\u2013\u2014]");self.assertNotIn("innerHTML",text)
		self.assertNotRegex((UI_DIR/"css/screens/history.css").read_text(encoding="utf-8"),r"(?:margin|padding)-(?:left|right)|\b(?:left|right)\s*:|text-align\s*:\s*(?:left|right)")


@unittest.skipUnless(_enabled,"Run through guarded parent UI overlay")
class HistoryTests(unittest.TestCase):
	# F81 replaces F79's flat-list geometry. Data, diagnostics and log invariants
	# stay covered below on the day-page renderer instead of stale row selectors.
	def open(self,route="#/history",fixture="week",theme="dark",lang="en",region="en-AT"):
		open_local(_harness,route,"history/"+fixture,theme,lang,region)
		WebDriverWait(_harness.driver,8).until(lambda d:d.execute_script("return document.querySelector('.history-screen')?.getAttribute('aria-busy')==='false'"))

	def js(self,body):
		result=_harness.driver.execute_async_script("const done=arguments[arguments.length-1];(async()=>{const assert=(v,m)=>{if(!v)throw Error(m)},pause=ms=>new Promise(r=>setTimeout(r,ms)),wait=async fn=>{for(let n=0;n<300;n++){if(fn())return;await pause(10)}throw Error('F81 wait timeout')},i=await import('./js/i18n.js'),r=await import('./js/router.js');"+body+";return{ok:true}})().then(done).catch(e=>done({ok:false,error:String(e),stack:e.stack}))");self.assertTrue(result["ok"],result)

	def _state_notice_cases(self,cases,lang):
		_harness.viewport(1131,643);self.open('#/history/day:2026-10-21',lang=lang,region='de-DE' if lang=='de' else 'en-AT')
		self.js('const cases='+json.dumps(cases)+';'+"""
		 const fixture=await(await fetch('./mock/history/week.json')).json(),clone=id=>structuredClone(fixture.methods.get_run.find(answer=>answer.data?.run_id===id).data);
		 const checkSeed=clone('week-today-check'),runSeed=clone('week-app'),snapshot=clone('week-warning-check').accounts[0].state_before;
		 let current;
		 const summary=run=>{const {warnings,accounts,...rest}=structuredClone(run);return{...rest,accounts:accounts.map(({tasks,state_before,state_after,...account})=>({...account,task_counts:Object.fromEntries(['ok','skip','fail','off'].map(status=>[status,tasks.filter(task=>task.status===status).length]))}))}};
		 window.pywebview.api.get_history=async()=>({ok:true,data:{runs:[summary(current)],next_before:null}});
		 window.pywebview.api.get_run=async({run_id})=>{assert(run_id===current.run_id,'state notice synthetic run identity');return{ok:true,data:structuredClone(current)}};
		 window.historyStateNoticeReport=[];
		 for(const [index,test] of cases.entries()){
		  const {code,slot,kind}=test,isCheck=kind==='check';current=structuredClone(isCheck?checkSeed:runSeed);
		  Object.assign(current,{run_id:'state-notice-'+index,kind,trigger:'scheduled',started_at:'2026-10-21T09:00:00Z',ended_at:'2026-10-21T09:01:00Z',duration_ms:60000,log_size:0});
		  const account=current.accounts[0];account.label='Alex (synthetic)';account.error=null;
		  const state=unreadable=>({...structuredClone(snapshot),at:current.started_at,unreadable});
		  account.state_before=state(slot==='after'?'signed_out':code);account.state_after=slot==='after'?state(code):null;
		  if(isCheck){account.decision={run:false,reason:code?'unreadable':'below_threshold',reason_key:code?'errors.decision.unreadable':'errors.decision.below_threshold',open_points:code?null:10,min_points:30};account.tasks.forEach((task,n)=>{task.status=n===6?'off':'not_run';task.reason_key=n===6?'errors.task.turned_off':'errors.task.not_run'});}
		  document.querySelector('.history-screen .screen-header button').click();
		  await wait(()=>document.querySelector('.history-screen')?.getAttribute('aria-busy')==='false'&&document.querySelector('.history-day-page')?.dataset.date==='2026-10-21');
		  const group=document.querySelector('.history-check-group');if(isCheck){assert(group&&!group.open,'History check group initially folded');group.querySelector('summary').click();}
		  await wait(()=>document.querySelector('[data-run-id="'+current.run_id+'"]'));
		  const block=document.querySelector('[data-run-id="'+current.run_id+'"]'),notices=[...block.querySelectorAll('.banner.tone-warning .banner-message')],decision=block.querySelector('.history-decision');
		  const whole=['signed_out','terms_of_use','page_blank'].includes(code),key=whole?'errors.state.'+code:'errors.state.sections',params=whole?{account:account.label}:{sections:code?i.formatList(code.split(',').map(section=>i.t('common.section.'+section))):''};
		  const expected=code?i.t(key,params):null,label='History state notice '+String(code)+' '+slot+' '+kind+' '+document.documentElement.lang;
		  assert(notices.length===(code?1:0),label+' expected='+String(expected)+' actual='+JSON.stringify(notices.map(node=>node.textContent))+' decision='+String(decision?.textContent));
		  if(code){const notice=notices[0];assert(notice.textContent===expected&&notice.checkVisibility()&&!notice.closest('details:not([open])'),label+' exact visible notice');const rect=notice.getBoundingClientRect();assert(rect.left>=0&&rect.right<=innerWidth+1&&notice.scrollWidth<=notice.clientWidth+1,label+' notice fits');}
		  if(isCheck){const expectedDecision=i.t(account.decision.reason_key,{points:account.decision.open_points,min_points:i.formatNumber(30)});assert(decision?.textContent===expectedDecision&&decision.checkVisibility(),label+' decision kept');assert(!block.querySelector('.history-task-list,.history-no-points,.history-points-band'),label+' no task attempts invented');if(code)assert(decision.nextElementSibling===notices[0].closest('.banner')&&decision.textContent!==expected,label+' separate decision and recovery notice');}
		  else{assert(!decision&&block.querySelector('[data-task="misc_cards"] .history-task-points')?.textContent===i.formatNumber(20),label+' non-check tiles unchanged');}
		  const attention=Boolean(code),date=document.querySelector('.history-date[data-date="2026-10-21"]');
		  assert(date.querySelectorAll('.history-attention').length===Number(attention)&&block.querySelectorAll('.history-run-marks .history-attention').length===Number(attention),label+' existing exception marks');
		  if(isCheck)assert(group.querySelectorAll(':scope>summary .history-attention').length===Number(attention),label+' existing group attention');
		  assert(!document.querySelector('.history-app-run,.history-wait-mark')&&!document.querySelector('.history-notices .banner'),label+' no new marks or fetch errors');
		  assert(document.documentElement.scrollWidth<=innerWidth+1&&document.getElementById('view').scrollWidth<=document.getElementById('view').clientWidth+1,label+' no sideways scroll');
		  window.historyStateNoticeReport.push({...test,language:document.documentElement.lang,notice:expected,decision:decision?.textContent||null,attention,noHorizontalOverflow:true});
		 }
		 """)
		self.assertEqual([],_harness.errors())
		print('History state notice cases '+json.dumps(_harness.driver.execute_script('return window.historyStateNoticeReport'),ensure_ascii=True),flush=True)

	def test_check_only_signed_out_state_notice(self):
		self._state_notice_cases([{'code':'signed_out','slot':'before','kind':'check'}],'en')

	def test_check_only_state_notices_and_noncheck_controls(self):
		cases=[{'code':code,'slot':slot,'kind':kind} for code in ('signed_out','terms_of_use','page_blank','daily_set,searches',None) for slot in ('before','after') for kind in ('check','run')]
		for lang in ('en','de'):
			with self.subTest(lang=lang):self._state_notice_cases(cases,lang)

	def test_f81_band_proportions_and_task_truth(self):
		_harness.viewport(1131,643);self.open();self.js("""
		 const block=document.querySelector('[data-run-id="week-today"]'),band=block.querySelector('.history-points-band'),rects=[...band.querySelectorAll('rect')];
		 assert(block.querySelector('.history-run-points').textContent===i.t('common.points.total',{count:187}),'F81 run keeps187 independently of tasks87');
		 assert(rects.length===4,'F81 no residual,zero,unknown or filler segment');
		 const expected={daily_set:30,explore_on_bing:15,visual_search:12,searches:30};
		 for(const rect of rects){const row=block.querySelector('.history-task-list [data-task="'+rect.dataset.task+'"]');assert(row&&row.querySelector('.history-task-points').textContent===i.formatNumber(expected[rect.dataset.task]),'F81 tiles are measured task points');assert(Math.abs(rect.getBoundingClientRect().width-band.getBoundingClientRect().width*expected[rect.dataset.task]/87)<=1,'F81 proportional task band within1px');}
		 assert(Math.abs(rects.reduce((n,x)=>n+x.getBoundingClientRect().width,0)-band.getBoundingClientRect().width)<=1,'F81 task band covers measured tasks only');
		 assert(block.querySelector('[data-task="bonus"] .history-task-points').textContent===i.formatNumber(0),'F81 known0stays0');
		 assert(block.querySelector('.history-no-points:not([open])').querySelectorAll('.history-task-row').length===3,'F81 zero and two disabled tasks folded not removed');
		 assert(!block.querySelector('.history-no-points [data-task="quests"] .history-task-points'),'F81 null not zero');
		 const icons=[...block.querySelectorAll('.history-task-icon use')].map(n=>n.getAttribute('href'));assert(icons.length===7&&new Set(icons).size===7,'F81 seven distinct local task icons');
		 assert(!block.querySelector('.history-run-marks').textContent.trim(),'F81 ordinary scheduled run has no label');
		""");self.assertEqual([],_harness.errors())

	def test_f81_folded_attention_and_lazy_checks(self):
		_harness.viewport(1131,643);self.open();self.js("""
		 const calls=[],original=window.pywebview.api.get_run;window.pywebview.api.get_run=async req=>{calls.push(req.run_id);return original(req)};
		 r.navigate('#/history/'+encodeURIComponent('day:2026-10-20'));await wait(()=>document.querySelector('.history-day-page')?.dataset.date==='2026-10-20'&&document.querySelector('.history-screen').getAttribute('aria-busy')==='false');
		 const group=document.querySelector('.history-check-group');assert(group&&!group.open,'F81 check group folded by default');
		 assert(group.querySelector('summary .history-attention')?.textContent===i.t('history.mark.attention'),'F81 folded attention remains visible');
		 assert(!calls.includes('week-warning-check')&&calls.length===2,'F81 checks lazy,onlyselecteddayearningruns read');
		 group.querySelector('summary').click();await wait(()=>group.querySelector('[data-run-id="week-warning-check"]'));
		 assert(calls.includes('week-warning-check'),'F81 check details read only when opened');
		 const warning=group.querySelector('[data-run-id="week-warning-check"]');assert(warning.querySelector('.history-decision').textContent===i.t('errors.decision.unreadable'),'F81 check reason retained');
		 assert(group.querySelector('.banner')&&group.querySelector('.history-technical')&&group.querySelector('section.log-panel'),'F81 check warning diagnostic and Runlog reachable');
		 assert(!group.querySelector('.history-task-list'),'F81 no fabricated empty check tasks');
		""");self.assertEqual([],_harness.errors())

	def test_f81_date_rail_and_older_runs(self):
		_harness.viewport(1131,643);self.open();self.js("""
		 const links=[...document.querySelectorAll('.history-date')];assert(links.length===7&&links.every(n=>n.tagName==='A'),'F81 seven localday links');
		 assert(links[0].dataset.date==='2026-10-21'&&links[0].hasAttribute('aria-current'),'F81 newestdefaultselection');
		 assert(!links[0].querySelector('.history-date-marks').children.length,'F81 ordinaryday no mark');
		 for(const link of links)assert(!link.querySelector('.history-run-points,.history-task-points,.stat,.run-points'),'F81 no day total or points');
		 assert(!document.querySelector('.history-back').getBoundingClientRect().height,'F81 no Back onwide');
		 assert(getComputedStyle(document.querySelector('.history-day-title')).fontSize==='20px','F81 dayheading20');
		 const rail=document.querySelector('.history-date-rail').getBoundingClientRect(),page=document.querySelector('.history-day-page').getBoundingClientRect();assert(rail.right<page.left,'F81 date rail inline start');
		 r.navigate('#/history/'+encodeURIComponent('day:2026-10-20'));await wait(()=>document.querySelector('.history-day-page')?.dataset.date==='2026-10-20');
		 const blocks=[...document.querySelectorAll('.history-day-page>.history-run-block')];assert(blocks.length===2&&blocks[0].dataset.runId==='week-two-late','F81 twoearningruns newestfirst');
		 assert(blocks[0].querySelector('.history-task-list')&&!blocks[0].querySelector('.history-older-tasks'),'F81 newest tiles unfolded');
		 assert(blocks[1].querySelector('.history-older-tasks:not([open])')&&blocks[1].querySelector('.history-points-band'),'F81 older bandvisible tilesfolded');
		""");self.assertEqual([],_harness.errors())

	def test_f81_narrow_enter_back_and_direct_run(self):
		from selenium.webdriver.common.keys import Keys
		_harness.viewport(860,600);self.open();self.js("assert(!document.querySelector('.history-day-page').getBoundingClientRect().height&&document.querySelector('.history-date-rail').getBoundingClientRect().height,'F81 narrow list is page');")
		_harness.driver.find_element('css selector','.history-date').send_keys(Keys.ENTER)
		WebDriverWait(_harness.driver,10).until(lambda d:d.execute_script("return document.activeElement.id==='history-day-heading'"))
		self.js("assert(!document.querySelector('.history-date-rail').getBoundingClientRect().height&&document.querySelector('.history-back').getBoundingClientRect().height,'F81 narrowday ownpage');document.querySelector('.history-back').click();await wait(()=>document.activeElement.matches('.history-date[data-date=\"2026-10-21\"]'));assert(!document.querySelector('.history-day-page').getBoundingClientRect().height,'F81 Backrestoreslist');")
		self.open('#/history/week-two-early');self.js("await wait(()=>document.activeElement.dataset.runId==='week-two-early');const run=document.activeElement;assert(run.getBoundingClientRect().top>=0&&run.getBoundingClientRect().top<innerHeight,'F81 run link scrolled into view');assert(document.querySelector('.history-day-page').dataset.date==='2026-10-20','F81 runlinkopensday');")
		self.assertEqual([],_harness.errors())

	def test_f81_r2_zero_points_are_folded(self):
		_harness.viewport(1131,643);self.open('#/history/week-app');self.js("""
		 const block=document.querySelector('[data-run-id="week-app"]'),tiles=[...block.querySelectorAll('.history-task-list .history-task-row')],folds=block.querySelectorAll('.history-no-points');
		 assert(tiles.length===1&&tiles[0].dataset.task==='misc_cards','F81R2 no zero-point tile');
		 assert(tiles[0].querySelector('.history-task-points').textContent===i.formatNumber(20),'F81R2 measured20tile');
		 assert(folds.length===1&&!folds[0].open,'F81R2 one folded no-points disclosure');
		 const fold=folds[0];assert(fold.querySelector('summary').textContent===i.t('history.tasks.no_points',{count:6}),'F81R2 exact no-points key and count6');
		 assert(fold.querySelectorAll('.history-task-row').length===6,'F81R2 everyzero retained');fold.open=true;
		 for(const row of fold.querySelectorAll('.history-task-row'))assert(row.querySelector('.history-task-points')?.textContent===i.formatNumber(0)&&row.querySelector('.history-task-idle-state').textContent===i.t('common.task_status.ok'),'F81R2 openedzero readable with actualdone status');
		 """);self.open('#/history/week-zero');self.js("""
		 const block=document.querySelector('[data-run-id="week-zero"]'),fold=block.querySelector('.history-no-points');
		 assert(!block.querySelector('.history-task-list,.history-points-band'),'F81R2 no-tile run has no band');
		 assert(block.querySelector('.history-run-points').textContent===i.t('common.points.total',{count:0}),'F81R2 no-tile run keeps ownpoints');
		 assert(fold&&!fold.open&&fold.querySelector('summary').textContent===i.t('history.tasks.no_points',{count:7}),'F81R2 no-tile count7');fold.open=true;
		 for(const status of ['ok','skip','off','not_run'])assert(fold.querySelector('[data-status="'+status+'"]'),'F81R2 all no-points states retained');
		 assert(fold.querySelector('[data-task="daily_set"] .history-task-points').textContent===i.formatNumber(0),'F81R2 knownzero open');
		 assert(!fold.querySelector('[data-task="visual_search"] .history-task-points'),'F81R2 unknown not zero');
		 """);self.assertEqual([],_harness.errors())

	def test_f81_r2_failure_full_width_and_unknown_done(self):
		_harness.viewport(1131,643);self.open('#/history/week-failure');self.js("""
		 const block=document.querySelector('[data-run-id="week-failure"]'),line=block.querySelector('.history-task-failure'),facts=block.querySelector('.history-run-summary'),tiles=[...block.querySelectorAll('.history-task-list .history-task-row')];
		 assert(line&&line.dataset.task==='searches'&&line.previousElementSibling===facts,'F81R2 failure line right under time');
		 assert(!block.querySelector('.history-task-list [data-status="fail"]'),'F81R2 failed task is no tile');
		 assert(line.querySelector('.history-failure-name').textContent===i.t('common.task.searches')&&line.querySelector('.history-task-reason').textContent===i.t('errors.task.element_missing'),'F81R2 failure icon name reason preserved');
		 assert(line.querySelector('.history-task-icon use').getAttribute('href')==='img/history-icons.svg#search','F81R2 failure task icon');
		 const a=line.getBoundingClientRect(),b=facts.getBoundingClientRect();assert(Math.abs(a.left-b.left)<1&&Math.abs(a.width-b.width)<1,'F81R2 failure spans block');
		 const p=line.querySelector('p');assert(p.getBoundingClientRect().height<=parseFloat(getComputedStyle(p).lineHeight)*2+1,'F81R2 failure at most2lines');
		 assert(tiles.length===4&&tiles.every(row=>row.dataset.status==='ok'&&row.querySelector('.history-task-mark')&&!row.querySelector('.history-task-points')),'F81R2 unknownrecord donechecktiles preserved');
		 assert(!block.querySelector('.history-points-band'),'F81R2 unknownrecord no inventedband');
		 const store=await import('./js/store.js'),fixture=await(await fetch('./mock/history/week.json')).json(),run=fixture.methods.get_run.find(x=>x.data?.run_id==='week-failure').data;
		 run.accounts[0].tasks.find(x=>x.task==='daily_set').points=10;store.set('live',{run,log:null,last:null});const next=document.querySelector('[data-run-id="week-failure"]'),failure=next.querySelector('.history-task-failure'),band=next.querySelector('.history-points');assert(failure.compareDocumentPosition(band)&Node.DOCUMENT_POSITION_FOLLOWING,'F81R2 failure precedes band');assert(failure.getBoundingClientRect().bottom<=band.getBoundingClientRect().top,'F81R2 fullwidthfailure aboveband');
		 """);self.assertEqual([],_harness.errors())

	def test_f81_r2_one_source_heading_and_older_summary_band(self):
		_harness.viewport(1131,643);self.open('#/history/week-two-early');self.js("""
		 const title=i.t('history.sources.title');
		 for(const block of document.querySelectorAll('.history-day-page>.history-run-block')){const walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT);let count=0;while(walker.nextNode())if(walker.currentNode.textContent.trim()===title)count++;assert(count===1,'F81R2 exactly one Points by task per earningblock');}
		 const block=document.querySelector('[data-run-id="week-two-early"]'),older=block.querySelector('.history-older-tasks');assert(older&&!older.open&&older.querySelector('summary>.history-points-band'),'F81R2 older band in summary');assert(!block.querySelector('.history-points h3'),'F81R2 older duplicateheading removed');older.open=true;assert(older.querySelector('.history-task-list'),'F81R2 older tiles inspectable');
		 """);self.assertEqual([],_harness.errors())

	def test_f81_async_direct_check_retry_and_live_boundaries(self):
		_harness.viewport(1131,643);self.open('#/history/week-warning-check');self.js("""
		 await wait(()=>document.activeElement.dataset.runId==='week-warning-check');assert(document.querySelector('.history-check-group').open,'F81 directcheck opens containinggroup');
		 const node=document.activeElement;assert(node.getBoundingClientRect().top>=0&&node.getBoundingClientRect().top<innerHeight,'F81 directcheckscrollfocus');
		 """)
		self.open();self.js("""
		 const fixture=await(await fetch('./mock/history/week.json')).json(),run=fixture.methods.get_run.find(x=>x.data?.run_id==='week-today').data,store=await import('./js/store.js');
		 store.set('live',{run:{...run,active:true,result:null,result_key:null,points_earned:188},log:null,last:null});assert(document.querySelector('[data-run-id="week-today"] .sr-only').textContent===i.t('common.run.active'),'F81 activeupdated');assert(document.querySelector('.history-run-points').textContent===i.t('common.points.total',{count:188}),'F81 canonicalactivepoints');
		 window.pywebview.api.get_history=async()=>({ok:false,error:{code:'internal',message_key:'errors.app.internal',params:{},detail:null,fields:null}});
		 document.querySelector('.screen-header button').click();await wait(()=>document.querySelector('.history-notices .banner'));assert(document.querySelector('.history-notices .banner button'),'F81 refresherrorretry');
		 window.pywebview.api.get_history=async()=>fixture.methods.get_history;document.querySelector('.history-notices .banner button').click();await wait(()=>document.querySelector('.history-run-block')&&document.querySelector('.history-screen').getAttribute('aria-busy')==='false');
		 let release;const original=window.pywebview.api.get_run;window.pywebview.api.get_run=req=>new Promise(resolve=>{release=()=>original(req).then(resolve)});r.navigate('#/history/'+encodeURIComponent('day:2026-10-18'));await wait(()=>!!release);r.navigate('#/settings/app');await wait(()=>document.querySelector('[data-screen="settings"]'));release();await pause(80);assert(!document.querySelector('.history-screen'),'F81 lateanswer no redraw afterunmount');
		 """);self.assertEqual([],_harness.errors())

	def test_f81_visual_interaction_motion_and_forced_colors(self):
		from selenium.webdriver.common.keys import Keys
		_harness.driver.execute_cdp_cmd('Emulation.setEmulatedMedia',{'features':[{'name':'prefers-reduced-motion','value':'no-preference'}]})
		try:
			_harness.viewport(1131,643);_harness.open('#/history','history/week','dark','en',False).wait_ready()
			WebDriverWait(_harness.driver,10).until(lambda d:d.execute_script("return document.querySelector('.history-screen')?.getAttribute('aria-busy')==='false'"))
			_harness.driver.find_element('tag name','body').send_keys(Keys.TAB)
			self.js("""
			 const motion=message=>message+' systemReducedMotion='+matchMedia('(prefers-reduced-motion: reduce)').matches;
			 assert(!matchMedia('(prefers-reduced-motion: reduce)').matches,motion('F81 normal motion media'));
			 const rail=document.querySelector('.history-date-list');assert(rail.querySelector('ul>li>a'),'F81 semanticlistoflinks');const links=[...rail.querySelectorAll('a')];links[0].focus();links[0].dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));assert(document.activeElement===links[1],'F81 arrowfocusnotselection');assert(links[0].hasAttribute('aria-current'),'F81 focusedunselectedkeepsselection');links[1].dispatchEvent(new KeyboardEvent('keydown',{key:'Home',bubbles:true}));assert(document.activeElement===links[0],'F81 Homefocus');
			 const page=document.querySelector('.history-day-page'),normal=getComputedStyle(page);assert(normal.animationDuration==='0.1s',motion('F81 inspector100ms actual='+normal.animationDuration));assert(getComputedStyle(links[0]).transitionDuration==='0.1s',motion('F81 hover100ms'));
			 const idle=document.querySelector('.history-idle');idle.open=true;await pause(30);assert(getComputedStyle(idle.querySelector('.history-idle-body')).animationDuration==='0.18s',motion('F81 disclosure180ms'));
			 document.documentElement.dataset.reducedMotion='true';try{assert(getComputedStyle(page).animationName==='none'&&getComputedStyle(links[0]).transitionProperty==='none',motion('F81 reducedmotionimmediate'));}finally{document.documentElement.dataset.reducedMotion='false';}
			 const focused=getComputedStyle(document.activeElement);assert(focused.outlineWidth==='2px','F81 focus2px');
			 """)
			_harness.driver.execute_cdp_cmd('Emulation.setEmulatedMedia',{'features':[{'name':'prefers-reduced-motion','value':'reduce'}]})
			self.js("""
			 const systemReduced=matchMedia('(prefers-reduced-motion: reduce)').matches,motion=message=>message+' systemReducedMotion='+systemReduced;
			 assert(systemReduced&&document.documentElement.dataset.reducedMotion==='false',motion('F81 system reduced motion with app flag false'));
			 const page=document.querySelector('.history-day-page'),links=[...document.querySelectorAll('.history-date')],body=document.querySelector('.history-idle[open] .history-idle-body');
			 assert(page&&links.length&&body,motion('F81 system reduced motion targets'));
			 for(const node of [page,...links,body]){const style=getComputedStyle(node);assert(style.animationName==='none'&&style.transitionProperty==='none'&&style.animationDuration==='0s'&&style.transitionDuration==='0s',motion('F81 system reduced motion immediate '+node.className+' animation='+style.animationName+'/'+style.animationDuration+' transition='+style.transitionProperty+'/'+style.transitionDuration));}
			 """)
			_harness.driver.execute_cdp_cmd('Emulation.setEmulatedMedia',{'features':[{'name':'prefers-reduced-motion','value':'no-preference'},{'name':'forced-colors','value':'active'}]})
			self.js("const page=document.querySelector('.history-day-page'),systemReduced=matchMedia('(prefers-reduced-motion: reduce)').matches;assert(!systemReduced&&matchMedia('(forced-colors: active)').matches&&getComputedStyle(page).animationName==='none','F81 forcedcolorsimmediate systemReducedMotion='+systemReduced);")
			self.assertEqual([],_harness.errors())
		finally:_harness.driver.execute_cdp_cmd('Emulation.setEmulatedMedia',{'features':[]})

	def test_f81_partial_day_paging_and_dedup(self):
		_harness.viewport(1131,643);self.open();self.js("""
		 const fixture=await(await fetch('./mock/history/week.json')).json(),all=fixture.methods.get_history.data.runs,requests=[],details=[],original=window.pywebview.api.get_run;
		 const chosen=['week-two-late','week-two-early','week-warning-check'].map(id=>all.find(x=>x.run_id===id)),earlier=all.find(x=>x.run_id==='week-wait');
		 window.pywebview.api.get_run=async req=>{details.push(req.run_id);return original(req)};
		 window.pywebview.api.get_history=async req=>{requests.push(req);return{ok:true,data:req.before==='boundary2'?{runs:[chosen[2],earlier,chosen[0]],next_before:'older'}:req.before?{runs:[chosen[1]],next_before:'boundary2'}:{runs:[chosen[0]],next_before:'boundary1'}}};
		 document.querySelector('.screen-header button').click();await wait(()=>requests.length===3&&document.querySelector('.history-day-page')?.dataset.date==='2026-10-20'&&document.querySelector('.history-screen').getAttribute('aria-busy')==='false');
		 assert(requests[1].before==='boundary1'&&requests[2].before==='boundary2'&&requests.every(x=>x.limit===50&&x.include_checks),'F81 partialday pages to earlierday');
		 assert(document.querySelectorAll('.history-day-page>.history-run-block').length===2,'F81 complete earningday no duplicate');
		 assert(document.querySelector('.history-check-group').dataset.checkCount==='1','F81 completecheckcount');
		 assert(details.length===2&&details.every(x=>['week-two-early','week-two-late'].includes(x)),'F81 no details from otherday orclosedchecks');
		 const count=document.querySelectorAll('.history-date').length;window.pywebview.api.get_history=async req=>({ok:true,data:{runs:[chosen[0]],next_before:null}});document.querySelector('.history-date-rail>button').click();await wait(()=>document.querySelector('.history-date-rail>button').hidden);assert(document.querySelectorAll('.history-date').length===count,'F81 loadearlierdedup');
		""");self.assertEqual([],_harness.errors())

	def test_f81_r3_shades_and_contrast(self):
		reports={}
		for theme in ('dark','light'):
			with self.subTest(theme=theme):
				_harness.viewport(1131,643);self.open(theme=theme)
				self.js(F81_SHADE_PROBE+"""
				 const fixture=await(await fetch('./mock/history/week.json')).json(),run=fixture.methods.get_run.find(answer=>answer.data?.run_id==='week-today').data,store=await import('./js/store.js'),tasks=run.accounts[0].tasks;
				 tasks.forEach((task,index)=>Object.assign(task,{status:'ok',reason_key:null,points:[30,15,5,10,20,60,1][index]}));store.set('live',{run,log:null,last:null});
				 await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
				 const block=document.querySelector('[data-run-id="week-today"]'),report=shadeReport(block);
				 assert(report.rows.length===7&&new Set(report.rows.map(row=>row.hex)).size===7,'F81R3 seven distinct rendered shades');
				 for(const row of report.rows){assert(row.segmentContrast>=3,'F81R3 segment-card contrast3 '+row.task+' '+row.segmentContrast);assert(row.iconHex===row.hex&&row.iconContrast>=3,'F81R3 matching icon-tile contrast3 '+row.task+' '+row.iconContrast);assert(row.dividerWidth>=1&&hex(row.divider)===report.surface,'F81R3 surface divider');}
				 for(let index=1;index<report.rows.length;index++)assert(luminance(report.rows[index].hex)>luminance(report.rows[index-1].hex),'F81R3 fixed lightness ramp in task order');
				 for(const row of report.rows.slice(1))assert(row.neighbourDistance>=.05,'F81R3 neighbouring OKLab distance0.05 '+row.task+' '+row.neighbourDistance);
				 const palette=new Map(report.rows.map(row=>[row.task,row.hex]));tasks.forEach((task,index)=>{task.points=[1,2,3,4,5,6,200][index]});store.set('live',{run,log:null,last:null});await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));const changed=shadeReport(document.querySelector('[data-run-id="week-today"]'));assert(changed.rows.every(row=>row.hex===palette.get(row.task)),'F81R3 task shade stays fixed when points change');
				 assert(report.labels.length>=5,'F81R3 fitting values remain shown');for(const label of report.labels)assert(label.contrast>=4.5,'F81R3 value contrast4.5 '+label.task+' '+label.contrast);
				 window.f81ShadeReport=report;
				 """)
				reports[theme]=_harness.driver.execute_script('return window.f81ShadeReport')
				print('F81R3 rendered '+theme+' '+json.dumps(reports[theme],sort_keys=True),flush=True)
				self.assertEqual([],_harness.errors())
		location=os.environ.get('RF_F81_SHADE_REPORT')
		if location:
			import tempfile
			target=Path(location).resolve();self.assertTrue(target.is_relative_to(Path(tempfile.gettempdir()).resolve()))
			target.write_text(json.dumps(reports,indent=2),encoding='utf-8')

	def test_f81_r4_newest_earning_run_stays_open_after_zero_run(self):
		_harness.viewport(1131,643);self.open();self.js("""
		 const fixture=await(await fetch('./mock/history/week.json')).json(),clone=id=>structuredClone(fixture.methods.get_run.find(answer=>answer.data?.run_id===id).data);
		 const zero=clone('week-app'),earning=clone('week-app'),older=clone('week-today'),check=clone('week-today-check');
		 for(const [run,id,start] of [[zero,'r4-zero','2026-10-21T15:57:00Z'],[earning,'r4-earning','2026-10-21T11:58:00Z'],[older,'r4-older','2026-10-21T07:56:00Z'],[check,'r4-check','2026-10-21T05:00:00Z']]){Object.assign(run,{run_id:id,started_at:start,ended_at:start,duration_ms:0,log_size:0});}
		 zero.points_earned=0;zero.accounts[0].points_earned=0;zero.accounts[0].tasks.forEach(task=>{task.points=0});
		 const day=[zero,earning,older,check],summary=run=>{const {warnings,accounts,...value}=structuredClone(run);return{...value,accounts:accounts.map(({tasks,state_before,state_after,...account})=>({...account,task_counts:Object.fromEntries(['ok','skip','fail','off'].map(status=>[status,tasks.filter(task=>task.status===status).length]))}))}};
		 window.pywebview.api.get_history=async()=>({ok:true,data:{runs:day.map(summary),next_before:null}});window.pywebview.api.get_run=async({run_id})=>({ok:true,data:structuredClone(day.find(run=>run.run_id===run_id))});
		 const refresh=async()=>{document.querySelector('.screen-header button').click();await wait(()=>document.querySelector('[data-run-id="r4-earning"]')&&document.querySelector('.history-screen').getAttribute('aria-busy')==='false');await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));};await refresh();
		 const blocks=[...document.querySelectorAll('.history-day-page>.history-run-block')],first=blocks[0],middle=blocks[1],last=blocks[2],shown=node=>Boolean(node&&node.getClientRects().length&&node.getBoundingClientRect().height>0);
		 assert(blocks.length===3&&blocks.map(block=>block.dataset.runId).join(',')==='r4-zero,r4-earning,r4-older','F81R4 day keeps chronological run order');
		 const tile=middle.querySelector('.history-task-list [data-task="misc_cards"]');assert(shown(tile)&&!tile.closest('details:not([open])')&&!middle.querySelector('.history-older-tasks'),'F81R4 newest earning tiles visible after zero-point run');
		 assert(tile.querySelector('.history-task-points').textContent===i.formatNumber(20)&&shown(middle.querySelector('.history-points-band')),'F81R4 newest earning band and measured20');
		 const folded=last.querySelector('.history-older-tasks');assert(folded&&!folded.open,'F81R4 older earning disclosure closed');assert(shown(folded.querySelector('summary>.history-points-band')),'F81R4 older earning band visible');assert(folded.querySelector('.history-task-list')?.closest('details:not([open])')===folded,'F81R4 older earning tiles inside closed disclosure');
		 assert(!first.querySelector('.history-task-list,.history-points-band,.history-older-tasks')&&first.querySelector('.history-no-points:not([open]) summary').textContent===i.t('history.tasks.no_points',{count:7}),'F81R4 zero-point run keeps only no-points disclosure');
		 assert(!document.querySelector('.history-check-group').open&&!document.querySelector('[data-run-id="r4-check"]'),'F81R4 checks stay lazy and folded');
		 window.f81R4Report={order:blocks.map(block=>block.dataset.runId),points:day.slice(0,3).map(run=>run.points_earned),visibleTask:'misc_cards',visiblePoints:20,olderFolded:!folded.open,zeroNoPoints:7,overflow:document.documentElement.scrollWidth>innerWidth+1};assert(!window.f81R4Report.overflow,'F81R4 no horizontal overflow');
		 window.f81R4NoPositive=async()=>{for(const run of day.slice(0,3)){run.points_earned=0;run.accounts[0].points_earned=0;run.accounts[0].tasks.forEach(task=>{task.points=null;task.status='ok';task.reason_key=null});}await refresh();const newest=document.querySelector('[data-run-id="r4-zero"]');assert(!newest.querySelector('.history-older-tasks')&&shown(newest.querySelector('.history-task-list')),'F81R4 no-positive day keeps newest open');for(const id of ['r4-earning','r4-older'])assert(document.querySelector('[data-run-id="'+id+'"] .history-older-tasks:not([open])'),'F81R4 no-positive day keeps other runs folded');};
		 """);self.assertEqual([],_harness.errors())
		self.assertTrue(_harness.driver.find_element('css selector','[data-run-id="r4-earning"] .history-task-list').is_displayed())
		self.assertFalse(_harness.driver.find_element('css selector','[data-run-id="r4-older"] .history-task-list').is_displayed())
		location=os.environ.get('RF_F81_R4_CAPTURE')
		if location:
			import hashlib,tempfile
			folder=Path(location).resolve();self.assertTrue(folder.is_relative_to(Path(tempfile.gettempdir()).resolve()));folder.mkdir(exist_ok=True)
			_harness.driver.execute_async_script("const done=arguments[0];document.fonts.ready.then(()=>requestAnimationFrame(()=>requestAnimationFrame(done)))")
			_harness.driver.execute_script("document.scrollingElement.scrollTop=0;document.querySelector('.app-main')?.scrollTo(0,0)")
			paths=('css/screens/history.css','js/screens/history-points.js','js/screens/history.js')
			source={path:hashlib.sha256((UI_DIR/path).read_bytes()).hexdigest() for path in paths};source['app/tests/ui/test_history.py']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
			image=folder/'newest-earning-en-dark-1131x643.png';png=_harness.driver.get_screenshot_as_png();image.write_bytes(png)
			report={'source':source,'path':str(image),'sha256':hashlib.sha256(png).hexdigest(),'metrics':_harness.driver.execute_script('return window.f81R4Report')}
			(folder/'capture.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print('F81R4 capture '+json.dumps(report,sort_keys=True),flush=True)
		self.js("await window.f81R4NoPositive();delete window.f81R4NoPositive;");self.assertEqual([],_harness.errors())

	def test_f81_r3_label_fit_both_directions_and_resize(self):
		for theme in ('dark','light'):
			for lang in ('en','ar'):
				with self.subTest(theme=theme,lang=lang):
					_harness.viewport(1131,643);self.open('#/history/synthetic-full','full',theme,lang)
					self.js("""
					 const fixture=await(await fetch('./mock/history/full.json')).json(),run=fixture.methods.get_run.data,store=await import('./js/store.js'),tasks=run.accounts[0].tasks;
					 for(const task of tasks)task.points=0;tasks.find(task=>task.task==='daily_set').points=1;tasks.find(task=>task.task==='visual_search').points=99;tasks.find(task=>task.task==='quests').points=10;tasks.find(task=>task.task==='searches').points=60;store.set('live',{run,log:null,last:null});
					 await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
					 const block=document.querySelector('[data-run-id="synthetic-full"]'),band=block.querySelector('.history-points-band'),small=band.querySelector('text[data-task="daily_set"]');
					 assert(small&&getComputedStyle(small).visibility==='hidden','F81R3 tiny adjacent segment drops label');assert(block.querySelector('.history-task-list [data-task="daily_set"] .history-task-points').textContent===i.formatNumber(1),'F81R3 omitted value still in tile');
					 const check=()=>{const shown=[...band.querySelectorAll('text')].filter(label=>getComputedStyle(label).visibility==='visible');assert(shown.length>=2,'F81R3 large segments keep values');const bandBox=band.getBoundingClientRect(),boxes=[];for(const label of shown){const box=label.getBoundingClientRect(),segment=band.querySelector('rect[data-task="'+label.dataset.task+'"]').getBoundingClientRect();assert(box.left>=segment.left+5&&box.right<=segment.right-5,'F81R3 label stays in own segment');assert(box.left>=bandBox.left&&box.right<=bandBox.right&&box.bottom<=bandBox.bottom+1&&box.top>=segment.bottom,'F81R3 label below band without clipping');boxes.push(box);}boxes.sort((a,b)=>a.left-b.left);for(let n=1;n<boxes.length;n++)assert(boxes[n].left-boxes[n-1].right>=10,'F81R3 no label touches neighbour');};check();
					 band.style.inlineSize='220px';await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));assert(Math.abs(band.getBoundingClientRect().width-220)<1,'F81R3 resize reaches actual band');check();band.style.inlineSize='100%';await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));check();
					 """);self.assertEqual([],_harness.errors())

	def test_f81_r3_closed_disclosure_heights(self):
		for theme in ('dark','light'):
			with self.subTest(theme=theme):
				_harness.viewport(1131,643);self.open('#/history/week-failure',theme=theme)
				self.js("""
				 const block=document.querySelector('[data-run-id="week-failure"]'),nodes=[block.querySelector('.history-no-points>summary'),block.querySelector('.history-technical>summary'),block.querySelector('section.log-panel>.log-toggle')];
				 assert(nodes.every(Boolean),'F81R3 three real closed controls');assert(!nodes[0].parentElement.open&&!nodes[1].parentElement.open&&nodes[2].getAttribute('aria-expanded')==='false','F81R3 rows measured closed');
				 const rects=nodes.map(node=>node.getBoundingClientRect());assert(Math.max(...rects.map(rect=>rect.height))-Math.min(...rects.map(rect=>rect.height))<=1,'F81R3 equal closed row heights');assert(Math.abs((rects[1].top-rects[0].top)-(rects[2].top-rects[1].top))<=1,'F81R3 equal closed row cadence');
				 console.log('F81R3 disclosure heights '+JSON.stringify(rects.map(rect=>({height:rect.height,top:rect.top}))));
				 """);self.assertEqual([],_harness.errors())

	def test_f75_empty_missing_error(self):
		_harness.viewport(1131,643)
		for route,fixture,selector in (("#/history","empty",".empty-state"),("#/history/synthetic-missing","missing",".banner"),("#/history/synthetic-failed","failed",".banner")):
			self.open(route,fixture);self.assertIsNotNone(_harness.driver.find_element("css selector",selector));self.assertEqual([],_harness.errors())
		self.js("assert(!document.querySelector('.history-task-points,.history-run-summary>strong'),'F75 missing points omitted not zero');assert(!document.querySelector('.history-screen').textContent.includes(i.t('common.value.unknown')),'F75 no unavailable failed run');assert(document.querySelector('.history-deferred').textContent===i.t('history.deferred.gave_up'),'F64 actual full-screen delay remains accurate')")

	def test_f75_technical_log_kept_collapsed(self):
		_harness.viewport(1131,643)
		self.open("#/history/synthetic-partial","detail");self.js("assert(!document.querySelector('.history-technical').open,'F75 technical closed');assert(document.querySelector('section.log-panel'),'F75 actual log reachable');assert(document.querySelector('.log-text').hidden,'F75 log not loaded until requested');document.querySelector('.history-technical').open=true;assert(document.querySelector('.history-technical pre').textContent.includes('Synthetic'),'F75 retained diagnostic')");self.assertEqual([],_harness.errors())
		self.js("const technical=document.querySelector('.history-technical'),toggle=technical.querySelector('summary');assert(technical.classList.contains('log-panel')&&toggle.classList.contains('log-toggle')&&toggle.querySelector('.icon'),'G2 shared closed-disclosure style');assert(getComputedStyle(toggle).listStyleType==='none','G2 no default triangle');assert(getComputedStyle(toggle).padding===getComputedStyle(document.querySelector('section.log-panel .log-toggle')).padding,'G2 same disclosure padding');")
		self.js("""
		 const offsets=[],original=window.pywebview.api.get_run_log;
		 window.pywebview.api.get_run_log=async request=>{offsets.push(request.offset);return original(request)};
		 const panel=document.querySelector('section.log-panel');panel.querySelector('.log-toggle').click();await wait(()=>panel.querySelector('pre').textContent.includes('page 1'));
		 panel.querySelector('.log-more').click();await wait(()=>panel.querySelector('pre').textContent.includes('page 2'));
		 panel.querySelector('.log-more').click();await wait(()=>panel.querySelector('pre').textContent.includes('page 3'));
		 assert(JSON.stringify(offsets)===JSON.stringify([0,876,1777]),'F75 byte log pages exactly once');
		 assert(panel.querySelector('.log-more').hidden&&panel.dir==='ltr'&&panel.querySelector('pre').dir==='ltr','F75 EOF and log direction');
		 const store=await import('./js/store.js'),answer=await window.pywebview.api.get_run({run_id:'synthetic-partial'});
		 store.set('live',{run:{...answer.data,points_earned:75,deferred:{state:3,waited_s:61,gave_up:false}},log:null,last:null});
		 assert(document.querySelector('section.log-panel')===panel,'F75 live refresh retains log instance');
		 assert(document.querySelector('.history-run-summary>strong').textContent.includes('75'),'F75 canonical live result refreshed');
		 assert(document.querySelector('.history-deferred').textContent===i.t('history.deferred.waited',{count:2}),'F64 measured full-screen wait plural');
		 const text=panel.querySelector('pre').textContent;r.navigate('#/settings/app');await wait(()=>document.querySelector('[data-screen="settings"]'));
		 window.rf.receive([{topic:'run.log',data:{run_id:'synthetic-partial',text:'synthetic after unmount'}}]);await pause(50);
		 assert(panel.querySelector('pre').textContent===text,'F75 log listener disposed on unmount');
		 """);self.assertEqual([],_harness.errors())

	def test_f81_failure_zero_and_wait_truth(self):
		_harness.viewport(1131,643);self.open('#/history/week-old');self.js("const block=document.querySelector('[data-run-id=\"week-old\"]');assert(block.querySelector('.banner')&&!block.querySelector('.history-points-band,.history-run-points,.history-task-points'),'F81 failed cause withoutpoints orband');")
		self.open('#/history/week-zero');self.js("const block=document.querySelector('[data-run-id=\"week-zero\"]');assert(!block.querySelector('.history-points-band'),'F81 nozeroorunknownfillerband');assert(block.querySelector('[data-task=\"daily_set\"] .history-task-points').textContent===i.formatNumber(0),'F81 zeroknown');assert(!block.querySelector('[data-task=\"visual_search\"] .history-task-points'),'F81 unknownnonumber');assert(block.querySelector('.history-idle [data-status=\"skip\"]'),'F81 skipretained');")
		self.open('#/history/week-wait');self.js("assert(document.querySelector('[data-run-id=\"week-wait\"] .history-run-marks').textContent.includes(i.t('history.mark.waited',{minutes:10})),'F81 waitedminutes');assert(document.querySelector('.history-deferred').textContent===i.t('history.deferred.waited',{count:10}),'F81 existingwaitnotice');")
		self.open('#/history/week-app');self.js("assert(document.querySelector('.history-app-run').textContent===i.t('history.mark.app_run'),'F81 Apprunexception');")
		self.assertEqual([],_harness.errors())

	def test_f81_locale_layout_matrix(self):
		from ui.test_locales import REGIONS,locales
		count=0
		for lang,direction in [("en","ltr")]+locales():
			for width,height in ((1000,700),(1131,643),(1440,900),(1920,1000),(860,600)):
				for theme in ("dark","light"):
					with self.subTest(lang=lang,width=width,theme=theme):
						_harness.viewport(width,height)
						for route in ('#/history','#/history/week-today'):
							self.open(route,'week',theme,lang,REGIONS.get(lang,'en-AT'));self.js("""
							 const root=document.querySelector('.history-screen'),page=document.querySelector('.history-day-page'),rail=document.querySelector('.history-date-rail');assert(document.documentElement.scrollWidth<=innerWidth+1&&root.scrollWidth<=root.clientWidth+1,'F81 nohorizoverflow');
							 assert(!root.textContent.includes('⟦')&&!root.textContent.includes('history.mark.'),'F81 nokeyleaks');
							 const shown=n=>n.getBoundingClientRect().height>0;if(innerWidth<=1000)assert(shown(page)!==shown(rail),'F81 narrowexclusivepage');else{assert(shown(page)&&shown(rail),'F81 widebothpanes');const a=rail.getBoundingClientRect(),b=page.getBoundingClientRect();assert(document.documentElement.dir==='rtl'?a.left>b.right:a.right<b.left,'F81 logicalRTLpanes');}
							 for(const summary of root.querySelectorAll('summary')){assert(summary.querySelectorAll(':scope>.icon').length===1,'F81 singlechevron');assert(getComputedStyle(summary).listStyleType==='none','F81 nonativetriangle');}
							 const heading=root.querySelector('.history-day-title');if(heading)assert(getComputedStyle(heading).fontSize==='20px','F81 sectionscale');
							 await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));for(const band of root.querySelectorAll('.history-points-band'))for(const label of band.querySelectorAll('text'))if(getComputedStyle(label).visibility==='visible'){const box=label.getBoundingClientRect(),segment=band.querySelector('rect[data-task="'+label.dataset.task+'"]').getBoundingClientRect();assert(box.left>=segment.left+5&&box.right<=segment.right-5&&box.top>=segment.bottom,'F81R3 matrix label inside own segment and below band');}
							 """);self.assertEqual([],_harness.errors());count+=1
		print(f'F81 History locale/layout matrix {count}screens',flush=True)

	def test_f81_capture(self):
		location=os.environ.get('RF_F81_CAPTURE')
		if not location:return
		import importlib.util,tempfile
		root=Path(location).resolve();self.assertTrue(root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
		spec=importlib.util.spec_from_file_location('f81_capture',root/'capture.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.run(_harness,root)
