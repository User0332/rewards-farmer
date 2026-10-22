import _bootstrap  # noqa: F401

import json
import os
import unittest

from ui.harness import UI_DIR, UiHarness


_enabled = os.environ.get("REWARDS_APP_UI_TESTS") == "1"
_harness = None
FIXTURES = {"automatic":"schedule", "fixed":"schedule", "manual":"schedule", "field-errors":"run", "legacy":"schedule", "ollama":"run", "openrouter":"run"}
SECTIONS = ("schedule", "run", "notifications", "app", "advanced")
HIDDEN_SETTINGS = {"schedule.checks", "schedule.min_points"}
SECRET_IDS = {"run.openrouter_key", "notify.webhook_url"}


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


class SettingsSourceTests(unittest.TestCase):
	def test_synthetic_fixtures_and_setting_inventory(self):
		from rfapp.settings import SETTING_IDS
		self.assertEqual(set(FIXTURES), {path.stem for path in (UI_DIR / "mock/settings").glob("*.json")})
		modes = set()
		for name in FIXTURES:
			fixture = json.loads((UI_DIR / f"mock/settings/{name}.json").read_text(encoding="utf-8"))
			self.assertIs(fixture["synthetic"], True)
			data = fixture["methods"]["get_settings"]["data"]
			self.assertEqual(set(SETTING_IDS) - SECRET_IDS, set(data["values"]), name)
			self.assertEqual(SECRET_IDS, set(data["secrets_set"]), name)
			self.assertTrue(all(type(flag) is bool for flag in data["secrets_set"].values()))
			for method in ("get_settings", "save_settings"):
				answer = fixture["methods"].get(method, [])
				for entry in answer if isinstance(answer, list) else [answer]:
					if not entry["ok"]:
						continue
					self.assertEqual(set(SETTING_IDS) - SECRET_IDS, set(entry["data"]["values"]), name)
					self.assertEqual(SECRET_IDS, set(entry["data"]["secrets_set"]), name)
					self.assertIn(entry["data"]["values"]["run.browser_mode"], ("visible", "hidden"), name)
			modes.add(data["values"]["schedule.mode"])
			self.assertIn("legacy_task", data["schedule"])
		self.assertEqual({"automatic", "fixed", "manual"}, modes)
		fixture = json.loads((UI_DIR / "mock/settings/field-errors.json").read_text(encoding="utf-8"))
		self.assertEqual({"run.ollama_url":"dollar_brace"}, fixture["methods"]["save_settings"]["error"]["fields"])
		self.assertIsNotNone(json.loads((UI_DIR / "mock/settings/legacy.json").read_text(encoding="utf-8"))["methods"]["get_settings"]["data"]["schedule"]["legacy_task"])

	def test_catalog_shared_controls_and_boundaries(self):
		catalog = json.loads((UI_DIR / "i18n/en/settings.json").read_text(encoding="utf-8"))
		setup = json.loads((UI_DIR / "i18n/en/setup.json").read_text(encoding="utf-8"))
		self.assertEqual(setup["messages"]["setup.schedule.replaced"]["text"], catalog["messages"]["settings.schedule.replaced"]["text"])
		self.assertEqual("settings", catalog["namespace"])
		for key, entry in catalog["messages"].items():
			self.assertTrue(entry["note"].strip(), key)
		js = (UI_DIR / "js/screens/settings.js").read_text(encoding="utf-8")
		self.assertIn("c.textField.create", js)
		self.assertNotRegex(js, r"el\([\"']input[\"']|createElement\([\"']input[\"']")
		self.assertNotIn("location.reload", js)
		self.assertNotIn("innerHTML", js)
		self.assertIn("data.secrets_set", js)
		self.assertEqual("Saved. Type a new value to replace it.", catalog["messages"]["settings.secret.saved"]["text"])
		self.assertEqual("Remove", catalog["messages"]["settings.secret.remove"]["text"])
		for path in sorted((UI_DIR / "i18n").glob("*/settings.json")):
			messages = json.loads(path.read_text(encoding="utf-8"))["messages"]
			self.assertNotIn("settings.run.browser.desktop", messages, path)
			self.assertNotIn("settings.run.browser.desktop_help", messages, path)
		self.assertNotRegex(js, r"\bstyle\s*:|\.style\b")
		for path in (UI_DIR / "js/screens/settings.js", UI_DIR / "css/screens/settings.css", UI_DIR / "i18n/en/settings.json"):
			self.assertNotRegex(path.read_text(encoding="utf-8"), "[\u2013\u2014]")
		css = (UI_DIR / "css/screens/settings.css").read_text(encoding="utf-8")
		self.assertNotRegex(css, r"(?:margin|padding)-(?:left|right)|\b(?:left|right)\s*:|text-align\s*:\s*(?:left|right)")


@unittest.skipUnless(_enabled, "Set REWARDS_APP_UI_TESTS=1 for isolated loopback Settings tests")
class SettingsTests(unittest.TestCase):
	def r4_css(self, rules):
		_harness.driver.execute_cdp_cmd("DOM.enable", {})
		_harness.driver.execute_cdp_cmd("CSS.enable", {})
		frame = _harness.driver.execute_cdp_cmd("Page.getFrameTree", {})["frameTree"]["frame"]["id"]
		sheet = _harness.driver.execute_cdp_cmd("CSS.createStyleSheet", {"frameId": frame})["styleSheetId"]
		_harness.driver.execute_cdp_cmd("CSS.setStyleSheetText", {"styleSheetId": sheet, "text": rules})

	def test_r4_l7_next_time_has_its_label(self):
		for lang in ("en", "de", "ar"):
			for fixture in ("automatic", "fixed"):
				self.open(fixture, lang=lang)
				if os.environ.get("RF_F76_R4_MUTATION") == "L7":
					_harness.driver.execute_script("document.querySelector('.settings-schedule-next dt').remove()")
				self.js("""
				 const row=document.querySelector('.settings-schedule-next'),label=row?.querySelector('dt'),value=row?.querySelector('dd'),schedule=s.get('settings').schedule;
				 assert(label?.checkVisibility()&&label.textContent===i.t(schedule.next_is_check?'overview.next_check':'overview.next_run')&&label.nextElementSibling===value,'R4 L7 next time has visible semantic label');
				 const {scheduleTime}=await import('./js/schedule-time.js');
				 assert(value.classList.contains('settings-schedule-status')&&value.textContent===scheduleTime(schedule,s.get('overview')?.generated_at||new Date().toISOString(),i,s.get('appInfo')?.format_locale),'R4 L7 shared timestamp retained');
				 assert(row.querySelectorAll('dt,dd').length===2&&getComputedStyle(value).marginInlineStart==='0px','R4 L7 compact single label/value pair');
				 """)
				self.assertEqual([], _harness.errors())

	def test_r4_l8_browse_on_input_row_and_native_keyboard(self):
		from selenium.webdriver.common.keys import Keys
		for lang in ("en", "de", "ar"):
			for width, height in ((1131, 643), (860, 600)):
				_harness.viewport(width, height)
				self.open("automatic", "advanced", lang=lang)
				if os.environ.get("RF_F76_R4_MUTATION") == "L8":
					self.r4_css(".settings-path-field > .button{grid-row:1!important;align-self:start!important}")
				self.js("""
				 for(const field of document.querySelectorAll('.settings-path-field')){
				  const input=field.querySelector('input'),pick=field.querySelector('button'),label=field.querySelector('label'),help=field.querySelector('.field-description');
				  const a=input.getBoundingClientRect(),b=pick.getBoundingClientRect(),f=field.getBoundingClientRect(),rtl=document.documentElement.dir==='rtl';
				  assert(Math.abs((a.top+a.height/2)-(b.top+b.height/2))<1,'R4 L8 Browse centered on input row');
				  assert(label.getBoundingClientRect().bottom<=a.top&&help.getBoundingClientRect().bottom<=a.top,'R4 L8 label and description above input');
				  assert(rtl?b.right<=a.left:a.right<=b.left,'R4 L8 input Browse mirrored order');
				  assert(a.width>f.width/2&&Math.abs((rtl?a.right:a.left)-(rtl?f.right:f.left))<1&&Math.abs((rtl?b.left:b.right)-(rtl?f.left:f.right))<1,'R4 L8 input fills remaining row width');
				  assert(input.dir==='ltr'&&pick.getAttribute('aria-label')===label.textContent+', '+i.t('settings.advanced.pick'),'R4 L8 path direction and named Browse');
				 }
				 assert(document.querySelectorAll('.settings-path-field').length===3,'R4 L8 all three paths');
				 """)
				ids = ["setting-advanced-edge-path", "setting-advanced-driver-path", "setting-advanced-data-folder"]
				_harness.driver.execute_script("window.r4Picks=[];window.pywebview.api.pick_path=async req=>{window.r4Picks.push(req);return {ok:true,data:{path:null}}};document.getElementById('setting-advanced-edge-path').focus()")
				for n, identity in enumerate(ids):
					self.assertEqual(identity, _harness.driver.switch_to.active_element.get_attribute("id"))
					_harness.driver.switch_to.active_element.send_keys(Keys.TAB)
					self.assertEqual(identity + "-browse", _harness.driver.switch_to.active_element.get_attribute("id"))
					_harness.driver.switch_to.active_element.send_keys(Keys.ENTER)
					self.js("await wait(()=>window.r4Picks.length===" + str(n + 1) + ");await new Promise(done=>setTimeout(done,0));")
					self.assertEqual(identity + "-browse", _harness.driver.switch_to.active_element.get_attribute("id"), "R4 L8 picker cancellation returns focus to Browse")
					_harness.driver.switch_to.active_element.send_keys(Keys.TAB)
				self.assertEqual("setting-advanced-log-level", _harness.driver.switch_to.active_element.get_attribute("id"))
				self.js("assert(JSON.stringify(window.r4Picks.map(x=>x.kind))===JSON.stringify(['edge','driver','data_folder']),'R4 L8 original picker kinds retained')")
				self.assertEqual([], _harness.errors())

	def test_r4_l9_export_description_belongs_to_button(self):
		for lang in ("en", "de", "ar"):
			self.open("automatic", "advanced", lang=lang)
			if os.environ.get("RF_F76_R4_MUTATION") == "L9":
				_harness.driver.execute_script("const root=document.querySelector('.settings-export');root.prepend(root.querySelector('p'))")
			self.js("""
			 const group=document.querySelector('.settings-export'),button=group.querySelector('button'),help=group.querySelector('.field-description');
			 assert(group.firstElementChild===button&&button.nextElementSibling===help,'R4 L9 export button precedes its description');
			 assert(help.textContent===i.t('settings.advanced.export.help')&&button.getAttribute('aria-describedby')===help.id,'R4 L9 export help ownership');
			 assert(group.previousElementSibling.dataset.settingId==='advanced.log_level'&&parseFloat(getComputedStyle(group.parentElement).rowGap)>=16,'R4 L9 normal field separation before export');
			 assert(help.getBoundingClientRect().top>=button.getBoundingClientRect().bottom&&getComputedStyle(help).fontSize===getComputedStyle(document.querySelector('.settings-path-field .field-description')).fontSize,'R4 L9 help directly below export');
			 """)
			self.assertEqual([], _harness.errors())

	def test_r4_l11_segmented_help_uses_field_description(self):
		for lang in ("en", "de", "ar"):
			self.open("automatic", lang=lang)
			if os.environ.get("RF_F76_R4_MUTATION") == "L11":
				_harness.driver.execute_script("document.querySelector('.settings-mode-help').classList.remove('field-description')")
			self.js("""
			 const help=document.querySelector('.settings-mode-help'),reference=document.querySelector('[data-setting-id="schedule.catch_up"] .field-description'),keys=['fontFamily','fontSize','fontWeight','lineHeight','color'];
			 const signature=node=>keys.map(key=>getComputedStyle(node)[key]);
			 window.r4HelpStyle=signature(help);
			 assert(help.classList.contains('field-description')&&JSON.stringify(signature(help))===JSON.stringify(signature(reference)),'R4 L11 Schedule help uses field-description style');
			 assert(help.textContent===i.t('settings.schedule.automatic_help'),'R4 L11 help copy unchanged');
			 await r.navigate('#/settings/run');await wait(()=>document.querySelector('[data-setting-id="run.browser_mode"] .field-description'));
			 const browser=document.querySelector('[data-setting-id="run.browser_mode"] .field-description');
			 assert(JSON.stringify(signature(browser))===JSON.stringify(window.r4HelpStyle),'R4 L11 segmented descriptions have identical computed type');
			 """)
			self.assertEqual([], _harness.errors())
		requested = os.environ.get("RF_F76_R4_SCRATCH")
		if requested:
			from pathlib import Path
			import importlib.util
			import tempfile
			root = Path(requested).resolve()
			self.assertTrue(root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
			spec = importlib.util.spec_from_file_location("f76_r4_capture", root / "capture.py")
			module = importlib.util.module_from_spec(spec)
			spec.loader.exec_module(module)
			module.run(_harness, root)

	def test_f76_headings_and_disclosure_do_not_remove_controls(self):
		self.open("automatic")
		self.js("""
		 assert(!document.querySelector('.screen-header p,.settings-section-description'),'F76 no repeated page or section introduction');
		 assert(document.getElementById('settings-save').disabled&&document.getElementById('settings-cancel').disabled,'F76 clean save semantics unchanged');
		 assert(document.querySelector('.settings-footer [role=status]').textContent==='','F76 clean-state caption omitted');
		 assert(document.getElementById('setting-schedule-mode')&&document.getElementById('setting-schedule-window-start')&&document.getElementById('setting-schedule-window-end'),'F76 exposed schedule controls retained');
		 r.navigate('#/settings/advanced');await wait(()=>document.querySelector('.settings-advanced'));
		 const advanced=document.querySelector('.settings-advanced');assert(!advanced.matches('details')&&!advanced.querySelector('summary'),'R3 Advanced needs no second click');
		 assert(document.getElementById('setting-advanced-edge-path').checkVisibility()&&document.getElementById('setting-advanced-driver-path').checkVisibility(),'R3 advanced controls immediately visible');
		 """)
		from selenium.webdriver.common.keys import Keys
		_harness.driver.execute_script("document.getElementById('settings-section-advanced').focus()")
		_harness.driver.switch_to.active_element.send_keys(Keys.TAB)
		self.assertEqual("setting-advanced-edge-path", _harness.driver.execute_script("return document.activeElement.id"))
		self.assertEqual([], _harness.errors())

	def test_r3_l3_panel_name_from_navigation_without_repeat(self):
		from selenium.webdriver.common.keys import Keys
		self.open()
		for section in SECTIONS:
			self.js("await r.navigate('#/settings/" + section + "');await wait(()=>document.querySelector('.settings-panel')?.dataset.section==='" + section + "');")
			if os.environ.get("RF_F76_R3_MUTATION") == "L3":
				_harness.driver.execute_script("const n=document.createElement('h2');n.textContent=document.querySelector('.settings-sections [aria-current=page]').textContent;document.querySelector('.settings-panel .card').prepend(n)")
			self.js("""
			 const panel=document.querySelector('.settings-panel'),selected=document.querySelector('.settings-sections [aria-current=page]');
			 assert(![...panel.querySelectorAll('h2,h3,.card-header')].some(n=>n.checkVisibility()),'R3 L3 no repeated panel heading');
			 assert(panel.getAttribute('aria-labelledby')===selected.id&&panel.getAttribute('role')==='region','R3 L3 panel named from selected tab');
			 assert(document.querySelectorAll('.settings-sections a').length===5,'R3 navigation retained');
			 """)
			targets = _harness.driver.execute_script("""
			 const links=[...document.querySelectorAll('.settings-sections a')],selected=links.find(node=>node.getAttribute('aria-current')==='page');
			 const first=[...document.querySelectorAll('.settings-panel button,.settings-panel input,.settings-panel select')].find(node=>!node.disabled&&node.tabIndex>=0&&node.checkVisibility());
			 selected.focus();
			 return [...links.slice(links.indexOf(selected)+1),first];
			 """)
			self.assertIsNotNone(targets[-1], "R3 L3 first panel control exists")
			for target in targets:
				_harness.driver.switch_to.active_element.send_keys(Keys.TAB)
				self.assertEqual(target, _harness.driver.switch_to.active_element, "R3 L3 navigation-to-panel native focus order: " + section)
		self.assertEqual([], _harness.errors())

	def test_r3_l4_advanced_fields_direct_and_keyboard(self):
		self.open("automatic", "advanced")
		if os.environ.get("RF_F76_R3_MUTATION") == "L4":
			_harness.driver.execute_script("const fields=document.getElementById('settings-advanced'),d=document.createElement('details'),s=document.createElement('summary');s.textContent='Advanced';fields.before(d);d.append(s,fields)")
		self.js("""
		 const fields=document.getElementById('settings-advanced');
		 assert(!fields.closest('details')&&document.getElementById('setting-advanced-edge-path').checkVisibility(),'R3 L4 Advanced fields directly visible');
		 for(const id of ['setting-advanced-edge-path','setting-advanced-driver-path','setting-advanced-data-folder','setting-advanced-log-level'])assert(document.getElementById(id).checkVisibility(),'R3 L4 real advanced control '+id);
		 """)
		from selenium.webdriver.common.keys import Keys
		_harness.driver.execute_script("document.getElementById('settings-section-advanced').focus()")
		_harness.driver.switch_to.active_element.send_keys(Keys.TAB)
		self.assertEqual("setting-advanced-edge-path", _harness.driver.execute_script("return document.activeElement.id"))
		self.assertEqual([], _harness.errors())

	def test_r3_l5_same_schedule_time_across_three_screens(self):
		for lang in ("en", "de"):
			self.open("automatic", lang=lang)
			self.js("""
			 const saved=structuredClone(s.get('settings')),overview=structuredClone(s.get('overview'));
			 const generated=new Date();generated.setHours(12,0,0,0);const next=new Date(generated);next.setDate(next.getDate()+1);next.setHours(9,45,0,0);
			 saved.schedule={...saved.schedule,mode:'automatic',next_at:next.toISOString(),next_is_check:true,task_exists:true,in_sync:true,error_code:null};
			 overview.generated_at=generated.toISOString();overview.schedule=structuredClone(saved.schedule);
			 s.set('overview',overview);window.pywebview.api.get_settings=async()=>({ok:true,data:saved});window.pywebview.api.get_overview=async()=>({ok:true,data:overview});
			 await r.navigate('#/overview');await wait(()=>document.querySelector('.overview-next-link'));
			 window.r3ExpectedTime=document.querySelector('.overview-next-link').textContent;
			 await r.navigate('#/settings/schedule');await wait(()=>document.querySelector('.settings-schedule-status'));
			 """)
			if os.environ.get("RF_F76_R3_MUTATION") == "L5":
				self.js("const schedule=s.get('settings').schedule;document.querySelector('.settings-schedule-status').textContent='Next check around '+i.formatDate(schedule.next_at)+' '+i.formatTime(schedule.next_at);")
			self.js("""
			 assert(document.querySelector('.settings-schedule-status').textContent===window.r3ExpectedTime,'R3 L5 same next-check wording in Settings');
			 window.pywebview.api.get_legacy_install=async()=>({ok:true,data:{found:false,path:null,accounts:[]}});
			 await r.navigate('#/setup/3');await wait(()=>document.querySelector('[data-summary=next] dd'));
			 assert(document.querySelector('[data-summary=next] dd').textContent===window.r3ExpectedTime,'R3 L5 same next-check wording in Setup');
			 const shared=await import('./js/schedule-time.js'),ov=await import('./js/screens/overview.js');assert(shared.scheduleTime===ov.scheduleTime,'R3 L5 one exported formatter');
			 """)
			self.assertEqual([], _harness.errors())

	def open(self, name="automatic", section=None, theme="dark", lang="en"):
		_harness.open(f"#/settings/{section or FIXTURES[name]}", f"settings/{name}", theme, lang).wait_ready()
		from selenium.webdriver.support.ui import WebDriverWait
		WebDriverWait(_harness.driver, 10).until(lambda browser: browser.execute_script("return document.querySelector('#view [data-screen=settings]')?.getAttribute('aria-busy')==='false'"))

	def js(self, body):
		result = _harness.driver.execute_async_script("""
			const done=arguments[arguments.length-1];
			(async()=>{
				const assert=(ok,label)=>{if(!ok)throw Error(label)};
				const i=await import('./js/i18n.js');
				const r=await import('./js/router.js');
				const s=await import('./js/store.js');
				const wait=async check=>{for(let n=0;n<200;n++){if(check())return;await new Promise(resolve=>setTimeout(resolve,10));}throw Error('condition timed out')};
				const input=(id,value)=>{const field=document.getElementById(id);field.value=value;field.dispatchEvent(new Event('input',{bubbles:true}));return field};
			""" + body + """
				return {ok:true};
			})().then(done).catch(error=>done({ok:false,error:String(error),stack:error.stack}));
		""")
		self.assertTrue(result["ok"], result)

	def field_error(self):
		self.js(r"""
			input('setting-run-ollama-url','http://localhost:11434/${synthetic}');
			document.getElementById('settings-save').click();
			await wait(()=>document.querySelector('[data-setting-id="run.ollama_url"] [aria-invalid=true]'));
			const row=document.querySelector('[data-setting-id="run.ollama_url"]');
			const field=row.querySelector('input');
			const described=(field.getAttribute('aria-describedby')||'').split(/\s+/).map(id=>document.getElementById(id));
			const error=described.find(node=>node && node.textContent===i.t('errors.settings.dollar_brace'));
			assert(error && row.contains(error) && error.getClientRects().length,'mock field error is beside its input');
			assert(document.querySelector('.banner').textContent.includes(i.t('errors.app.settings_invalid')),'bridge summary localized');
			assert(document.activeElement===field,'focus lands on invalid field');
			assert(!document.querySelector('#view').textContent.includes('Synthetic validation failure'),'technical envelope not primary copy');
		""")

	def tabs(self):
		result = _harness.driver.execute_script("""
			const nodes=[...document.querySelectorAll('a[href],button,input,select,textarea,summary,[tabindex]')].filter(node=>node.getClientRects().length && !node.disabled && node.tabIndex>=0);
			for(let n=0;n<nodes.length;n++)if(!nodes[n].id)nodes[n].id='settings-tab-'+n;
			return {ids:nodes.filter(node=>node.closest('#view')).map(node=>node.id),count:nodes.length};
		""")
		order = _harness.tab_order(result["count"] + 12)
		self.assertTrue(set(result["ids"]).issubset(order), {"missing":sorted(set(result["ids"])-set(order)),"order":order})

	def test_field_error_and_dirty_cancel(self):
		self.open("field-errors")
		self.js("""
			const field=document.getElementById('setting-run-ollama-url');field.focus();
			field.value='http://localhost:11435/v1';field.dispatchEvent(new Event('input',{bubbles:true}));
			assert(document.activeElement===field && field.value==='http://localhost:11435/v1','URL editing keeps focus');
		""")
		self.field_error()
		self.js("""
			input('setting-run-ollama-url','http://localhost:11435/v1');
			assert(!document.querySelector('[data-setting-id="run.ollama_url"] [aria-invalid=true]'),'editing clears field error');
			document.getElementById('settings-cancel').click();
			assert(document.getElementById('setting-run-ollama-url').value==='http://localhost:11434/v1','cancel restores stored value');
			assert(document.getElementById('settings-save').disabled,'cancel clears dirty state');
		""")
		self.assertEqual([], _harness.errors())

	def test_all_offered_rows_schedule_types_and_no_hidden_controls(self):
		self.open()
		self.js("""
			assert(document.querySelector('.time-range-inputs'),'automatic time range');
			assert(!document.querySelector('[data-setting-id="schedule.checks"]') && !document.querySelector('[data-setting-id="schedule.min_points"]'),'no expert schedule knobs');
			const manual=document.querySelector('[data-value=manual]');manual.click();
			assert(!document.querySelector('.time-range-inputs') && !document.querySelector('.weekdays'),'manual has no timed controls');
			document.querySelector('[data-value=fixed]').click();
			assert(document.getElementById('setting-schedule-time') && document.querySelector('.weekdays'),'fixed time and days');
			document.querySelector('#settings-section-run').click();await wait(()=>document.querySelectorAll('.settings-task-options [role=switch]').length===7);
			assert(document.querySelector('.settings-panel').dataset.section==='run','same-module section route');
			const seen=new Set();
			for(const section of ['schedule','run','notifications','app','advanced']){
				await r.navigate('#/settings/'+section);await wait(()=>document.querySelector('.settings-panel').dataset.section===section);
				for(const row of document.querySelectorAll('[data-setting-id]'))seen.add(row.dataset.settingId);
				if(section==='run'){
					for(const value of ['ollama','openrouter']){const selector=document.getElementById('setting-run-search-source');selector.value=value;selector.dispatchEvent(new Event('change',{bubbles:true}));for(const row of document.querySelectorAll('[data-setting-id]'))seen.add(row.dataset.settingId);}
				}
			}
			const required=[...Object.keys(s.get('settings').values),...Object.keys(s.get('settings').secrets_set)].filter(id=>!['schedule.checks','schedule.min_points','schedule.window_start','schedule.window_end'].includes(id));
			assert(required.every(id=>seen.has(id)),'every offered table row: '+required.filter(id=>!seen.has(id)));
			assert(document.querySelector('#settings-advanced')&&!document.querySelector('#settings-advanced').matches('details'),'R3 advanced direct');
		""")
		self.assertEqual([], _harness.errors())

	def test_field_errors_for_time_switch_picker_and_secret(self):
		self.open()
		self.js("""
			window.pywebview.api.save_settings=async()=>({ok:false,error:{code:'settings.invalid',message_key:'errors.app.settings_invalid',params:{},detail:'synthetic',fields:{'schedule.window_end':'window_order'}}});
			const end=document.getElementById('setting-schedule-window-end');end.value='08:00';end.dispatchEvent(new Event('change',{bubbles:true}));
			document.getElementById('settings-save').click();await wait(()=>document.getElementById('setting-schedule-window-end').getAttribute('aria-invalid')==='true');
			assert(document.querySelector('[data-field-error="schedule.window_end"]').textContent===i.t('errors.settings.window_order'),'end-time error');
			assert(!document.getElementById('setting-schedule-window-start').hasAttribute('aria-invalid'),'start not falsely invalid');
			await r.navigate('#/settings/run');await wait(()=>document.querySelector('.settings-task-options'));
			window.pywebview.api.save_settings=async()=>({ok:false,error:{code:'settings.invalid',message_key:'errors.app.settings_invalid',params:{},detail:'synthetic',fields:{'run.tasks_off':'tasks_all_off'}}});
			document.querySelector('.settings-task-options [role=switch]').click();document.getElementById('settings-save').click();
			await wait(()=>document.querySelectorAll('.settings-task-options [aria-invalid=true]').length===7);
			assert(document.querySelector('[data-field-error="run.tasks_off"]').textContent===i.t('errors.settings.tasks_all_off'),'task-group error');
			await r.navigate('#/settings/advanced');await wait(()=>document.getElementById('settings-advanced'));
			window.pywebview.api.save_settings=async()=>({ok:false,error:{code:'settings.invalid',message_key:'errors.app.settings_invalid',params:{},detail:'synthetic',fields:{'advanced.edge_path':'file_wrong_name'}}});
			input('setting-advanced-edge-path','C:/Synthetic/not-edge.exe');document.getElementById('settings-save').click();
			await wait(()=>document.querySelector('[data-setting-id="advanced.edge_path"] [aria-invalid=true]'));
			assert(document.getElementById('setting-advanced-edge-path').checkVisibility(),'R3 advanced errors immediately visible');
			assert(document.querySelector('[data-setting-id="advanced.edge_path"]').textContent.includes('msedge.exe'),'file name recovery parameter');
			await r.navigate('#/settings/notifications');await wait(()=>document.getElementById('setting-notify-webhook-url'));
			window.pywebview.api.save_settings=async()=>({ok:false,error:{code:'settings.invalid',message_key:'errors.app.settings_invalid',params:{},detail:'synthetic',fields:{'notify.webhook_url':'https_required'}}});
			input('setting-notify-webhook-url','http://example.invalid/private');document.getElementById('settings-save').click();
			await wait(()=>document.querySelector('[data-setting-id="notify.webhook_url"] [aria-invalid=true]'));
			assert(document.querySelector('[data-setting-id="notify.webhook_url"]').textContent.includes(i.t('errors.settings.https_required')),'secret URL field error');
			assert(!document.getElementById('view').textContent.includes('example.invalid/private'),'secret not in copy');
		""")
		self.assertEqual([], _harness.errors())

	def test_secret_presence_replacement_remove_and_partial_save(self):
		self.open("openrouter")
		self.js("""
			const secretIds=['run.openrouter_key','notify.webhook_url'];
			const assertRedacted=async()=>{
				const response=await window.pywebview.api.get_settings({});
				for(const data of [response.data,s.get('settings')]){
					assert(secretIds.every(id=>!Object.hasOwn(data.values,id)),'secret values absent from raw response and page state');
					assert(secretIds.every(id=>typeof data.secrets_set[id]==='boolean'),'only secret-presence flags');
					assert(!JSON.stringify(data).includes('synthetic-new-key-for-ui'),'new secret not in response or store after save');
				}
				assert([...document.querySelectorAll('#view input')].every(field=>!field.value.includes('synthetic-new-key-for-ui')),'new secret not in DOM after save');
				assert(!document.documentElement.outerHTML.includes('synthetic-new-key-for-ui'),'secret not in attributes or text');
			};
			await assertRedacted();
			const key=document.getElementById('setting-run-openrouter-key');
			assert(key.type==='password' && key.value==='' && key.placeholder===i.t('settings.secret.saved'),'saved key empty and presence placeholder');
			let requestKeys=[],removed=false;const original=window.pywebview.api.save_settings;
			window.pywebview.api.save_settings=async request=>{requestKeys=Object.keys(request.changes);removed=requestKeys.every(id=>request.changes[id]==='');return original(request)};
			const submit=async()=>{
				const previous=s.get('settings');document.getElementById('settings-save').click();
				await wait(()=>s.get('settings')!==previous && !document.querySelector('.settings-form').disabled);
			};
			await r.navigate('#/settings/notifications');await wait(()=>document.getElementById('setting-notify-webhook-url'));
			const webhook=document.getElementById('setting-notify-webhook-url');assert(webhook.type==='password' && webhook.value==='','stored webhook not shown');
			assert(webhook.placeholder===i.t('settings.secret.saved'),'saved webhook presence');
			const selector=document.getElementById('setting-notify-policy');selector.value='errors';selector.dispatchEvent(new Event('change',{bubbles:true}));
			await submit();
			assert(JSON.stringify(requestKeys)===JSON.stringify(['notify.policy']),'untouched secret fields never sent');
			assert(document.querySelector('.banner').textContent.includes(i.t('settings.saved')),'save confirmation');
			await assertRedacted();
			await r.navigate('#/settings/run');await wait(()=>document.getElementById('setting-run-openrouter-key'));
			input('setting-run-openrouter-key','synthetic-new-key-for-ui');await submit();
			assert(JSON.stringify(requestKeys)===JSON.stringify(['run.openrouter_key']),'replacement sends only edited key');
			assert(document.getElementById('setting-run-openrouter-key').placeholder===i.t('settings.secret.saved'),'successful replacement only exposes saved flag');
			await assertRedacted();
			await r.navigate('#/settings/notifications');await wait(()=>document.getElementById('setting-notify-webhook-url'));
			const remove=document.querySelector('[data-remove-secret="notify.webhook_url"]');assert(remove.textContent===i.t('settings.secret.remove'),'Remove action copy');remove.click();
			await submit();
			assert(removed && requestKeys.length===1 && requestKeys[0]==='notify.webhook_url','Remove sends only empty string');
			assert(!s.get('settings').secrets_set['notify.webhook_url'],'webhook presence cleared');
			assert(document.getElementById('setting-notify-webhook-url').placeholder===i.t('settings.secret.empty'),'removed placeholder empty');
			await assertRedacted();
			await r.navigate('#/settings/run');await wait(()=>document.getElementById('setting-run-openrouter-key'));
			input('setting-run-openrouter-key','synthetic-invalid');document.getElementById('settings-save').click();
			await wait(()=>document.querySelector('[data-setting-id="run.openrouter_key"] [aria-invalid=true]'));
			assert(document.querySelector('[data-setting-id="run.openrouter_key"]').textContent.includes(i.t('errors.settings.dollar_brace')),'new secret validation still adjacent');
			assert(document.getElementById('setting-run-openrouter-key').value==='synthetic-invalid','failed save preserves typed draft');
			document.getElementById('settings-cancel').click();await assertRedacted();
			const keyRemove=document.querySelector('[data-remove-secret="run.openrouter_key"]');keyRemove.click();await submit();
			assert(removed && requestKeys.length===1 && requestKeys[0]==='run.openrouter_key','key Remove sends empty string');
			assert(!s.get('settings').secrets_set['run.openrouter_key'],'key presence cleared');await assertRedacted();
		""")
		self.assertEqual([], _harness.errors())

	def test_browser_mode_help_tracks_selected_mode(self):
		self.open("automatic", "run")
		self.js("""
			const offered=[...document.querySelectorAll('#setting-run-browser-mode [data-value]')].map(node=>node.dataset.value);
			assert(JSON.stringify(offered)===JSON.stringify(['visible','hidden']),'only visible and background values offered');
			for(const mode of ['visible','hidden']){
				const control=document.querySelector('#setting-run-browser-mode [data-value="'+mode+'"]');control.click();
				const help=document.getElementById('setting-run-browser-mode-help');
				assert(help.textContent===i.t('settings.run.browser.'+mode+'_help'),'mode-specific consequence '+mode);
				assert(document.querySelector('#setting-run-browser-mode [role=radiogroup]').getAttribute('aria-describedby')===help.id,'help connected to mode group');
			}
			assert(!document.querySelector('#setting-run-browser-mode [data-value=desktop]'),'no virtual desktop mode');
		""")
		self.assertEqual([], _harness.errors())

	def test_f64_unknown_browser_values_render_as_visible(self):
		self.open("automatic", "run")
		self.js("""
			const original=window.pywebview.api.get_settings;
			for(const value of ['desktop','future-mode',null,undefined]){
				window.pywebview.api.get_settings=async request=>{const answer=await original(request);const data=structuredClone(answer.data);if(value===undefined)delete data.values['run.browser_mode'];else data.values['run.browser_mode']=value;return {ok:true,data}};
				const before=s.get('settings');window.rf.receive([{topic:'settings.changed',data:{}}]);
				await wait(()=>s.get('settings')!==before && document.querySelector('.settings-form') && !document.querySelector('.settings-form').disabled);
				assert(document.querySelector('#setting-run-browser-mode [data-value=visible]').getAttribute('aria-checked')==='true','unsupported mode renders visible');
				assert(document.getElementById('setting-run-browser-mode-help').textContent===i.t('settings.run.browser.visible_help'),'unsupported mode has visible help');
				assert(!document.querySelector('#view').textContent.includes('settings.run.browser.') && !document.querySelector('#view').textContent.includes('future-mode'),'no raw key or unsupported value');
			}
			window.pywebview.api.get_settings=original;
		""")
		self.assertEqual([], _harness.errors())

	def test_f64_run_captures_at_1100_in_english_and_german(self):
		_harness.viewport(1100, 760)
		for lang in ("en", "de"):
			with self.subTest(lang=lang):
				self.open("automatic", "run", "dark", lang)
				self.js("assert(document.querySelectorAll('#setting-run-browser-mode [data-value]').length===2,'two modes at operator window size');")
				self.assertEqual([], _harness.errors())
				_harness.screenshot(f"settings-run-f64-dark-{lang}-1100x760")
		_harness.viewport(1280, 800)

	def test_shared_column_edges_and_requested_captures(self):
		measurements = []
		try:
			for width, height in ((860, 600), (1100, 760), (1440, 900), (1920, 1080)):
				_harness.viewport(width, height)
				languages = ("en", "x-rtl", "de") if width == 1100 else ("en", "x-rtl")
				for lang in languages:
					self.open("manual", "schedule", "dark", lang)
					self.js("await r.navigate('#/overview');await wait(()=>document.querySelector('#view .overview-screen h1'));")
					overview_heading = _harness.driver.execute_script("const box=document.querySelector('#view .overview-screen h1').getBoundingClientRect(),view=document.getElementById('view');return {left:box.left,right:box.right,viewClientWidth:view.clientWidth,viewOffsetWidth:view.offsetWidth};")
					for section in SECTIONS:
						with self.subTest(width=width, height=height, lang=lang, section=section):
							self.js("await r.navigate('#/settings/" + section + "');await wait(()=>document.querySelector('#view .settings-screen')?.getAttribute('aria-busy')==='false');")
							edges = _harness.driver.execute_script("""
								const root=document.querySelector('#view .settings-screen');
								const measure=selector=>{const node=root.querySelector(selector), box=node.getBoundingClientRect();return {left:box.left,right:box.right,width:box.width}};
								const view=document.getElementById('view');
								return {heading:measure('.screen-header'),headingText:measure('.screen-header h1'),tabs:measure('.settings-sections'),panel:measure('.settings-panel'),footer:measure('.settings-footer'),dir:document.documentElement.dir,overflow:view.scrollWidth>view.clientWidth+1,viewClientWidth:view.clientWidth,viewOffsetWidth:view.offsetWidth};
							""")
							leading = "right" if edges["dir"] == "rtl" else "left"
							row = {"viewport":[width,height],"lang":lang,"section":section,"edges":edges,"overviewHeading":overview_heading,"leading":{"settings":edges["headingText"][leading],"overview":overview_heading[leading]}}
							measurements.append(row)
							print("Settings shared edges " + json.dumps(row, sort_keys=True), flush=True)
							capture = section == "schedule" and ((width, lang) in ((1920, "en"), (1440, "en"), (1100, "de")))
							if capture:
								_harness.driver.execute_script("document.getElementById('view').scrollTop=0")
								_harness.screenshot(f"settings-edges-dark-{lang}-{width}x{height}")
							self.assertEqual([], _harness.errors())
							self.assertEqual("rtl" if lang == "x-rtl" else "ltr", edges["dir"])
							self.assertFalse(edges["overflow"], row)
							for name in ("heading", "tabs", "footer"):
								self.assertLessEqual(abs(edges[name]["left"] - edges["panel"]["left"]), 1, row)
								self.assertLessEqual(abs(edges[name]["right"] - edges["panel"]["right"]), 1, row)
							self.assertLessEqual(edges["panel"]["width"], 821, row)
							if width in (1440, 1920):
								self.assertLessEqual(abs(row["leading"]["settings"] - row["leading"]["overview"]), 1, row)
		finally:
			print(f"Settings shared-edge cases={len(measurements)}", flush=True)
			_harness.viewport(1280, 800)

	def test_legacy_keep_and_confirmed_replace(self):
		self.open("legacy")
		self.js("""
			let calls=0;const original=window.pywebview.api.replace_legacy_task;window.pywebview.api.replace_legacy_task=async request=>{calls++;return original(request)};
			const keep=[...document.querySelectorAll('button')].find(node=>node.textContent===i.t('settings.schedule.keep'));keep.click();
			assert(calls===0 && !document.querySelector('.settings-panel .banner'),'keep leaves both tasks alone');
		""")
		# Same-URL navigation can retain the dismissed notice in the screen module.
		# These are independent user choices, so reset the mock document explicitly.
		_harness.driver.refresh()
		_harness.wait_ready()
		self.js("""
			let calls=0;const original=window.pywebview.api.replace_legacy_task;window.pywebview.api.replace_legacy_task=async request=>{calls++;return original(request)};
			const replace=[...document.querySelectorAll('#view button')].find(node=>node.textContent===i.t('settings.schedule.replace'));
			const dialog=document.getElementById('dialog');assert(!document.getElementById('view').contains(dialog),'body-level dialog');
			const dialogButton=key=>[...dialog.querySelectorAll('button')].find(node=>node.textContent===i.t(key));
			const closed=()=>new Promise(resolve=>dialog.addEventListener('close',()=>requestAnimationFrame(resolve),{once:true}));
			assert(replace,'explicit refresh restores the legacy notice');
			replace.focus();replace.click();
			await wait(()=>dialog.open && dialogButton('common.action.cancel'));assert(calls===0,'confirmation before effect');
			const cancelSettled=closed();dialogButton('common.action.cancel').click();await cancelSettled;
			assert(!dialog.open && calls===0,'settled cancel has no effect');
			assert(document.activeElement===replace,'cancel returns focus to opener');
			replace.click();await wait(()=>dialog.open && dialogButton('settings.schedule.replace'));
			const replaceSettled=closed();dialogButton('settings.schedule.replace').click();await replaceSettled;
			await wait(()=>calls===1 && !document.querySelector('.settings-panel .banner'));
			assert(document.querySelector('.banner').textContent.includes(i.t('settings.schedule.replaced')),'confirmed legacy replacement');
		""")
		self.assertEqual([], _harness.errors())

	def test_paths_models_updates_export_and_external_change(self):
		self.open("automatic", "advanced")
		self.js(r"""
			assert(document.getElementById('setting-advanced-edge-path').checkVisibility(),'R3 advanced fields directly shown');
			const picked=[];window.pywebview.api.pick_path=async request=>{picked.push(request.kind);return {ok:true,data:{path:request.kind==='data_folder'?'C:\\Synthetic\\Profiles':'C:\\Synthetic\\'+(request.kind==='edge'?'msedge.exe':'msedgedriver.exe')}}};
			for(const id of ['advanced.edge_path','advanced.driver_path','advanced.data_folder'])document.querySelector('[data-setting-id="'+id+'"] button').click();
			await wait(()=>document.getElementById('setting-advanced-data-folder').value==='C:\\Synthetic\\Profiles');
			assert(JSON.stringify(picked)===JSON.stringify(['edge','driver','data_folder']),'all picker kinds');
			[...document.querySelectorAll('button')].find(node=>node.textContent===i.t('settings.advanced.export.label')).click();
			await wait(()=>document.querySelector('.banner')?.textContent.includes('diagnostics.zip'));
			await r.navigate('#/settings/app');await wait(()=>document.getElementById('setting-app-language'));
			assert([...document.getElementById('setting-app-language').options].every(node=>!node.value.startsWith('x-')),'pseudo languages not offered');
			[...document.querySelectorAll('button')].find(node=>node.textContent===i.t('settings.app.updates.check')).click();
			await wait(()=>document.querySelector('.banner')?.textContent.includes(i.t('settings.app.updates.current')));
		""")
		self.open("ollama")
		self.js("""
			[...document.querySelectorAll('button')].find(node=>node.textContent===i.t('settings.run.models.refresh')).click();
			await wait(()=>document.getElementById('setting-run-ollama-model').options.length===2);
			input('setting-run-ollama-url','http://localhost:11435/v1');
			window.rf.receive([{topic:'settings.changed',data:{}}]);await wait(()=>document.querySelector('.banner'));
			assert(document.querySelector('.banner').textContent.includes(i.t('settings.external_change')),'external-edit notice');
			assert(document.getElementById('setting-run-ollama-url').value==='http://localhost:11435/v1','edits kept');
		""")
		self.assertEqual([], _harness.errors())

	def test_z_fixture_section_theme_language_viewport_matrix(self):
		failures = []
		captures = 0
		cases = [(name, section) for name, section in FIXTURES.items()] + [("automatic", section) for section in SECTIONS if section != "schedule"]
		for width, height in ((1280, 800), (860, 600)):
			_harness.viewport(width, height)
			for name, section in cases:
				for theme in ("dark", "light"):
					for lang in ("en", "x-long", "x-rtl"):
						with self.subTest(fixture=name, section=section, theme=theme, lang=lang, width=width):
							self.open(name, section, theme, lang)
							if name == "field-errors":
								self.field_error()
							self.tabs()
							_harness.driver.execute_script("document.getElementById('view').scrollTop=0")
							state = _harness.driver.execute_script("const view=document.getElementById('view');return {root:document.documentElement.scrollWidth>innerWidth+1,view:view.scrollWidth>view.clientWidth+1,dir:document.documentElement.dir,theme:document.documentElement.dataset.theme}")
							errors = _harness.errors()
							if state["root"] or state["view"] or errors:
								failures.append({"fixture":name,"section":section,"theme":theme,"lang":lang,"width":width,"state":state,"errors":errors})
							self.assertEqual("rtl" if lang == "x-rtl" else "ltr", state["dir"])
							self.assertEqual(theme, state["theme"])
							self.js("assert(document.title===i.t('settings.title'),'settings document title');assert(!document.querySelector('[style]'),'no inline styles');")
							if width == 1280:
								fixture_name = name if section == FIXTURES[name] else f"{name}-{section}"
								_harness.screenshot(f"settings-{fixture_name}-{theme}-{lang}")
								captures += 1
		print(f"Settings captures={captures}; layout/browser failures={len(failures)}; signed_in={_harness.signed_in}; inprivate={_harness.inprivate}", flush=True)
		self.assertEqual([], failures)
