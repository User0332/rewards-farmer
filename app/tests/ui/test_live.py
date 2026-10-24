import _bootstrap  # noqa: F401

import json
import os
import unittest
from pathlib import Path

from ui.harness import UI_DIR, UiHarness, _Handler


_harness = None
_original_get = None
_enabled = os.environ.get("REWARDS_APP_UI_TESTS") == "1"
FIXTURES = sorted(path.stem for path in (UI_DIR / "mock/live").glob("*.json"))
TASKS = "daily_set explore_on_bing visual_search misc_cards quests searches bonus".split()


def setUpModule():
	global _harness, _original_get
	if _enabled:
		if os.environ.get('RF_LIVE_STATE_NOTICE_MUTATION'):
			if os.environ['RF_LIVE_STATE_NOTICE_MUTATION']!='check-gate':raise ValueError('Unknown Live state-notice test mutation')
			source=(UI_DIR/'js/screens/live.js').read_text(encoding='utf-8')
			old='children.push(checkOnly ? decisionNotice(account) : null, errorNotice(account), stateNotice(state, account));'
			if source.count(old)!=1:raise AssertionError('Live state mutation must target one actual account plan')
			payload=source.replace(old,old.replace('stateNotice(state, account)','checkOnly ? null : stateNotice(state, account)')).encode('utf-8')
			_original_get=_Handler.do_GET
			def mutated_get(handler):
				from urllib.parse import urlsplit
				if urlsplit(handler.path).path=='/js/screens/live.js':
					handler.send_response(200);handler.send_header('Content-Type','text/javascript');handler.send_header('Content-Length',str(len(payload)));handler.end_headers();handler.wfile.write(payload)
				else:_original_get(handler)
			_Handler.do_GET=mutated_get
		_harness = UiHarness()
		try:
			_harness.start().wait_ready()
		except BaseException:
			_harness.stop()
			raise


def tearDownModule():
	if _harness is not None:
		_harness.stop()
	if _original_get is not None:_Handler.do_GET=_original_get


class LiveSourceTests(unittest.TestCase):
	def test_synthetic_fixtures_lifecycle_and_catalog(self):
		self.assertGreaterEqual(len(FIXTURES), 8)
		for fixture in FIXTURES:
			with self.subTest(fixture=fixture):
				data = json.loads((UI_DIR / "mock/live" / (fixture + ".json")).read_text(encoding="utf-8"))
				self.assertIs(data["synthetic"], True)
				self.assertTrue(data["note"])
				for method in ("get_live", "get_run"):
					response = data["methods"].get(method)
					if response and response["ok"]:
						run = response["data"].get("run") if method == "get_live" else response["data"]
						if run:
							for account in run["accounts"]:
								self.assertEqual(TASKS, [task["task"] for task in account["tasks"]])
								for task in account["tasks"]:
									self.assertIn(task["status"], ("pending", "running", "ok", "skip", "fail", "off", "not_run"))
						if method == "get_live" and response["data"]["log"]:
							log = response["data"]["log"]
							self.assertGreaterEqual(log["next_offset"], len(log["text"].encode("utf-8")))
				for push in data["pushes"]:
					self.assertGreaterEqual(push["after_ms"], 0)
					if push["topic"] == "run.log":
						self.assertEqual(push["data"]["next_offset"] - push["data"]["offset"], len(push["data"]["text"].encode("utf-8")))
		progress = json.loads((UI_DIR / "mock/live/progress.json").read_text(encoding="utf-8"))
		cli = json.loads((UI_DIR / "mock/live/cli.json").read_text(encoding="utf-8"))
		self.assertEqual("cli", cli["methods"]["get_live"]["data"]["run"]["trigger"])
		self.assertTrue(cli["methods"]["get_live"]["data"]["run"]["active"])
		self.assertTrue({"run.started", "run.snapshot", "run.log", "run.finished"}.issubset({push["topic"] for push in progress["pushes"]}))
		catalog = json.loads((UI_DIR / "i18n/en/live.json").read_text(encoding="utf-8"))
		self.assertEqual("live", catalog["namespace"])
		for key, entry in catalog["messages"].items():
			self.assertTrue(entry["note"].strip(), key)
			self.assertNotRegex(json.dumps(entry, ensure_ascii=False), "[\u2013\u2014]")
		code = (UI_DIR / "js/screens/live.js").read_text(encoding="utf-8")
		self.assertNotIn("innerHTML", code)
		self.assertNotIn(".style", code)
		self.assertNotIn('on("run.finished"', code)
		self.assertNotIn('c.progressRing.create',code)
		self.assertNotIn('common.value.unknown',code)
		self.assertIn('Number.isFinite(run.points_earned)',code)


@unittest.skipUnless(_enabled, "Set REWARDS_APP_UI_TESTS=1 for guarded mock UI acceptance")
class LiveTests(unittest.TestCase):
	def setUp(self):
		_harness.viewport(1280, 800)

	def js(self, body):
		mutation=os.environ.get("RF_F76_UI_MUTATION")
		if mutation:
			self.assertIn(mutation,("ring","text","unknown"))
			_harness.driver.execute_script("""
			 const mutation=arguments[0],summary=document.querySelector('.live-summary');
			 if(mutation==='ring'){const ring=document.createElement('div');ring.className='progress-ring';ring.textContent='7 / 7';summary.append(ring)}
			 if(mutation==='text'){const text=document.createElement('p');text.className='synthetic-note';text.textContent='Synthetic extra explanation';document.querySelector('.screen-header').append(text)}
			 if(mutation==='unknown'){const value=document.createElement('span');value.className='stat-value';value.textContent='Not available';summary.append(value)}
			 """,mutation)
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

	def wait_final(self):
		from selenium.webdriver.support.ui import WebDriverWait
		WebDriverWait(_harness.driver, 12, poll_frequency=0.1).until(lambda browser: browser.execute_script("return document.querySelector('.live-screen')?.dataset.active==='false'"))

	def assert_keyboard(self):
		expected = _harness.driver.execute_script("""
			return [...document.querySelectorAll('#view button,#view a[href],#view input,#view select,#view summary,#toasts button')]
				.filter(node=>!node.disabled&&node.tabIndex>=0&&node.getClientRects().length&&![...node.closest('details:not([open])')?.querySelectorAll(':scope > :not(summary)')||[]].some(child=>child.contains(node)))
				.map((node,index)=>{if(!node.id)node.id='live-tab-'+index;return node.id;});
		""")
		order = _harness.tab_order(len(expected) + 12)
		self.assertTrue(set(expected).issubset(order), {"expected": expected, "order": order})

	def _state_notice_cases(self,cases,lang,load_last=False):
		_harness.viewport(1131,643);_harness.open('#/live','live/idle-last',lang=lang,reduced_motion=True).wait_ready()
		from selenium.webdriver.support.ui import WebDriverWait
		WebDriverWait(_harness.driver,8).until(lambda driver:driver.execute_script("return document.querySelector('.live-screen')?.dataset.run==='synthetic-check'"))
		self.js('const cases='+json.dumps(cases)+',loadLast='+json.dumps(load_last)+';'+"""
		 const fixture=await(await fetch('./mock/live/idle-last.json')).json(),finished=await(await fetch('./mock/live/finished.json')).json();
		 const checkSeed=fixture.methods.get_run.data,runSeed=finished.methods.get_live.data.run,snapshot=checkSeed.accounts[0].state_before;
		 let current;const calls=[],wait=async predicate=>{for(let n=0;n<300;n++){if(predicate())return;await new Promise(resolve=>setTimeout(resolve,10))}throw Error('Live state notice last-check wait')};
		 const router=await import('./js/router.js');window.pywebview.api.get_history=async()=>({ok:true,data:{runs:[],next_before:null}});
		 window.pywebview.api.get_live=async()=>{calls.push('get_live');return{ok:true,data:{run:null,log:null}}};
		 window.pywebview.api.get_run=async({run_id})=>{calls.push('get_run');assert(run_id===current.run_id,'Live state notice last-run identity');return{ok:true,data:structuredClone(current)}};
		 window.liveStateNoticeReport=[];
		 for(const [index,test] of cases.entries()){
		  const {code,slot,kind}=test,isCheck=kind==='check';current=structuredClone(isCheck?checkSeed:runSeed);const account=current.accounts[0];
		  Object.assign(current,{run_id:'state-notice-'+index,kind,trigger:'scheduled',log_size:0});account.label='Alex (synthetic)';account.error=null;
		  const state=unreadable=>({...structuredClone(snapshot),unreadable});account.state_before=state(slot==='after'?'signed_out':code);account.state_after=slot==='after'?state(code):null;
		  if(isCheck){account.decision={run:false,reason:code?'unreadable':'below_threshold',reason_key:code?'errors.decision.unreadable':'errors.decision.below_threshold',open_points:code?null:10,min_points:30};account.tasks.forEach((task,n)=>{task.status=n===6?'off':'not_run';task.reason_key=n===6?'errors.task.turned_off':'errors.task.not_run'});}
		  if(loadLast){router.navigate('#/history');await wait(()=>document.getElementById('view').dataset.route==='#/history'&&document.querySelector('.history-screen')?.getAttribute('aria-busy')==='false');const start=calls.length;s.set('overview',{...s.get('overview'),last_run:{...current,accounts:[{...account,task_counts:{ok:0,skip:0,fail:0,off:1}}]}});s.set('live',{run:null,log:null,last:null});router.navigate('#/live');await wait(()=>s.get('live')?.run?.run_id===current.run_id&&document.querySelector('.live-account-plan'));assert(calls.slice(start).join(',')==='get_live,get_run','Live last check loaded through real refresh');}
		  else s.set('live',{run:current,log:null,last:null});
		  const plan=document.querySelector('.live-account-plan'),notices=[...plan.querySelectorAll('.banner.tone-warning .banner-message')],decision=plan.querySelector('.live-decision');
		  const whole=['signed_out','terms_of_use','page_blank'].includes(code),key=whole?'errors.state.'+code:'errors.state.sections',params=whole?{account:account.label}:{sections:code?i.formatList(code.split(',').map(section=>i.t('common.section.'+section))):''};
		  const expected=code?i.t(key,params):null,label='Live state notice '+String(code)+' '+slot+' '+kind+' '+document.documentElement.lang;
		  assert(notices.length===(code?1:0),label+' expected='+String(expected)+' actual='+JSON.stringify(notices.map(node=>node.textContent))+' decision='+String(decision?.textContent));
		  if(code){const notice=notices[0];assert(notice.textContent===expected&&notice.checkVisibility()&&!notice.closest('details:not([open])'),label+' exact visible notice');const rect=notice.getBoundingClientRect();assert(rect.left>=0&&rect.right<=innerWidth+1&&notice.scrollWidth<=notice.clientWidth+1,label+' notice fits');}
		  if(isCheck){const expectedDecision=i.t(account.decision.reason_key,{points:account.decision.open_points,min_points:i.formatNumber(30)});assert(decision?.textContent===expectedDecision&&decision.checkVisibility(),label+' decision kept');assert(!plan.querySelector('.live-task-list,.live-task-row'),label+' no task attempts invented');if(code)assert(decision.nextElementSibling===notices[0].closest('.banner')&&decision.textContent!==expected,label+' separate decision and recovery notice');}
		  else{assert(!decision&&plan.querySelectorAll('.live-task-row').length===7,label+' non-check task rows unchanged');}
		  assert(document.documentElement.scrollWidth<=innerWidth+1&&document.getElementById('view').scrollWidth<=document.getElementById('view').clientWidth+1,label+' no sideways scroll');
		  window.liveStateNoticeReport.push({...test,path:loadLast?'last-refresh':'store-run',language:document.documentElement.lang,notice:expected,decision:decision?.textContent||null,noHorizontalOverflow:true});
		 }
		 """)
		self.assertEqual([],_harness.errors())
		print('Live state notice cases '+json.dumps(_harness.driver.execute_script('return window.liveStateNoticeReport'),ensure_ascii=True),flush=True)

	def test_check_only_signed_out_state_notice(self):
		self._state_notice_cases([{'code':'signed_out','slot':'before','kind':'check'}],'en')

	def test_check_only_state_notices_and_noncheck_controls(self):
		cases=[{'code':code,'slot':slot,'kind':kind} for code in ('signed_out','terms_of_use','page_blank','daily_set,searches',None) for slot in ('before','after') for kind in ('check','run')]
		for lang in ('en','de'):
			for load_last in (False,True):
				with self.subTest(lang=lang,load_last=load_last):self._state_notice_cases(cases,lang,load_last)

	def test_f76_subtraction_and_known_values(self):
		_harness.open("#/live", "live/failed").wait_ready()
		self.js("""
		 assert(!document.querySelector('.progress-ring'),'F76 no duplicate completion ring');
		 assert(!document.querySelector('.synthetic-note'),'F76 no added explanatory caption');
		 assert(s.get('live').run.points_earned===null&&!document.querySelector('.live-summary .stat-value'),'F76 unknown value not shown');
		 assert(document.querySelectorAll('.live-task-row').length===7,'F76 task identities retained');
		 const details=document.querySelector('.live-more-details');assert(details&&!details.open&&details.querySelector('summary .icon'),'F76 one closed diagnostics style');
		 """)
		self.assertEqual([], _harness.errors())
	def test_r3_l1_finished_action_has_no_outcome_glyph(self):
		mutation = os.environ.get("RF_F76_R3_MUTATION") == "L1"
		for fixture in ("finished", "idle-last", "failed"):
			_harness.open("#/live", "live/" + fixture).wait_ready()
			if mutation:
				_harness.driver.execute_script("const glyph=document.createElement('span');glyph.className='live-outcome';glyph.textContent='-';document.querySelector('.live-progress').prepend(glyph)")
			self.js("""
			 assert(!document.querySelector('.live-outcome,.live-summary [role=img],.live-progress > .icon'),'R3 L1 no standalone outcome glyph');
			 assert(document.querySelector('#live-run-action')?.textContent===i.t('live.history'),'R3 L1 History action retained');
			 """)
			self.assertEqual([], _harness.errors())

	def test_a_scripted_snapshots_points_log_and_finish(self):
		_harness.open("#/live", "live/progress").wait_ready()
		self.js("""
			let liveCalls=0,detailCalls=0;
			const originalLive=window.pywebview.api.get_live,originalRun=window.pywebview.api.get_run;
			window.pywebview.api.get_live=async request=>{liveCalls++;return originalLive(request);};
			window.pywebview.api.get_run=async request=>{detailCalls++;return originalRun(request);};
			const seen=[],marks=[];
			const off=s.on('live',value=>{if(value.run){seen.push(value.run.points_earned);marks.push(document.querySelector('[data-task=daily_set]')?.dataset.status);}});
			while(!s.get('live')?.run)await new Promise(resolve=>setTimeout(resolve,40));
			document.querySelector('.log-toggle').click();
			window.liveLogNode=document.querySelector('.log-panel');
			while(s.get('live').run.active)await new Promise(resolve=>setTimeout(resolve,40));
			off();
			assert(seen.includes(0)&&seen.includes(35)&&seen.includes(90)&&seen.includes(150),'points count up from real scripted pushes '+seen);
			assert(marks.includes('running')&&marks.includes('ok'),'task checks off from snapshots');
			assert(detailCalls===1&&liveCalls===0,'F22 get_run on finish, no get_live');
			assert(s.get('live').last.result==='ok','finished summary retained');
			assert(document.querySelectorAll('.live-task-row[data-status=ok]').length===6,'final tasks');
			assert(document.querySelector('[data-task=visual_search]').dataset.status==='skip','final skip');
			assert(document.querySelector('.live-summary .stat-value').textContent==='150','final count');
			assert(document.querySelector('.log-panel')===window.liveLogNode,'one log panel across snapshots and finish');
			assert(document.querySelector('.log-toggle').getAttribute('aria-expanded')==='true','disclosure remains open');
			assert(document.querySelector('.log-text').textContent==='Synthetic log\\nDaily set done\\nSearches done\\n','offset log append no duplicates');
			assert(document.querySelector('.log-panel').dir==='ltr','technical log ltr');
		""")
		self.assertEqual([], _harness.errors())

	def test_b_fixture_matrix(self):
		cases = 0
		for width, height in ((1280, 800), (860, 600)):
			_harness.viewport(width, height)
			for theme in ("dark", "light"):
				for lang in ("en", "x-long", "x-rtl"):
					for fixture in FIXTURES:
						with self.subTest(width=width, theme=theme, lang=lang, fixture=fixture):
							_harness.open("#/live", "live/" + fixture, theme, lang).wait_ready()
							if fixture == "progress":
								self.wait_final()
							self.js("""
								assert(document.title===i.t('live.title'),'F21 document title');
								assert(document.querySelector('.live-screen'),'real Live module');
								assert(!document.querySelector('#view [style]'),'no inline styles');
								const view=document.getElementById('view');assert(view.scrollWidth<=view.clientWidth+1&&document.documentElement.scrollWidth<=innerWidth+1,'no horizontal overflow');
								const run=s.get('live')?.run;if(run)assert(document.querySelectorAll('.live-task-row').length===run.accounts.filter(account=>!(run.kind==='check'&&account.decision?.run===false&&account.tasks.every(task=>['not_run','off'].includes(task.status)))).length*7,'task attempts keep seven rows; decision-only checks omit empty tasks');
								if(run?.active&&run.trigger==='cli')assert(!document.querySelector('#live-run-action')&&document.querySelector('.live-external-run'),'CLI has no Stop');
								if(run?.points_earned===null)assert(!document.querySelector('.live-summary .stat-value'),'unknown live count omitted');
								if(document.querySelector('.live-log-host .log-panel'))assert(document.querySelector('.live-log-host .log-panel').dir==='ltr','log stays ltr');
							""")
							if width == 1280:
								shot = _harness.screenshot(f"live-{fixture}-{theme}-{lang}")
								self.assertTrue(Path(shot).is_file())
							self.assert_keyboard()
							if fixture == "running":
								self.js("document.querySelector('.log-toggle').click();await new Promise(resolve=>setTimeout(resolve,0));assert(!document.querySelector('.log-text').hidden,'log can be opened');")
								self.assert_keyboard()
							self.assertEqual([], _harness.errors())
							cases += 1
		print(f"Live fixture matrix: {cases} cases, {len(FIXTURES) * 6} screenshots", flush=True)

	def test_c_stop_waiting_and_error_recovery(self):
		_harness.open("#/live", "live/running").wait_ready()
		self.js("""
			window.pywebview.api.stop_run=async request=>{assert(request.run_id==='synthetic-live','stop bound to run');return {ok:false,error:{code:'run.not_active',message_key:'errors.app.run_not_active',params:{},detail:null,fields:null}};};
			document.querySelector('#live-run-action').click();await new Promise(resolve=>setTimeout(resolve,0));
			assert(document.querySelector('#toasts').textContent.includes(i.t('errors.app.run_not_active')),'catalog error');
			assert(!document.querySelector('#live-run-action').disabled,'failed stop can recover');
			let resolve;window.pywebview.api.stop_run=async()=>new Promise(done=>{resolve=done;});
			document.querySelector('#live-run-action').click();
			assert(document.querySelector('#live-run-action').disabled,'waiting disabled');assert(document.querySelector('.live-stop-note'),'waiting explanation');
			resolve({ok:true,data:{stopping:true}});await new Promise(done=>setTimeout(done,0));
			assert(document.querySelector('#live-run-action').disabled&&s.get('live').run.active,'accepted stop still waiting');
			const final=structuredClone(s.get('live').run);final.active=false;final.result='stopped';final.result_key='errors.run.stopped';final.ended_at='2026-10-14T20:00:00Z';final.stop_requested=true;
			final.accounts[0].tasks=final.accounts[0].tasks.map(task=>['pending','running'].includes(task.status)?{...task,status:'not_run',reason_key:'errors.task.stopped'}:task);
			window.pywebview.api.get_run=async()=>({ok:true,data:final});window.rf.receive([{topic:'run.finished',data:{...final,accounts:[]}}]);await new Promise(done=>setTimeout(done,0));
			assert(!document.querySelector('.live-outcome')&&document.querySelector('#live-run-action').textContent===i.t('live.history'),'R3 finished stop keeps History without stray glyph');assert(!document.querySelector('.live-stop-note'),'waiting only while active');
		""")
		self.assertEqual([], _harness.errors())

	def test_d_idle_last_run_and_unmount(self):
		_harness.open("#/live", "live/first-run").wait_ready()
		self.js("assert(document.querySelector('.empty-state').textContent.includes(i.t('live.idle.title')),'idle teaches next step');")
		_harness.open("#/live", "live/idle-last").wait_ready()
		self.js("""
			assert(s.get('live').run.run_id==='synthetic-check'&&!s.get('live').run.active,'idle loads last run');
			assert(document.querySelector('.live-decision').textContent.includes('fewer than'),'skip decision not a failure');
			assert(!document.querySelector('.live-task-row'),'skipped check has no empty task card');
			assert(!document.querySelector('.task-reason'),'skipped check never says ended early');
			r.navigate('#/overview');await new Promise(resolve=>setTimeout(resolve,80));
			const root=document.querySelector('#view').firstElementChild;s.set('live',{...s.get('live'),run:null});assert(document.querySelector('#view').firstElementChild===root,'unmounted Live cannot render');
		""")
		self.assertEqual([], _harness.errors())

	def test_f_review_failure_hierarchy_and_cli_admission(self):
		_harness.open("#/live", "live/cli").wait_ready()
		self.js("""
			assert(s.get('live').run.trigger==='cli'&&s.get('live').run.active,'real CLI fixture consumer');
			assert(document.querySelectorAll('.live-task-row').length===7&&document.querySelector('.stat-value').textContent==='35','CLI progress still visible');
			assert(!document.querySelector('#live-run-action'),'CLI Stop absent');
			assert(document.querySelector('.live-external-run').textContent===i.t('errors.app.run_not_stoppable'),'CLI explanation');
			let calls=0;window.pywebview.api.stop_run=async()=>{calls++;return {ok:true,data:{stopping:true}};};
			assert(calls===0,'CLI no request');
		""")
		_harness.open("#/live", "live/failed").wait_ready()
		self.js("""
			const content=document.querySelector('.live-content'),primary=document.querySelector('.live-primary-error');
			assert(content.firstElementChild===primary&&primary.textContent.includes(i.t('errors.browser.edge_missing')),'account primary error leads');
			assert([...content.querySelectorAll('.banner')].filter(node=>node.checkVisibility()).length===1,'only primary error initially visible');
			const details=document.querySelector('.live-more-details');assert(details&&!details.open&&details.querySelector('summary').textContent===i.t('live.more_details'),'single folded diagnostics');
			assert(!document.querySelector('.live-account-plan .banner'),'no repeated banners in account plan');
			assert(!document.querySelector('.live-summary .stat-value'),'unknown value omitted');
			details.open=true;assert(details.querySelectorAll('.banner').length>=2,'secondary errors retained not discarded');
		""")
		self.assert_keyboard()
		self.assertTrue(Path(_harness.screenshot("live-failed-details-dark-en")).is_file())
		_harness.open("#/live", "live/first-run").wait_ready()
		self.js("""
			assert(!document.querySelector('.live-screen .synthetic-note,.progress-ring'),'F76 no preview caption or completion ring');
			assert(document.querySelector('.screen-header h1')&&!document.querySelector('.screen-header p:not([hidden])'),'F76 idle heading not repeated');
		""")
		self.assertEqual([], _harness.errors())

	def test_e_push_unsubscribe_and_mount_fetch_race(self):
		_harness.open("#/live", "live/running").wait_ready()
		self.js("""
			const module=await import('./js/screens/live.js');
			const calls=[],listeners=new Map();let resolveLive;
			const sourceRun=structuredClone(s.get('live').run);
			const values=new Map([['live',{run:sourceRun,log:{text:'',next_offset:0},last:null}],['overview',null]]);
			const localStore={get:key=>values.get(key),set:(key,value)=>{values.set(key,value);for(const fn of [...(listeners.get(key)||[])])fn(value);},on:(key,fn)=>{if(!listeners.has(key))listeners.set(key,new Set());listeners.get(key).add(fn);return()=>listeners.get(key).delete(fn);}};
			const c=await import('./js/components/index.js');
			const host=document.createElement('div');document.querySelector('#view').append(host);
			const localBridge={call:(method,request)=>{calls.push(method);if(method==='get_live')return new Promise(resolve=>{resolveLive=resolve;});throw Error('unexpected '+method);}};
			const instance=module.mount(host,{bridge:localBridge,i18n:i,store:localStore,router:r,components:c});
			const newer={...sourceRun,points_earned:100};localStore.set('live',{run:newer,log:{text:'',next_offset:0},last:null});resolveLive({run:sourceRun,log:{text:'old',next_offset:3}});await new Promise(resolve=>setTimeout(resolve,0));
			assert(localStore.get('live').run===newer,'mount response cannot overwrite newer push');
			assert(host.querySelector('.stat-value').textContent==='100','newer points retained');
			instance.unmount();assert([...listeners.values()].every(set=>set.size===0),'all screen listeners removed');
			localStore.set('live',{run:{...newer,points_earned:200},log:null,last:null});assert(host.querySelector('.stat-value').textContent==='100','unmounted screen quiet');host.remove();
		""")
		self.assertEqual([], _harness.errors())
