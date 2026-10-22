import _bootstrap  # noqa: F401

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.harness import SCREENSHOT_DIR, UI_DIR, UiHarness
from rfapp import signin


_enabled = os.environ.get("REWARDS_APP_UI_TESTS") == "1"
_harness = None
SIGNIN = {"starting": "starting", "waiting": "waiting", "verifying": "verifying", "signed-in": "signed_in", "failed": "failed", "driver-missing": "failed", "driver-mismatch": "failed", "picker-cancel": "failed", "edge-save-error": "failed", "timed-out": "timed_out", "cancelled": "cancelled", "busy": "busy", "terms": "signed_in", "progress": "signed_in"}
STEP_TWO = {"automatic", "fixed", "manual", "legacy-task", "schedule-error", "replace-error"}
STEP_THREE = {"done", "finish-error", "first-run-error", "review"}
FIXTURES = sorted(path.stem for path in (UI_DIR / "mock/setup").glob("*.json"))


def setUpModule():
	global _harness
	if _enabled:
		_harness = UiHarness()
		try:
			_harness.start().wait_ready()
		except BaseException:
			_harness.stop()
			raise


def tearDownModule():
	if _harness is not None:
		_harness.stop()


class SourceTests(unittest.TestCase):
	def test_catalog_fixtures_and_boundaries(self):
		catalog = json.loads((UI_DIR / "i18n/en/setup.json").read_text(encoding="utf-8"))
		self.assertEqual("setup", catalog["namespace"])
		for key, entry in catalog["messages"].items():
			self.assertTrue(entry["note"].strip(), key)
			self.assertNotRegex(json.dumps(entry, ensure_ascii=False), "[\u2013\u2014]")
		self.assertEqual(30, len(FIXTURES))
		states = set()
		for fixture in FIXTURES:
			data = json.loads((UI_DIR / "mock/setup" / (fixture + ".json")).read_text(encoding="utf-8"))
			self.assertTrue(data["synthetic"])
			self.assertIsInstance(data["methods"], dict)
			for push in data["pushes"]:
				if push["topic"] == "signin.status":
					states.add(push["data"]["state"])
		self.assertEqual({"starting", "waiting", "verifying", "signed_in", "failed", "timed_out", "cancelled", "busy"}, states)
		css = (UI_DIR / "css/screens/setup.css").read_text(encoding="utf-8")
		self.assertNotRegex(css, r"(?m)(?:^|[;{])\s*(?:width|height|left|right|top|bottom|margin-left|padding-right)\s*:")
		for path in [UI_DIR / "js/screens/setup.js", UI_DIR / "js/screens/accounts/signin.js"]:
			text = path.read_text(encoding="utf-8")
			self.assertNotRegex(text, "[\u2013\u2014]")
			self.assertNotIn(".style", text)
			self.assertNotIn('el("input"', text)
		for fixture in FIXTURES:
			data = json.loads((UI_DIR / "mock/setup" / (fixture + ".json")).read_text(encoding="utf-8"))
			for method in ("get_settings", "save_settings"):
				answers = data["methods"].get(method, [])
				for answer in answers if isinstance(answers, list) else [answers]:
					if answer.get("ok"):
						self.assertEqual({"run.openrouter_key": False, "notify.webhook_url": False}, answer["data"]["secrets_set"], (fixture, method))
		answers = json.loads((UI_DIR / "mock/setup/edge-save-error.json").read_text(encoding="utf-8"))["methods"]["save_settings"]
		self.assertEqual("file_missing", answers[-1]["error"]["fields"]["advanced.edge_path"])
		self.assertEqual("file_wrong_name", answers[0]["error"]["fields"]["advanced.edge_path"])
		source = (UI_DIR / "js/screens/setup.js").read_text(encoding="utf-8")
		for identity in ("setup-signin", "setup-cancel-signin", "setup-retry-signin", "setup-continue", "setup-back", "setup-finish", "setup-first-run", "setup-open-overview", "setup-autostart", "setup-retry-run", "setup-export-diagnostics", "setup-import", "setup-replace-legacy", "setup-keep-legacy"):
			self.assertIn('"' + identity + '"', source, identity)
		self.assertNotIn('save_settings', source[source.index("function renderDone()"):source.index("function render(focusId)")])
		self.assertEqual("checks", catalog["messages"]["setup.summary.automatic"]["plural"])
		self.assertEqual({"one", "other"}, set(catalog["messages"]["setup.summary.automatic"]["forms"]))
		self.assertEqual("Every day", catalog["messages"]["setup.summary.every_day"]["text"])
		self.assertEqual("Fixed time, at {time}", catalog["messages"]["setup.summary.fixed"]["text"])


@unittest.skipUnless(_enabled, "Set REWARDS_APP_UI_TESTS=1 for isolated setup UI tests")
class SetupTests(unittest.TestCase):
	def test_r3_l6_review_keeps_account_label(self):
		self.open("review", 1)
		self.js("""
		 const accounts=s.get('accounts').map(a=>({...a,label:'Account 1',name:'Microsoft Display Name'}));
		 s.set('accounts',accounts);window.pywebview.api.list_accounts=async()=>({ok:true,data:{accounts}});
		 await r.navigate('#/overview');await wait(()=>document.querySelector('.overview-screen'));
		 await r.navigate('#/setup/1');await wait(()=>document.querySelector('.setup-content .account-row-label'));
		 assert(document.querySelector('.setup-content .account-row-label').textContent==='Account 1','R3 L6 step1 uses label');
		 await r.navigate('#/setup/3');await wait(()=>document.querySelector('[data-summary=account] .account-row-label'));
		 """)
		if os.environ.get("RF_F76_R3_MUTATION") == "L6":
			_harness.driver.execute_script("document.querySelector('[data-summary=account] .account-row-label').textContent='Microsoft Display Name'")
		self.js("""
		 assert(document.querySelector('[data-summary=account] .account-row-label').textContent==='Account 1','R3 L6 review keeps account label');
		 assert(document.querySelector('.nav-account .account-label').textContent==='Account 1','R3 L6 same navigation label');
		 await r.navigate('#/accounts');await wait(()=>document.querySelector('.accounts-entry .account-row-label'));
		 assert(document.querySelector('.accounts-entry .account-row-label').textContent==='Account 1','R3 L6 same Accounts label');
		 """)
		self.assertEqual([], _harness.errors())

	def test_f76_progress_and_days_disclosure_are_compact(self):
		self.open("automatic", 2)
		self.js("""
		 assert(!document.querySelector('.setup-preview,.setup-progress,.setup-background h3'),'F76 duplicated framing removed');
		 assert(document.querySelectorAll('.setup-steps li').length===3&&document.querySelectorAll('.setup-steps [aria-current=step]').length===1,'F76 accessible three-step navigation retained');
		 const days=document.querySelector('.setup-days'),summary=days.querySelector('summary');assert(!days.open&&summary.classList.contains('log-toggle')&&summary.querySelector('.icon'),'F76 consistent closed disclosure');
		 summary.click();assert(days.open&&document.querySelectorAll('#setup-days button').length===7,'F76 every day control remains');
		 assert(document.querySelector('.risk-notice')&&document.getElementById('setup-autostart')&&document.getElementById('setup-back')&&document.getElementById('setup-continue'),'F76 risk and behavior controls retained');
		 """)
		from selenium.webdriver.common.keys import Keys
		_harness.driver.execute_script("const details=document.querySelector('.setup-days');details.open=false;details.querySelector('summary').focus()")
		_harness.driver.switch_to.active_element.send_keys(Keys.ENTER)
		self.assertTrue(_harness.driver.execute_script("return document.querySelector('.setup-days').open"), "F76 Enter opens Days")
		_harness.driver.switch_to.active_element.send_keys(Keys.TAB)
		self.js("""
		 const locale=new Intl.Locale(s.get('appInfo')?.format_locale||'en-US');
		 const firstDay=locale.weekInfo?.firstDay||locale.getWeekInfo?.().firstDay||1;
		 const expected=['mon','tue','wed','thu','fri','sat','sun'][firstDay-1];
		 assert(document.activeElement.matches('#setup-days button[data-day]')&&document.activeElement.dataset.day===expected,'F76 Tab reaches the first day in regional order');
		 """)
		_harness.driver.execute_script("document.querySelector('.setup-days summary').focus()")
		_harness.driver.switch_to.active_element.send_keys(Keys.SPACE)
		self.assertFalse(_harness.driver.execute_script("return document.querySelector('.setup-days').open"), "F76 Space closes Days")
		self.assertEqual([], _harness.errors())

	def js(self, body):
		result = _harness.driver.execute_async_script("""
			const done=arguments[arguments.length-1];
			(async()=>{
				const assert=(ok,label)=>{if(!ok)throw Error(label)};
				const wait=async test=>{for(let n=0;n<100;n++){if(test())return;await new Promise(r=>setTimeout(r,10))}throw Error('condition timeout')};
				const c=await import('./js/components/index.js');
				const i=await import('./js/i18n.js');
				const s=await import('./js/store.js');
				const r=await import('./js/router.js');
			""" + body + """
				return {ok:true};
			})().then(done).catch(e=>done({ok:false,error:String(e),stack:e.stack}));
		""")
		self.assertTrue(result["ok"], result)

	def open(self, fixture, step=1, theme="dark", lang="en"):
		url = _harness.url(f"#/setup/{step}", "setup/" + fixture, theme, lang)
		same_url = _harness.driver.current_url == url
		_harness.open(f"#/setup/{step}", "setup/" + fixture, theme, lang).wait_ready()
		if same_url:
			print("setup same-URL fixture before explicit refresh: " + str(_harness.driver.execute_script("return {keepChoice:!document.getElementById('setup-keep-legacy'),replaceChoice:!!document.getElementById('setup-replace-legacy')}")), flush=True)
			# A navigation to the same fragment can keep the mounted screen's choices.
			_harness.driver.refresh()
			_harness.wait_ready()
		if fixture in SIGNIN:
			from selenium.webdriver.support.ui import WebDriverWait
			WebDriverWait(_harness.driver, 5).until(lambda driver: driver.execute_script("return document.querySelector('[data-signin-state]')?.dataset.signinState") == SIGNIN[fixture])

	def test_start_cancel_progress_and_subscription_cleanup(self):
		self.open("signin")
		self.js("""
			const calls=[];
			for(const method of ['add_account','cancel_signin']){const old=window.pywebview.api[method];window.pywebview.api[method]=async req=>{calls.push([method,req]);return old(req)}}
			document.getElementById('setup-signin').click();
			await wait(()=>document.querySelector('[data-signin-state]')?.dataset.signinState==='starting');
			assert(document.querySelector('.risk-notice'),'risk remains starting');
			window.rf.receive([{topic:'signin.status',data:{account_id:'account-1',state:'waiting',error_code:null,message_key:'accounts.signin.waiting',detail:null,at:'2026-10-14T20:00:00Z'}}]);
			document.getElementById('setup-cancel-signin').click();await wait(()=>calls.some(([m])=>m==='cancel_signin'));
			assert(calls.find(([m])=>m==='cancel_signin')[1].account_id==='account-1','cancel identity');
			assert(document.querySelector('[data-signin-state]').dataset.signinState==='waiting','request not terminal');
			assert(document.querySelectorAll('.setup-status .banner').length===1 && document.querySelector('.setup-status .banner-message').textContent===i.t('accounts.signin.cancelling'),'F58 cancel message wins once');
			window.rf.receive([{topic:'signin.status',data:{account_id:'account-1',state:'cancelled',error_code:null,message_key:'accounts.signin.cancelled',detail:null,at:'2026-10-14T20:00:01Z'}}]);
			assert(document.getElementById('setup-retry-signin'),'cancel retry');
		""")
		self.assertEqual([], _harness.errors())

	def test_producer_statuses_have_one_banner_and_connected_identity(self):
		with tempfile.TemporaryDirectory(prefix="setup-status-proof-") as directory, patch.object(signin.paths, "runs_dir", return_value=Path(directory)):
			statuses = [signin._write_status("account-1", state) for state in ("starting", "waiting", "verifying", "signed_in", "failed", "timed_out", "cancelled", "busy")]
			statuses.extend(signin._write_status("account-1", state, code) for state, code in (("failed", "edge_missing"), ("failed", "crashed"), ("signed_in", "terms_of_use")))
		_harness.viewport(1100, 760)
		for theme, lang in (("dark", "en"), ("light", "en"), ("dark", "de")):
			self.open("signed-in", theme=theme, lang=lang)
			self.js("""
				const statuses=""" + json.dumps(statuses) + """;
				for(const status of statuses){
					window.rf.receive([{topic:'signin.status',data:status}]);
					const panel=document.querySelector('.setup-status');const banners=panel.querySelectorAll('.banner');
					assert(banners.length===1,'F58 setup one status banner: '+status.state+'/'+status.error_code);
					assert(banners[0].querySelector('.banner-message').textContent===i.t(status.message_key,{account:'Alex (synthetic)'}),'F58 producer message wins');
					const tone=status.error_code==='terms_of_use'?'warning':status.state==='signed_in'?'success':['failed','busy','timed_out'].includes(status.state)?'warning':'info';
					assert(banners[0].classList.contains('tone-'+tone),'F58 tone '+tone);
					assert(Boolean(panel.querySelector('.spinner'))===['starting','waiting','verifying'].includes(status.state),'spinner follows progress');
				}
				window.rf.receive([{topic:'signin.status',data:{...statuses[0],message_key:null}}]);
				assert(document.querySelectorAll('.setup-status .banner').length===1 && document.querySelector('.setup-status .banner-message').textContent===i.t('accounts.signin.starting'),'absent message fallback');
				window.rf.receive([{topic:'signin.status',data:statuses[1]}]);
			""")
			self.assertTrue(Path(_harness.screenshot(f"setup-f58-waiting-{theme}-{lang}-1100x760")).is_file())
			self.js("window.rf.receive([{topic:'signin.status',data:" + json.dumps(statuses[3]) + "}]);")
			self.js("""
				const row=document.querySelector('[data-account="account-1"]');
				assert(row.querySelector('.account-row-email bdi').textContent==='alex@example.com','F59 connected address');
				assert(row.querySelector('.account-row-email bdi').dir==='auto','address isolated');
				assert(!row.querySelector('.account-row-balance'),'F76 unknown balance omitted');
				assert(!document.querySelector('.setup-status .tone-warning'),'connected is success only');
			""")
			self.assertTrue(Path(_harness.screenshot(f"setup-f59-connected-{theme}-{lang}-1100x760")).is_file())
			self.assertEqual([], _harness.errors())

	def test_migration_import_and_failed_import(self):
		self.open("migration")
		self.js("""
			let calls=0;document.getElementById('setup-signin').click();await wait(()=>document.getElementById('dialog').open);
			assert(document.getElementById('dialog').textContent.includes(i.t('errors.app.accounts_migration_needed')),'migration explanation');
			window.pywebview.api.add_account=async req=>{calls++;assert(req.confirm_migration===true,'explicit confirm');return {ok:true,data:{account_id:'account-1',signin:{account_id:'account-1',state:'starting',error_code:null,message_key:'accounts.signin.starting',detail:null,at:'2026-10-14T20:00:00Z'}}}};
			document.querySelector('#dialog .button-primary').click();await wait(()=>calls===1);assert(!document.getElementById('dialog').open,'dialog closed');
		""")
		self.open("legacy-install")
		self.js("""
			document.getElementById('setup-import').click();await wait(()=>document.querySelector('.account-row'));
			assert(s.get('accounts')[0].label==='Imported (synthetic)' && !document.getElementById('setup-continue').disabled,'import configured');
		""")
		self.open("import-error")
		self.js("document.getElementById('setup-import').click();await wait(()=>document.querySelector('[role=alert]'));assert(document.getElementById('view').textContent.includes(i.t('errors.app.accounts_legacy_not_found')),'import error');")
		self.assertEqual([], _harness.errors())

	def test_schedule_payloads_and_legacy_choices(self):
		for fixture in ("automatic", "fixed", "manual"):
			with self.subTest(mode=fixture):
				self.open(fixture, 2)
				self.js("""
					const mode=""" + json.dumps(fixture) + """;
					let req;window.pywebview.api.save_settings=async value=>{req=value;return {ok:false,error:{code:'schedule.failed',message_key:'errors.app.schedule_failed',params:{},detail:null,fields:null}}};
					document.getElementById('setup-continue').click();await wait(()=>req);
					assert(req.changes['schedule.mode']===mode,'mode');
					assert(Object.keys(req.changes).length===(mode==='manual'?2:mode==='fixed'?4:5),'exact wizard fields');
					assert(req.changes['app.autostart']===true,'startup saved with schedule default');
					assert(!('schedule.checks' in req.changes) && !('schedule.random_delay' in req.changes),'advanced untouched');
				""")
		self.open("legacy-task", 2)
		self.js("""
			let replaced=0;const old=window.pywebview.api.replace_legacy_task;window.pywebview.api.replace_legacy_task=async req=>{replaced++;return old(req)};
			assert(document.getElementById('setup-continue').disabled,'legacy choice required');
			document.getElementById('setup-keep-legacy').click();assert(replaced===0 && !document.getElementById('setup-continue').disabled,'keep does not delete');
		""")
		self.open("legacy-task", 2)
		self.js("assert(document.getElementById('setup-replace-legacy') && document.getElementById('setup-keep-legacy'),'fixture reset after same-URL navigation');document.getElementById('setup-replace-legacy').click();await wait(()=>!document.getElementById('setup-replace-legacy'));assert(!document.getElementById('setup-continue').disabled,'replacement accepted');")
		self.assertEqual([], _harness.errors())

	def test_finish_save_order_optional_run_and_recovery(self):
		for run in (False, True):
			self.open("done", 3)
			self.js("""
				const run=""" + json.dumps(run) + """;
				const calls=[];for(const m of ['save_settings','complete_setup','start_run'])window.pywebview.api[m]=async req=>{calls.push([m,req]);return m==='save_settings'?{ok:true,data:{values:{},schedule:{},warnings:[]}}:{ok:true,data:{}}};
				assert(document.getElementById('setup-first-run').getAttribute('aria-checked')==='true','F46 first run defaults on');
				assert(document.getElementById('setup-finish').textContent===i.t('setup.done.finish_run'),'F46 click names effect');
				if(!run)document.getElementById('setup-first-run').click();
				assert(document.getElementById('setup-finish').textContent===i.t(run?'setup.done.finish_run':'setup.done.finish'),'toggle updates finish effect');
				document.getElementById('setup-finish').click();await wait(()=>location.hash===(run?'#/live':'#/overview'));
				assert(calls[0][0]==='complete_setup','finish completes without resaving startup');
				assert(!calls.some(([m])=>m==='save_settings'),'finish never writes startup again');
				assert(calls.some(([m])=>m==='start_run')===run,'run opt in');
				assert(s.get('appInfo').setup_done,'store permits navigation');
			""")
		self.open("first-run-error", 3)
		self.js("document.getElementById('setup-finish').click();await wait(()=>document.getElementById('setup-open-overview'));assert(s.get('appInfo').setup_done && document.querySelector('[role=alert]'),'setup stays done after run failure');")
		self.assertEqual([], _harness.errors())

	def test_background_choice_saved_once_with_schedule_and_survives_back(self):
		self.open("review", 2)
		self.js("""
			const calls=[];
			const routeReady=async(step,control)=>{try{await wait(()=>location.hash==='#/setup/'+step && document.getElementById('view').dataset.route==='#/setup/'+step && document.getElementById(control) && !document.getElementById(control).disabled);}catch(error){throw Error('step '+step+' not ready: '+document.querySelector('.setup-content').textContent+' calls='+JSON.stringify(calls));}};
			for(const method of ['save_settings','complete_setup','start_run']){const original=window.pywebview.api[method];window.pywebview.api[method]=async request=>{calls.push([method,request]);return original(request);};}
			const startup=document.getElementById('setup-autostart');
			assert(s.get('settings').values['app.autostart']===false,'fresh-install producer value remains off');
			assert(startup.getAttribute('aria-checked')==='true','wizard defaults startup on independently of missing Run value');
			document.getElementById('setup-back').click();await routeReady(1,'setup-continue');
			document.getElementById('setup-continue').click();await routeReady(2,'setup-continue');
			assert(document.getElementById('setup-autostart').getAttribute('aria-checked')==='true','unsaved choice survives Back and Continue');
			document.getElementById('setup-continue').click();await routeReady(3,'setup-finish');
			assert(calls.filter(([method])=>method==='save_settings').length===1,'happy path writes once');
			const save=calls.find(([method])=>method==='save_settings')[1].changes;
			assert(save['app.autostart']===true && save['schedule.mode']==='automatic' && save['schedule.window_start']==='09:45' && save['schedule.window_end']==='21:45','same save contains schedule and startup');
			assert(!document.getElementById('setup-autostart') && document.getElementById('setup-first-run'),'step three only asks for optional run');
			document.getElementById('setup-back').click();await routeReady(2,'setup-continue');
			assert(document.getElementById('setup-autostart').getAttribute('aria-checked')==='true','saved choice retained on return');
			document.getElementById('setup-continue').click();await routeReady(3,'setup-finish');
			assert(calls.filter(([method])=>method==='save_settings').length===1,'unchanged return does not write twice');
			document.getElementById('setup-first-run').click();document.getElementById('setup-finish').click();await wait(()=>location.hash==='#/overview');
			assert(calls.filter(([method])=>method==='save_settings').length===1 && calls.filter(([method])=>method==='complete_setup').length===1 && !calls.some(([method])=>method==='start_run'),'Finish only completes, no duplicate save or unwanted run');
		""")
		self.assertEqual([], _harness.errors())

	def test_unticked_autostart_survives_back_and_continue_and_saves_false(self):
		self.open("review", 2)
		self.js("""
			const calls=[];
			const saved=s.get('settings');
			const ready=async(step,control)=>wait(()=>document.getElementById('view').dataset.route==='#/setup/'+step && document.getElementById(control) && !document.getElementById(control).disabled);
			assert(saved.values['app.autostart']===false && document.getElementById('setup-autostart').getAttribute('aria-checked')==='true','fixture off, wizard on');
			window.pywebview.api.save_settings=async request=>{calls.push(request);return {ok:true,data:{...saved,values:{...saved.values,...request.changes},warnings:[]}};};
			document.getElementById('setup-autostart').click();
			assert(document.getElementById('setup-autostart').getAttribute('aria-checked')==='false','user chooses off');
			document.getElementById('setup-back').click();await ready(1,'setup-continue');
			document.getElementById('setup-continue').click();await ready(2,'setup-continue');
			assert(document.getElementById('setup-autostart').getAttribute('aria-checked')==='false','unsaved off choice survives Back and Continue');
			document.getElementById('setup-continue').click();await ready(3,'setup-finish');
			assert(calls.length===1 && calls[0].changes['app.autostart']===false && calls[0].changes['schedule.mode']==='automatic','step two saves user off choice with schedule');
			document.getElementById('setup-back').click();await ready(2,'setup-continue');
			assert(document.getElementById('setup-autostart').getAttribute('aria-checked')==='false','saved off choice survives Back');
			document.getElementById('setup-continue').click();await ready(3,'setup-finish');
			assert(calls.length===1,'unchanged off choice does not save again');
			document.getElementById('setup-first-run').click();document.getElementById('setup-finish').click();await wait(()=>location.hash==='#/overview');
			assert(calls.length===1 && s.get('settings').values['app.autostart']===false,'Finish does not overwrite off choice');
		""")
		self.assertEqual([], _harness.errors())

	def test_schedule_summary_modes_unknown_and_failure_are_explicit(self):
		for fixture in ("review", "fixed", "manual", "done", "schedule-error"):
			with self.subTest(fixture=fixture):
				self.open(fixture, 3)
				self.js("""
					const fixture=""" + json.dumps(fixture) + """;
					const saved=s.get('settings'),mode=saved.values['schedule.mode'],schedule=saved.schedule;
					const summary=document.querySelector('.setup-summary');
					const knownNext=mode==='manual'||Boolean(schedule.next_at&&!schedule.error_code&&schedule.task_exists&&schedule.in_sync);
					assert(summary.querySelectorAll('.setup-summary-row').length===(knownNext?3:2),'F76 account schedule and known next only');
					const account=summary.querySelector('[data-summary=account]');
					const next=summary.querySelector('[data-summary=next] dd')?.textContent;
					const {scheduleTime}=await import('./js/schedule-time.js');
					const expectedNext=scheduleTime(schedule,s.get('overview')?.generated_at||new Date().toISOString(),i,s.get('appInfo')?.format_locale);
					assert(!document.getElementById('setup-autostart') && document.getElementById('setup-first-run'),'finish choices');
					if(fixture==='review'){
						const identity=s.get('accounts')[0];
						assert(account.querySelector('.account-row-label').textContent===identity.label && !account.textContent.includes(identity.name) && account.querySelector('.account-row-email bdi').textContent===identity.email,'label and address from connected account');
						assert(summary.querySelector('[data-summary=schedule] dd').textContent.includes(i.t('setup.summary.automatic',{checks:4,start:i.formatTime('09:45'),end:i.formatTime('21:45')})),'automatic plural/window');
						assert(next===expectedNext,'automatic shared Overview next status, not fabricated');
					}else if(mode==='fixed'){
						assert(summary.querySelector('[data-summary=schedule]').textContent.includes(i.t('setup.summary.fixed',{time:i.formatTime(saved.values['schedule.time'])})),'fixed time summary');
						assert(next===expectedNext,'fixed shared Overview next status');
					}else if(mode==='manual'){
						assert(summary.querySelector('[data-summary=schedule]').textContent.includes(i.t('setup.schedule.manual_body')) && next===i.t('overview.schedule.manual_next'),'manual explicitly waits for Run now');
					}else{
						assert(next===undefined,'F76 unknown and failed next time omitted');
						if(fixture==='schedule-error')assert(document.querySelector('.setup-content .banner').textContent===i.t('errors.app.schedule_failed'),'failed schedule warning still visible');
					}
					if(next!==undefined)assert(next.trim()!=='','known next row never empty');
				""")
				self.assertEqual([], _harness.errors())

	def test_summary_every_day_for_automatic_and_fixed_subset_and_manual(self):
		for fixture in ("automatic", "fixed"):
			for all_days in (True, False):
				with self.subTest(fixture=fixture, all_days=all_days):
					self.open(fixture, 2)
					self.js("""
						const allDays=""" + json.dumps(all_days) + """;
						const target=allDays?['mon','tue','wed','thu','fri','sat','sun']:['mon','wed','fri'];
						document.querySelector('.setup-days').open=true;
						for(const button of document.querySelectorAll('#setup-days button[data-day]')){
							if((button.getAttribute('aria-pressed')==='true')!==target.includes(button.dataset.day))button.click();
						}
						let saved;
						const original=window.pywebview.api.save_settings;
						window.pywebview.api.save_settings=async request=>{saved=request;return original(request);};
						document.getElementById('setup-continue').click();
						await wait(()=>document.getElementById('view').dataset.route==='#/setup/3' && document.querySelector('.setup-summary-detail'));
						const text=document.querySelector('[data-summary=schedule] .setup-summary-detail').textContent;
						assert(JSON.stringify(saved.changes['schedule.days'])===JSON.stringify(target),'selected days saved through real control');
						if(allDays)assert(text===i.t('setup.summary.every_day') && text==='Every day','seven days compact for either mode');
						else{
							const expected=i.formatList([5,7,9].map(day=>new Intl.DateTimeFormat('en-US',{weekday:'short'}).format(new Date(2026,0,day))));
							assert(text===expected && text!==i.t('setup.summary.every_day'),'subset keeps localized weekday list');
						}
					""")
					self.assertEqual([], _harness.errors())
		self.open("manual", 3)
		self.js("assert(!document.querySelector('[data-summary=schedule] .setup-summary-detail'),'manual still has no days line');")
		self.assertEqual([], _harness.errors())
		_harness.viewport(1920, 1080)
		self.open("review", 3)
		self.js("assert(document.querySelector('[data-summary=schedule] .setup-summary-detail').textContent==='Every day','all days screenshot state');document.activeElement.blur();")
		path = _harness.screenshot("setup-summary-every-day-dark-en-1920x1080")
		self.assertTrue(Path(path).is_file())
		print("Every day summary screenshot=" + path, flush=True)
		self.assertEqual([], _harness.errors())

	def test_risk_above_card_in_first_viewport_and_responsive_actions(self):
		for width, height in ((860, 600), (1100, 760), (1440, 900), (1920, 1080)):
			_harness.viewport(width, height)
			for fixture in ("signin", "signed-in"):
				self.open(fixture)
				self.js("""
					const root=document.querySelector('.setup-screen'),risk=root.querySelector('.risk-notice'),card=root.querySelector('.card');
					const rbox=risk.getBoundingClientRect(),cbox=card.getBoundingClientRect();
					assert(document.getElementById('view').scrollTop===0,'initial viewport');
					assert(rbox.top>=0 && rbox.bottom<=innerHeight && rbox.bottom<cbox.top,'risk inside first viewport above card');
					const signin=document.getElementById('setup-signin');if(signin)assert(rbox.bottom<signin.getBoundingClientRect().top,'risk before Sign in');
					assert(root.getBoundingClientRect().width<=1361,'canvas capped at1360');
					const steps=root.querySelector('.setup-steps'),items=[...steps.children].map(node=>node.getBoundingClientRect());
					assert(Math.abs(items[1].top-items[0].top)<1,'F76 one compact step strip at every shipped width');
					const button=document.getElementById('setup-continue'),body=card.querySelector('.card-body').getBoundingClientRect(),b=button.getBoundingClientRect();
					assert(Math.abs(b.left-body.left)<1 && b.width<body.width*.8,'button natural width on content edge');
					assert(!document.querySelector('#view [style]') && root.scrollWidth<=root.clientWidth+1,'CSP and no overflow');
				""")
				self.assertEqual([], _harness.errors())

	def test_setup_inline_recovery_real_mock_answers(self):
		_harness.viewport(1280, 800)
		def capture(name, theme, lang):
			self.js("""
				const panel=document.querySelector('.setup-recovery');
				if(panel){panel.scrollIntoView({block:'center'});for(const button of panel.querySelectorAll('button')){const box=button.getBoundingClientRect();assert(box.top>=0 && box.bottom<=innerHeight,'recovery action in capture')}}
			""")
			self.assertTrue(Path(_harness.screenshot(f"setup-{name}-{theme}-{lang}")).is_file())
		for theme in ("dark", "light"):
			for lang in ("en", "x-long"):
				self.open("failed", theme=theme, lang=lang)
				capture("edge-recovery-ready", theme, lang)
				self.js("""
					const calls=[];for(const m of ['pick_path','save_settings','export_diagnostics']){const original=window.pywebview.api[m];window.pywebview.api[m]=async req=>{calls.push([m,req]);return original(req)}}
					assert(document.getElementById('setup-export-diagnostics') && document.getElementById('setup-choose-edge'),'B1 inline actions');
					document.getElementById('setup-export-diagnostics').click();await wait(()=>document.querySelector('.setup-recovery .tone-success'));
					assert(document.querySelector('.setup-recovery').textContent.includes('diagnostics.zip'),'diagnostic path');
					document.getElementById('setup-choose-edge').click();await wait(()=>calls.some(([m])=>m==='save_settings') && !document.getElementById('setup-choose-edge').disabled);
					assert(calls.find(([m])=>m==='pick_path')[1].kind==='edge','edge picker kind');
					const changes=calls.find(([m])=>m==='save_settings')[1].changes;
					assert(Object.keys(changes).length===1 && changes['advanced.edge_path']==='C:\\\\Synthetic\\\\Edge\\\\msedge.exe','selected edge path only');
					assert(location.hash==='#/setup/1' && s.get('settings').values['advanced.edge_path']===changes['advanced.edge_path'],'saved without leaving setup');
				""")
				capture("edge-saved", theme, lang)
				self.js("document.getElementById('setup-retry-signin').click();await wait(()=>document.querySelector('[data-signin-state]').dataset.signinState==='starting');")
				self.open("driver-mismatch", theme=theme, lang=lang)
				capture("driver-recovery-ready", theme, lang)
				self.js("document.getElementById('setup-reset-driver').click();await wait(()=>document.querySelector('.setup-recovery .tone-success'));assert(s.get('settings').values['advanced.driver_path']==='','custom driver cleared');")
				capture("driver-reset", theme, lang)
				self.open("driver-missing", theme=theme, lang=lang)
				capture("driver-missing-recovery", theme, lang)
				self.open("picker-cancel", theme=theme, lang=lang)
				self.js("let saves=0;const old=window.pywebview.api.save_settings;window.pywebview.api.save_settings=async req=>{saves++;return old(req)};document.getElementById('setup-choose-edge').click();await wait(()=>!document.getElementById('setup-choose-edge').disabled);document.getElementById('setup-export-diagnostics').click();await wait(()=>!document.getElementById('setup-export-diagnostics').disabled);assert(saves===0 && !document.querySelector('.setup-recovery .tone-success'),'cancel makes no write');")
				capture("picker-cancelled", theme, lang)
				self.open("edge-save-error", theme=theme, lang=lang)
				self.js("""
					document.getElementById('setup-choose-edge').click();await wait(()=>document.querySelector('[data-recovery-field="advanced.edge_path"]'));
					const reason=document.querySelector('.setup-recovery [data-recovery-field="advanced.edge_path"]');
					assert(reason.textContent===i.t('errors.settings.file_missing') && reason.checkVisibility({checkVisibilityCSS:true}),'N2 existing Settings reason is visible');
					assert(reason.getAttribute('role')==='alert' && !document.querySelector('.setup-recovery .banner') && !document.querySelector('.setup-recovery').textContent.includes(i.t('errors.app.settings_invalid')),'F53 file_missing reason without generic marked-fields banner');
					assert(document.getElementById('setup-choose-edge').getAttribute('aria-invalid')==='true' && document.getElementById('setup-choose-edge').getAttribute('aria-describedby')===reason.id,'N2 picker marked and reason associated');
					assert(!document.querySelector('.setup-recovery .tone-success') && document.getElementById('setup-retry-signin'),'save failure recoverable');
				""")
				capture("edge-save-failed", theme, lang)
				self.js("""
					const pick=window.pywebview.api.pick_path;
					window.pywebview.api.pick_path=async request=>{const response=await pick(request);response.data.path='C:\\\\Synthetic\\\\Edge\\\\wrong.exe';return response};
					document.getElementById('setup-choose-edge').click();
					await wait(()=>document.querySelector('.setup-recovery [data-recovery-field="advanced.edge_path"]')?.textContent===i.t('errors.settings.file_wrong_name',{name:'msedge.exe'}));
					const reason=document.querySelector('.setup-recovery [data-recovery-field="advanced.edge_path"]');
					assert(reason.checkVisibility({checkVisibilityCSS:true}) && reason.textContent.includes('msedge.exe'),'N2 producer file_wrong_name includes required executable');
					assert(reason.getAttribute('role')==='alert' && !document.querySelector('.setup-recovery .banner') && !document.querySelector('.setup-recovery').textContent.includes(i.t('errors.app.settings_invalid')),'F53 file_wrong_name reason without generic marked-fields banner');
					assert(!reason.textContent.includes('{name}'),'N2 executable placeholder resolved');
				""")
				capture("edge-wrong-name", theme, lang)
				self.open("export-error", theme=theme, lang=lang)
				self.js("document.getElementById('setup-export-diagnostics').click();await wait(()=>document.querySelector('.setup-recovery [role=alert]'));assert(document.querySelector('.setup-recovery').textContent.includes(i.t('errors.app.diagnostics_failed')),'export error shown');")
				capture("export-failed", theme, lang)
				for fixture, step, trigger in (("load-error", 1, None), ("replace-error", 2, "setup-replace-legacy"), ("finish-error", 3, "setup-finish")):
					self.open(fixture, step, theme, lang)
					if trigger:
						self.js(f"document.getElementById('{trigger}').click();await wait(()=>document.getElementById('setup-export-diagnostics'));")
					capture(f"{fixture}-recovery", theme, lang)
					self.js("document.getElementById('setup-export-diagnostics').click();await wait(()=>document.querySelector('.setup-recovery .tone-success'));assert(document.querySelector('.setup-recovery').textContent.includes('diagnostics.zip'),'generic setup recovery exported');")
					capture(f"{fixture}-exported", theme, lang)
				self.open("done", 3, theme, lang)
				self.js("document.getElementById('setup-first-run').click();assert(document.getElementById('setup-finish').textContent===i.t('setup.done.finish'),'first run can be disabled');")
				self.assertTrue(Path(_harness.screenshot(f"setup-first-run-off-{theme}-{lang}")).is_file())
				self.assertEqual([], _harness.errors())

	def test_setup_recovery_keeps_unrendered_and_nonfield_error_banners(self):
		_harness.viewport(1280, 800)
		for theme in ("dark", "light"):
			for lang in ("en", "x-long"):
				self.open("edge-save-error", theme=theme, lang=lang)
				self.js("""
					const save=window.pywebview.api.save_settings;
					const cases=[
						{fields:{'schedule.days':'days_empty'},code:'settings.invalid',message_key:'errors.app.settings_invalid'},
						{fields:null,code:'settings.invalid',message_key:'errors.app.settings_invalid'},
						{fields:{},code:'settings.invalid',message_key:'errors.app.settings_invalid'},
						{fields:{'advanced.edge_path':'file_missing','schedule.days':'days_empty'},code:'settings.invalid',message_key:'errors.app.settings_invalid'},
						{fields:{'advanced.edge_path':'file_missing'},code:'internal',message_key:'errors.app.internal'}
					];
					for(const testCase of cases){
						window.pywebview.api.save_settings=async request=>{const response=await save(request);response.error={...response.error,...testCase};return response};
						document.getElementById('setup-choose-edge').click();
						await wait(()=>!document.getElementById('setup-choose-edge').disabled && document.querySelector('.setup-recovery .banner'));
						const banner=document.querySelector('.setup-recovery .banner');
						assert(banner.textContent===i.t(testCase.message_key) && banner.checkVisibility({checkVisibilityCSS:true}),'F53 other and nonfield errors retain banner');
						assert(document.querySelectorAll('.setup-recovery .banner').length===1,'one recovery banner');
					}
					window.pywebview.api.save_settings=save;
					const panel=document.querySelector('.setup-recovery');panel.scrollIntoView({block:'center'});
				""")
				self.assertTrue(Path(_harness.screenshot(f"setup-recovery-banner-fallback-{theme}-{lang}")).is_file())
				self.assertEqual([], _harness.errors())

	def test_z_fixture_matrix_keyboard_and_screenshots(self):
		for width, height in ((1280, 800), (860, 600)):
			_harness.viewport(width, height)
			for lang in ("en", "x-long", "x-rtl"):
				for theme in ("dark", "light"):
					for fixture in FIXTURES:
						with self.subTest(fixture=fixture, theme=theme, lang=lang, width=width):
							step = 2 if fixture in STEP_TWO else 3 if fixture in STEP_THREE else 1
							self.open(fixture, step, theme, lang)
							if fixture == "migration":
								self.js("document.getElementById('setup-signin').click();await wait(()=>document.getElementById('dialog').open);")
							elif fixture in ("import-error", "schedule-error", "replace-error", "finish-error"):
								identity = {"import-error": "setup-import", "schedule-error": "setup-continue", "replace-error": "setup-replace-legacy", "finish-error": "setup-finish"}[fixture]
								self.js(f"document.getElementById('{identity}').click();await wait(()=>document.querySelector('[role=alert]'));")
							elif fixture == "first-run-error":
								self.js("document.getElementById('setup-finish').click();await wait(()=>document.getElementById('setup-open-overview'));")
							self.js("""
								assert(document.querySelector('.risk-notice'),'visible risk');assert(!document.querySelector('[style]'),'no inline style');
								assert(document.title===i.t('setup.title'),'title contract');
								assert(document.querySelectorAll('.setup-step-number').length===3 && [...document.querySelectorAll('.setup-steps li')].every(n=>getComputedStyle(n).borderBlockEndWidth==='0px'),'B7 numbered non-tab progress');
								if(document.querySelector('.setup-recovery'))assert(document.getElementById('setup-export-diagnostics'),'B1 diagnostics stays in setup');
								assert(document.documentElement.scrollWidth<=innerWidth+1 && document.getElementById('view').scrollWidth<=document.getElementById('view').clientWidth+1,'no overflow');
							""")
							if width == 1280:
								modal = _harness.driver.execute_script("return document.getElementById('dialog').open")
								if modal:
									from selenium.webdriver.common.keys import Keys
									ids = _harness.driver.execute_script("const buttons=[...document.querySelectorAll('#dialog button:not(:disabled)')];buttons[0].focus();return buttons.map((n,j)=>{n.id ||= 'setup-dialog-tab-'+j;return n.id})")
									seen = set()
									for _ in range(len(ids) + 2):
										_harness.driver.switch_to.active_element.send_keys(Keys.TAB)
										self.assertTrue(_harness.driver.execute_script("return document.getElementById('dialog').contains(document.activeElement)"))
										seen.add(_harness.driver.execute_script("return document.activeElement.id"))
									self.assertTrue(set(ids).issubset(seen), (ids, sorted(seen)))
									for _ in range(len(ids) + 2):
										_harness.driver.switch_to.active_element.send_keys(Keys.SHIFT, Keys.TAB)
										self.assertTrue(_harness.driver.execute_script("return document.getElementById('dialog').contains(document.activeElement)"))
								else:
									was_open = _harness.driver.execute_script("const days=document.querySelector('#view .setup-days');if(!days)return null;const open=days.open;days.open=true;return open")
									ids = _harness.driver.execute_script("return [...document.querySelectorAll('#view button,#view input,#view select,#view summary,#view a[href]')].filter(n=>n.tabIndex>=0 && !n.matches(':disabled') && !n.closest('[inert],[hidden]') && n.checkVisibility({checkVisibilityCSS:true}) && !n.closest('details:not([open])')).map((n,j)=>{n.id ||= 'setup-tab-'+j;return n.id})")
									time_stops = _harness.driver.execute_script("return document.querySelectorAll('#view input[type=time]').length")
									order = _harness.tab_order(len(ids) + time_stops + 3)
									self.assertTrue(set(ids).issubset(order), {"missing": sorted(set(ids) - set(order)), "seen": sorted(set(ids) & set(order))})
									if was_open is not None:
										_harness.driver.execute_script("document.querySelector('#view .setup-days').open=arguments[0]", was_open)
								_harness.driver.execute_script("document.getElementById('view').scrollTop=0;if(!document.getElementById('dialog').open)document.getElementById('view').focus()")
								self.assertTrue(Path(_harness.screenshot(f"setup-{fixture}-{theme}-{lang}")).is_file())
							self.assertEqual([], _harness.errors())
					print(f"setup matrix {width} {lang} {theme}: {len(FIXTURES)}", flush=True)
		print(f"setup screenshots: {SCREENSHOT_DIR}; signed_in={_harness.signed_in}", flush=True)
