import _bootstrap  # noqa: F401

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.harness import SCREENSHOT_DIR, UI_DIR, UiHarness
from rfapp import signin


_enabled = os.environ.get("REWARDS_APP_UI_TESTS") == "1"
_harness = None
FIXTURES = sorted(path.stem for path in (UI_DIR / "mock/accounts").glob("*.json"))


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
	def test_catalog_synthetic_fixtures_and_shared_input(self):
		catalog = json.loads((UI_DIR / "i18n/en/accounts.json").read_text(encoding="utf-8"))
		self.assertEqual("accounts", catalog["namespace"])
		for key, entry in catalog["messages"].items():
			self.assertTrue(entry["note"].strip(), key)
			self.assertNotRegex(json.dumps(entry, ensure_ascii=False), "[\u2013\u2014]")
		self.assertEqual(14, len(FIXTURES))
		for fixture in FIXTURES:
			data = json.loads((UI_DIR / "mock/accounts" / (fixture + ".json")).read_text(encoding="utf-8"))
			self.assertTrue(data["synthetic"])
			self.assertIsInstance(data["methods"], dict)
		text = (UI_DIR / "js/screens/accounts.js").read_text(encoding="utf-8")
		self.assertIn("c.textField.create", text)
		self.assertIn("maxLength: 40", text)
		self.assertIn("errors.app.accounts_add_unsupported", text)
		self.assertNotIn('el("input"', text)
		self.assertNotIn(".style", text)
		self.assertNotRegex(text, "[\u2013\u2014]")
		css = (UI_DIR / "css/screens/accounts.css").read_text(encoding="utf-8")
		self.assertNotRegex(css, r"(?m)(?:^|[;{])\s*(?:width|height|left|right|top|bottom|margin-left|padding-right)\s*:")


@unittest.skipUnless(_enabled, "Set REWARDS_APP_UI_TESTS=1 for isolated Accounts UI tests")
class AccountsTests(unittest.TestCase):
	def test_r4_l10_secondary_action_style_and_keyboard(self):
		from selenium.webdriver.common.keys import Keys
		for lang in ("en", "de", "ar"):
			_harness.viewport(1131, 643)
			self.open("list", lang=lang)
			self.js("document.getElementById('accounts-more-account-1').open=true;await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)))")
			if os.environ.get("RF_F76_R4_MUTATION") == "L10":
				_harness.driver.execute_script("document.getElementById('accounts-remove-account-1').classList.remove('button-ghost')")
			self.js("""
			 const buttons=['rename','pause','remove'].map(action=>document.getElementById('accounts-'+action+'-account-1'));
			 const keys=['backgroundColor','borderTopColor','borderTopWidth','borderTopStyle','borderRadius','paddingBlockStart','paddingInlineStart','fontSize','fontWeight','lineHeight','boxShadow'];
			 const signature=node=>keys.map(key=>getComputedStyle(node)[key]),first=JSON.stringify(signature(buttons[0]));
			 assert(buttons.every(node=>node.classList.contains('button-ghost')&&JSON.stringify(signature(node))===first),'R4 L10 all secondary actions share ghost style');
			 assert(getComputedStyle(buttons[2]).color===getComputedStyle(document.documentElement).getPropertyValue('--c-danger').trim()||buttons[2].classList.contains('button-danger'),'R4 L10 Remove retains danger tone');
			 assert(getComputedStyle(buttons[0]).color===getComputedStyle(buttons[1]).color&&getComputedStyle(buttons[2]).color!==getComputedStyle(buttons[0]).color,'R4 L10 color is the destructive difference');
			 document.getElementById('accounts-more-toggle-account-1').focus();
			 """)
			for action in ("rename", "pause", "remove"):
				_harness.driver.switch_to.active_element.send_keys(Keys.TAB)
				self.assertEqual("accounts-" + action + "-account-1", _harness.driver.switch_to.active_element.get_attribute("id"))
			_harness.driver.switch_to.active_element.send_keys(Keys.ENTER)
			self.js("await wait(()=>document.getElementById('dialog').open);assert(document.querySelectorAll('#dialog .button-danger').length===2,'R4 L10 both original removal confirmation choices retained')")
			_harness.driver.switch_to.active_element.send_keys(Keys.ESCAPE)
			self.js("await wait(()=>!document.getElementById('dialog').open);await new Promise(done=>requestAnimationFrame(done))")
			self.assertEqual("accounts-remove-account-1", _harness.driver.switch_to.active_element.get_attribute("id"), "R4 L10 cancellation restores Remove focus")
			self.assertEqual([], _harness.errors())

	def test_r3_l2_disclosure_geometry_and_native_keyboard(self):
		from selenium.webdriver.common.keys import Keys
		mutation = os.environ.get("RF_F76_R3_MUTATION") == "L2"
		for lang in ("en", "de"):
			for width, height in ((1131, 643), (860, 600), (1280, 800)):
				_harness.viewport(width, height)
				self.open("list", lang=lang)
				if mutation:
					_harness.driver.execute_cdp_cmd("DOM.enable", {})
					_harness.driver.execute_cdp_cmd("CSS.enable", {})
					frame = _harness.driver.execute_cdp_cmd("Page.getFrameTree", {})["frameTree"]["frame"]["id"]
					sheet = _harness.driver.execute_cdp_cmd("CSS.createStyleSheet", {"frameId": frame})["styleSheetId"]
					_harness.driver.execute_cdp_cmd("CSS.setStyleSheetText", {"styleSheetId": sheet, "text": ".accounts-entry .accounts-disclosure>summary{padding-block:24px!important}.accounts-screen .account-row .control-actions{align-items:center!important}"})
				self.js("""
				 const entry=document.querySelector('.accounts-entry'),details=entry.querySelector('details'),summary=details.querySelector('summary'),signin=entry.querySelector('[id^=accounts-signin-]');
				 const box=n=>{const r=n.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height,b:r.bottom}};
				 const first={summary:box(summary),signin:box(signin)};
				 assert(Math.abs(first.summary.h-first.signin.h)<1&&Math.abs(first.summary.y-first.signin.y)<1,'R3 L2 controls same height and top');
				 details.open=true;await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)));
				 const opened={summary:box(summary),signin:box(signin)};
				 assert(Math.abs(opened.summary.w-first.summary.w)<1&&Math.abs(opened.signin.y-first.signin.y)<1&&Math.abs(opened.signin.x-first.signin.x)<1,'R3 L2 opening preserves width and Sign in position');
				 assert([...details.querySelectorAll('button')].every(n=>box(n).y>=Math.max(opened.summary.b,opened.signin.b)),'R3 L2 actions below control row');
				 details.open=false;summary.focus();
				 """)
				_harness.driver.switch_to.active_element.send_keys(Keys.ENTER)
				self.assertTrue(_harness.driver.execute_script("return document.querySelector('.accounts-entry details').open"))
				_harness.driver.switch_to.active_element.send_keys(Keys.TAB)
				self.assertEqual("accounts-rename-account-1", _harness.driver.execute_script("return document.activeElement.id"))
				_harness.driver.execute_script("document.querySelector('.accounts-entry summary').focus()")
				_harness.driver.switch_to.active_element.send_keys(Keys.SPACE)
				self.assertFalse(_harness.driver.execute_script("return document.querySelector('.accounts-entry details').open"))
				self.assertEqual([], _harness.errors())

	def js(self, body):
		result = _harness.driver.execute_async_script("""
			const done=arguments[arguments.length-1];
			(async()=>{
				const assert=(ok,label)=>{if(!ok)throw Error(label)};
				const wait=async test=>{for(let n=0;n<100;n++){if(test())return;await new Promise(r=>setTimeout(r,10))}throw Error('condition timeout')};
				const c=await import('./js/components/index.js');const i=await import('./js/i18n.js');const s=await import('./js/store.js');
			""" + body + """
				return {ok:true};
			})().then(done).catch(e=>done({ok:false,error:String(e),stack:e.stack}));
		""")
		self.assertTrue(result["ok"], result)

	def open(self, fixture, theme="dark", lang="en"):
		url = _harness.url("#/accounts", "accounts/" + fixture, theme, lang)
		same_url = _harness.driver.current_url == url
		_harness.open("#/accounts", "accounts/" + fixture, theme, lang).wait_ready()
		if same_url:
			print("accounts same-URL fixture before explicit refresh: " + str(_harness.driver.execute_script("return [...document.querySelectorAll('#view [data-account]')].map(n=>n.dataset.account)")), flush=True)
			# Reopening a mutated fixture must replace its page, not only its fragment.
			_harness.driver.refresh()
			_harness.wait_ready()
		if fixture in ("signin", "signin-failed"):
			from selenium.webdriver.support.ui import WebDriverWait
			expected = "waiting" if fixture == "signin" else "failed"
			WebDriverWait(_harness.driver, 5).until(lambda driver: driver.execute_script("return document.querySelector('[data-signin-state]')?.dataset.signinState") == expected)

	def test_footer_and_account_row_share_identity_initial_priority(self):
		self.open("list")
		self.js("""
			const {accountInitial}=await import('./js/components/kit.js');
			const host=document.createElement('section');document.querySelector('#view').append(host);
			const cases=[
				{label:'Label',name:'  Terry Example ',email:'chris@example.com',initial:'T'},
				{label:'Label',name:'  ',email:' chris@example.com ',initial:'C'},
				{label:' Sample account ',name:null,email:null,initial:'S'},
				{label:'fallback',name:String.fromCodePoint(0x10428)+' Sample',email:'mail@example.com',initial:String.fromCodePoint(0x10400)},
				{label:'  ',name:null,email:' ',initial:''},
			];
			for(const sample of cases){
				const account={...s.get('accounts')[0],...sample};delete account.initial;
				s.set('accounts',[account]);
				const footer=document.querySelector('.nav-account .avatar');
				const component=c.accountRow.create({account});host.replaceChildren(component);
				const row=component.querySelector('.avatar');
				assert(accountInitial(account)===sample.initial,'helper expected identity initial');
				assert(footer.textContent===sample.initial && row.textContent===sample.initial,'footer and row share helper priority');
			}
			assert(accountInitial(null)==='' && accountInitial(undefined)==='','no account is empty');
			s.set('accounts',[]);assert(document.querySelector('.nav-account .avatar').textContent==='','empty footer uses helper');
		""")
		self.assertEqual([], _harness.errors())

	def test_f68_account_rows_use_synthetic_picture_and_keep_initial_fallback(self):
		_harness.viewport(1131, 643)
		_harness.open("#/accounts", "pictures/accounts").wait_ready()
		self.js("""
			await wait(()=>document.querySelector('[data-account="account-1"] .avatar img')?.naturalWidth>0);
			const first=document.querySelector('[data-account="account-1"] .avatar'),second=document.querySelector('[data-account="account-2"] .avatar'),image=first.querySelector('img');
			assert(image.alt==='' && image.getAttribute('src').startsWith('data:image/png;base64,') && image.naturalWidth<=128 && image.naturalHeight<=128,'F68 decorative local PNG');
			assert(!second.querySelector('img') && second.textContent==='C','F68 no picture initial');
			assert(document.querySelector('.nav-account .avatar img').getAttribute('src')===image.getAttribute('src'),'row and sidebar share picture');
			const account={...s.get('accounts')[0],picture_rev:null};const plain=c.accountRow.create({account});document.querySelector('#view').append(plain);
			assert(plain.querySelector('.avatar').textContent==='T' && !plain.querySelector('img'),'F68 initial retained for null rev');
			const before=first.getBoundingClientRect(),after=plain.querySelector('.avatar').getBoundingClientRect();assert(Math.abs(before.width-after.width)<1 && Math.abs(before.height-after.height)<1,'F68 avatar dimensions unchanged');
		""")
		self.assertEqual([], _harness.errors())

	def test_signin_cancel_all_states_and_foreign_push(self):
		self.open("signin")
		self.js("""
			let req;window.pywebview.api.cancel_signin=async value=>{req=value;return {ok:true,data:{}}};
			document.getElementById('accounts-cancel-signin').click();await wait(()=>req);assert(req.account_id==='jamie','cancel id');
			assert(document.querySelector('[data-signin-state]').dataset.signinState==='waiting','request not terminal');
			assert(document.querySelectorAll('.accounts-signin .banner').length===1 && document.querySelector('.accounts-signin .banner-message').textContent===i.t('accounts.signin.cancelling'),'F58 cancellation wins');
			window.rf.receive([{topic:'signin.status',data:{account_id:'jamie',state:'busy',error_code:null,message_key:'accounts.signin.busy',detail:null,at:'2026-10-14T20:00:00Z'}}]);
			window.rf.receive([{topic:'signin.status',data:{account_id:'foreign',state:'signed_in',error_code:null,message_key:'accounts.signin.signed_in',detail:null,at:'2026-10-14T20:00:00Z'}}]);
			assert(document.querySelector('[data-signin-state]').dataset.signinState==='busy','foreign push ignored');
			assert(document.getElementById('view').textContent.includes(i.t('errors.app.accounts_add_unsupported')),'no add when configured');
		""")
		self.assertEqual([], _harness.errors())

	def test_producer_statuses_have_one_banner_and_account_identity(self):
		with tempfile.TemporaryDirectory(prefix="accounts-status-proof-") as directory, patch.object(signin.paths, "runs_dir", return_value=Path(directory)):
			statuses = [signin._write_status("account-1", state) for state in ("starting", "waiting", "verifying", "signed_in", "failed", "timed_out", "cancelled", "busy")]
			statuses.extend(signin._write_status("account-1", state, code) for state, code in (("failed", "edge_missing"), ("failed", "crashed"), ("signed_in", "terms_of_use")))
		_harness.viewport(1100, 760)
		for theme, lang in (("dark", "en"), ("light", "en"), ("dark", "de")):
			self.open("list", theme, lang)
			self.js("""
				const statuses=""" + json.dumps(statuses) + """;
				for(const status of statuses){
					window.rf.receive([{topic:'signin.status',data:status}]);
					const panel=document.querySelector('.accounts-signin');const banners=panel.querySelectorAll('.banner');
					assert(banners.length===1,'F58 Accounts one status banner');
					assert(banners[0].querySelector('.banner-message').textContent===i.t(status.message_key,{account:'Alex (synthetic)'}),'F58 producer text');
					const tone=status.error_code==='terms_of_use'?'warning':status.state==='signed_in'?'success':['failed','busy','timed_out'].includes(status.state)?'warning':'info';
					assert(banners[0].classList.contains('tone-'+tone),'F58 state tone');
					assert(Boolean(panel.querySelector('.spinner'))===['starting','waiting','verifying'].includes(status.state),'spinner preserved');
				}
				window.rf.receive([{topic:'signin.status',data:{...statuses[0],message_key:null}}]);
				assert(document.querySelectorAll('.accounts-signin .banner').length===1 && document.querySelector('.accounts-signin .banner-message').textContent===i.t('accounts.signin.starting'),'absent message fallback');
			""")
			self.open("list", theme, lang)
			self.js("""
				const first=document.querySelector('[data-account="account-1"]');const second=document.querySelector('[data-account="account-2"]');const third=document.querySelector('[data-account="account-3"]');
				assert(first.querySelector('.account-row-email bdi').textContent==='alex@example.com' && first.querySelector('.account-row-email bdi').dir==='auto','F59 identity with isolation');
				assert(first.querySelector('.avatar').textContent==='T' && second.querySelector('.avatar').textContent==='C' && third.querySelector('.avatar').textContent==='S','F59 name email label avatar priority');
				assert(!third.querySelector('.account-row-email'),'unknown identity has no invented address');
				assert(!second.querySelector('.account-row-balance'),'F76 unknown points omitted');
				assert(first.querySelector('.account-row-balance').textContent===i.t('common.points.total',{count:4200}),'known points unchanged');
				assert(document.documentElement.scrollWidth<=innerWidth+1,'no row overflow');
			""")
			self.assertTrue(Path(_harness.screenshot(f"accounts-f59-identity-{theme}-{lang}-1100x760")).is_file())
			self.assertEqual([], _harness.errors())

	def test_rename_pause_resume_and_label_safety(self):
		self.open("list")
		self.js("""
			const add=document.getElementById('accounts-add-another');assert(add.disabled && add.getAttribute('aria-describedby')==='accounts-add-reason','B2 disabled add with reason');
			assert(add.closest('details')===document.querySelector('.accounts-list').lastElementChild,'F76 add limit folded after accounts');
			assert(getComputedStyle(document.getElementById('accounts-add-reason')).fontSize==='14px','B2 body-size reason');
			const header=document.querySelector('.accounts-screen .screen-header');assert(header.children.length===1 && header.querySelector('h1') && !header.querySelector('p'),'F76 heading only');
			assert(!document.querySelector('.accounts-preview'),'F76 preview caption removed');
			assert(document.querySelector('[data-account="account-2"]').textContent.includes(i.t('common.account.signed_out')) && i.t('common.account.signed_out')==='Signed out','B5 status is not action');
			let rename;window.pywebview.api.rename_account=async req=>{rename=req;const a=s.get('accounts').find(a=>a.id===req.account_id);return {ok:true,data:{account:{...a,label:req.label}}}};
			const more=document.getElementById('accounts-more-account-1');assert(!more.open,'F76 secondary actions initially folded');more.querySelector('summary').click();assert(more.open&&more.querySelector('summary .icon'),'F76 same chevron disclosure');
			document.getElementById('accounts-rename-account-1').click();assert(document.activeElement.id==='accounts-name','inline focus');
			const input=document.getElementById('accounts-name');assert(input.maxLength===40 && input.labels.length,'shared labeled field');
			input.value='<script>synthetic</script>';input.dispatchEvent(new Event('input',{bubbles:true}));
			document.getElementById('accounts-save-name').click();await wait(()=>rename && !document.getElementById('accounts-name'));
			assert(rename.account_id==='account-1' && rename.label==='<script>synthetic</script>','rename payload');
			assert(!document.querySelector('#view script') && document.getElementById('view').textContent.includes(rename.label),'label stays text');
			let pause;window.pywebview.api.set_account_paused=async req=>{pause=req;const a=s.get('accounts').find(a=>a.id===req.account_id);return {ok:true,data:{account:{...a,paused:req.paused}}}};
			document.getElementById('accounts-pause-account-1').click();await wait(()=>pause && s.get('accounts')[0].paused);
			assert(pause.paused===true,'pause');document.getElementById('accounts-pause-account-1').click();await wait(()=>!s.get('accounts')[0].paused);assert(pause.paused===false,'resume');
		""")
		self.assertEqual([], _harness.errors())

	def test_f76_secondary_actions_are_folded_but_keyboard_reachable(self):
		self.open("list")
		self.js("""
		 assert(!document.querySelector('.accounts-intro,.accounts-preview'),'F76 no framing paragraphs');
		 const rows=[...document.querySelectorAll('.accounts-entry')];assert(rows.length===s.get('accounts').length,'F76 every account retained');
		 for(const entry of rows){
		  const more=entry.querySelector('.accounts-disclosure'),summary=more.querySelector('summary');
		  assert(!more.open&&summary.querySelector('.icon'),'F76 one closed chevron for secondary actions');
		  assert(entry.querySelector('.account-row button').textContent===i.t('accounts.action.signin_again'),'F76 sign in remains immediate');
		  summary.click();assert(more.open&&more.querySelectorAll('button').length>=2,'F76 rename pause remove still reachable');
		  const id=entry.querySelector('.account-row').dataset.account,account=s.get('accounts').find(a=>a.id===id);
		  if(account.balance===null)assert(!entry.querySelector('.account-row-balance'),'F76 no unknown number label');
		 }
		 const limit=document.querySelector('.accounts-list > details');assert(limit&&!limit.open,'F76 add limitation folded');
		 limit.querySelector('summary').click();assert(document.getElementById('accounts-add-another').disabled&&limit.textContent.includes(i.t('errors.app.accounts_add_unsupported')),'F76 unsupported action remains explained');
		 """)
		self.assertEqual([], _harness.errors())

	def test_f76_more_actions_native_keyboard_and_focus(self):
		from selenium.webdriver.common.keys import Keys
		self.open("list")
		summary=_harness.driver.find_element("css selector",".accounts-entry .accounts-disclosure > summary")
		summary.click()
		summary.send_keys(Keys.ENTER)
		self.assertFalse(_harness.driver.execute_script("return arguments[0].parentElement.open",summary))
		summary.send_keys(Keys.SPACE)
		self.assertTrue(_harness.driver.execute_script("return arguments[0].parentElement.open",summary))
		summary.send_keys(Keys.TAB)
		self.assertEqual("accounts-rename-account-1",_harness.driver.execute_script("return document.activeElement.id"))
		_harness.driver.switch_to.active_element.send_keys(Keys.ENTER)
		self.assertEqual("accounts-name",_harness.driver.execute_script("return document.activeElement.id"))
		_harness.driver.find_element("id","accounts-cancel-name").click()
		self.assertEqual("accounts-rename-account-1",_harness.driver.execute_script("return document.activeElement.id"))
		self.assertEqual([],_harness.errors())

	def test_remove_choices_warning_and_root_failure(self):
		for delete in (False, True):
			self.open("recycle-warning")
			self.js("""
				const removeData=""" + json.dumps(delete) + """;let req;const old=window.pywebview.api.remove_account;window.pywebview.api.remove_account=async value=>{req=value;return old(value)};
				document.getElementById('accounts-remove-jamie').click();await wait(()=>document.getElementById('dialog').open);
				const dialog=document.getElementById('dialog');const closed=new Promise(resolve=>dialog.addEventListener('close',resolve,{once:true}));
				const buttons=[...dialog.querySelectorAll('button')];assert(buttons.length===3,'cancel and both choices');buttons[removeData?2:1].click();await closed;
				await wait(()=>document.querySelector('[data-account="@root"]'));
				assert(req.account_id==='jamie' && req.delete_data===removeData,'exact remove choice');
				assert(document.getElementById('view').textContent.includes('C:\\\\Synthetic\\\\jamie'),'warning path');
				assert(location.hash==='#/accounts','remaining root stays');
				assert(document.querySelectorAll('#view .banner.tone-warning').length===1 && document.querySelectorAll('#toasts .tone-warning').length===0,'N3 retained account warning appears once inline');
				assert(getComputedStyle(document.querySelector('.accounts-help')).borderBlockEndWidth==='0px','B4 older-account note has no short rule');
			""")
		self.open("root-error")
		self.js("""
			let req;const old=window.pywebview.api.remove_account;window.pywebview.api.remove_account=async value=>{req=value;return old(value)};
			document.getElementById('accounts-remove-%40root').click();await wait(()=>document.getElementById('dialog').open);
			const dialog=document.getElementById('dialog');const closed=new Promise(resolve=>dialog.addEventListener('close',resolve,{once:true}));
			assert(dialog.querySelectorAll('button').length===2,'root no keep');dialog.querySelector('.button-danger').click();await closed;
			await wait(()=>document.querySelector('#view [role=alert]'));assert(req.delete_data===true,'root deletion required');
			assert(s.get('accounts')[0].id==='@root' && document.querySelector('[data-account="@root"]'),'root retained');
			assert(document.getElementById('view').textContent.includes(i.t('errors.app.accounts_root_recycle_failed')),'F20 error');
		""")
		self.assertEqual([], _harness.errors())

	def test_last_removal_warning_survives_once_in_setup(self):
		_harness.viewport(1280, 800)
		for theme in ("dark", "light"):
			for lang in ("en", "x-long"):
				self.open("last-removed-warning", theme, lang)
				self.js("""
					const dialog=document.getElementById('dialog');document.getElementById('accounts-remove-jamie').click();await wait(()=>dialog.open);
					const closed=new Promise(resolve=>dialog.addEventListener('close',resolve,{once:true}));dialog.querySelectorAll('.button-danger')[1].click();await closed;
					await wait(()=>location.hash==='#/setup/1' && document.querySelector('.setup-screen'));
					assert(document.querySelectorAll('#toasts .tone-warning').length===1 && document.querySelectorAll('#view .banner.tone-warning').length===0,'N3 last-account warning appears once as toast');
					const warning=document.querySelector('#toasts .tone-warning');assert(warning.textContent.includes('C:\\\\Synthetic\\\\jamie') && warning.checkVisibility({checkVisibilityCSS:true}),'redirect warning visible');
				""")
				self.assertTrue(Path(_harness.screenshot(f"accounts-last-removal-warning-{theme}-{lang}")).is_file())
				self.assertEqual([], _harness.errors())

	def test_last_removal_routes_setup_and_cancel_does_nothing(self):
		self.open("last-removed")
		self.js("""
			let called=0;const old=window.pywebview.api.remove_account;window.pywebview.api.remove_account=async req=>{called++;return old(req)};
			const dialog=document.getElementById('dialog');
			document.getElementById('accounts-remove-jamie').click();await wait(()=>dialog.open);
			let closed=new Promise(resolve=>dialog.addEventListener('close',resolve,{once:true}));dialog.querySelector('.button-ghost').click();await closed;await new Promise(requestAnimationFrame);assert(called===0,'cancel no mutation');
			document.getElementById('accounts-remove-jamie').click();await wait(()=>dialog.open);
			closed=new Promise(resolve=>dialog.addEventListener('close',resolve,{once:true}));dialog.querySelector('.button-danger').click();await closed;
			await wait(()=>location.hash==='#/setup/1');assert(s.get('accounts').length===0 && !s.get('appInfo').has_accounts,'empty account state');
		""")

	def prepare(self, fixture):
		if fixture == "rename-error":
			self.js("document.getElementById('accounts-rename-jamie').click();document.getElementById('accounts-save-name').click();await wait(()=>document.querySelector('#view [role=alert]'));")
		elif fixture == "pause-error":
			self.js("document.getElementById('accounts-pause-jamie').click();await wait(()=>document.querySelector('#view [role=alert]'));")
		elif fixture in ("recycle-warning", "root-error", "remove-busy"):
			identity = "%40root" if fixture == "root-error" else "jamie"
			self.js(f"document.getElementById('accounts-remove-{identity}').click();await wait(()=>document.getElementById('dialog').open);const dialog=document.getElementById('dialog');const closed=new Promise(resolve=>dialog.addEventListener('close',resolve,{{once:true}}));dialog.querySelectorAll('.button-danger')[" + ("0" if fixture == "root-error" else "1") + "].click();await closed;await wait(()=>document.querySelector('#view [role=alert]'));")

	def test_z_fixture_matrix_keyboard_and_screenshots(self):
		for width, height in ((1280, 800), (860, 600)):
			_harness.viewport(width, height)
			for lang in ("en", "x-long", "x-rtl"):
				for theme in ("dark", "light"):
					for fixture in FIXTURES:
						with self.subTest(fixture=fixture, theme=theme, lang=lang, width=width):
							self.open(fixture, theme, lang)
							self.prepare(fixture)
							self.js("assert(document.title===i.t('accounts.title'),'title contract');assert(!document.querySelector('[style]'),'CSP');assert(document.documentElement.scrollWidth<=innerWidth+1 && document.getElementById('view').scrollWidth<=document.getElementById('view').clientWidth+1,'no overflow');")
							self.js("if(document.querySelector('.account-row')){assert(document.getElementById('accounts-add-another').disabled,'B2 add unavailable');assert(getComputedStyle(document.getElementById('accounts-add-reason')).fontSize==='14px','B2 readable reason');}assert(!document.querySelector('.accounts-limit'),'old footnote removed');")
							self.js("for(const help of document.querySelectorAll('.accounts-help'))assert(getComputedStyle(help).borderBlockEndWidth==='0px','B4 no short rule');if(document.querySelector('.accounts-help') && document.querySelector('#view .banner.tone-warning'))assert(document.querySelectorAll('#view .banner.tone-warning').length===1 && !document.querySelector('#toasts .tone-warning'),'N3 warning once on retained Accounts');")
							if width == 1280:
								ids = _harness.driver.execute_script("return [...document.querySelectorAll('#view button,#view input,#view select,#view summary,#view a[href]')].filter(n=>n.tabIndex>=0 && !n.matches(':disabled') && !n.closest('[inert],[hidden]') && n.checkVisibility({checkVisibilityCSS:true}) && (!n.closest('details:not([open])') || n.matches('summary'))).map((n,j)=>{n.id ||= 'accounts-tab-'+j;return n.id})")
								order = _harness.tab_order(len(ids) + 2)
								self.assertTrue(set(ids).issubset(order), {"missing": sorted(set(ids) - set(order)), "seen": sorted(set(ids) & set(order))})
								_harness.driver.execute_script("document.getElementById('view').scrollTop=0;document.getElementById('view').focus()")
								self.assertTrue(Path(_harness.screenshot(f"accounts-{fixture}-{theme}-{lang}")).is_file())
							self.assertEqual([], _harness.errors())
					print(f"accounts matrix {width} {lang} {theme}: {len(FIXTURES)}", flush=True)
		print(f"accounts screenshots: {SCREENSHOT_DIR}; signed_in={_harness.signed_in}", flush=True)
