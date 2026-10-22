import _bootstrap  # noqa: F401

import importlib
import os
import unittest
from pathlib import Path


_harness = None
_enabled = os.environ.get("REWARDS_APP_UI_TESTS") == "1"


def setUpModule():
	global _harness
	if not _enabled:
		return
	if not Path(__file__).with_name("harness.py").is_file():
		raise unittest.SkipTest("W2-3a UiHarness has not landed; the JS loopback scratch proof is separate")
	module = importlib.import_module("ui.harness")
	_harness = module.UiHarness()
	try:
		_harness.start()
	except BaseException:
		_harness.stop()
		raise


def tearDownModule():
	if _harness is not None:
		_harness.stop()


@unittest.skipUnless(_enabled, "Set REWARDS_APP_UI_TESTS=1 for one loopback Edge session through UiHarness")
class RuntimeTests(unittest.TestCase):
	def setUp(self):
		_harness.open(route="#/overview", fixture="shell/overview", lang="en")
		_harness.wait_ready()

	def run_js(self, body):
		result = _harness.driver.execute_async_script("""
			const done = arguments[arguments.length - 1];
			(async () => {
				const i = await import('./js/i18n.js');
				const strip = (text) => text.replace(/[\u2068\u2069]/g, '');
				const assert = (condition, label) => { if (!condition) throw new Error(label); };
			""" + body + """
				return {ok: true};
			})().then(done).catch(error => done({ok:false,error:String(error),stack:error.stack}));
		""")
		self.assertTrue(result["ok"], result)
		self.assertEqual([], _harness.errors())

	def test_exact_api_english_placeholders_and_localize(self):
		self.run_js("""
			assert(JSON.stringify(Object.keys(i).sort()) === JSON.stringify(['init','t','has','localize','formatNumber','formatDate','formatTime','formatRelative','formatDuration','formatList'].sort()), 'exact API');
			assert(i.has('tray.menu.open') && !i.has('does.not.exist'), 'has');
			assert(i.t('tray.menu.open') === 'Open Rewards Farmer', 'source text');
			const text = i.t('errors.state.signed_out', {account:'<Ada>'});
			assert(text.includes('\u2068<Ada>\u2069'), 'every value isolated');
			assert(strip(i.t('errors.decision.enough_open',{points:1})) === 'About 1 point was open.', 'one');
			assert(strip(i.t('errors.decision.enough_open',{points:2})) === 'About 2 points were open.', 'other');
			const root=document.createElement('div'); root.dataset.i18n='tray.menu.open';
			root.dataset.i18nAttr='title:tray.menu.quit;aria-label:tray.menu.open';
			i.localize(root);
			assert(root.textContent === 'Open Rewards Farmer' && root.title === 'Quit' && root.getAttribute('aria-label') === 'Open Rewards Farmer', 'root localization');
			const child=document.createElement('span'); child.dataset.i18n='tray.menu.quit'; root.append(child);
			delete root.dataset.i18n; i.localize(root); assert(child.textContent === 'Quit','descendant localization');
			assert(_missing() === true, 'missing keys log');
			function _missing() { const errors=[]; const original=console.error; console.error=(message)=>errors.push(message); try { return i.t('does.not.exist')==='does.not.exist' && i.t('toString')==='toString' && errors.length===2; } finally { console.error=original; } }
		""")

	def test_regional_formatters_and_duration_fallback(self):
		self.run_js("""
			await i.init({lang:'en',dir:'ltr',formatLocale:'de-DE'});
			assert(i.formatNumber(1234567)==='1.234.567','Windows regional number');
			const day=new Date(2026,9,14);
			assert(i.formatDate('2026-10-14') === new Intl.DateTimeFormat('de-DE',{dateStyle:'medium'}).format(day),'date');
			assert(i.formatDate('2026-10-14','short') === new Intl.DateTimeFormat('de-DE',{dateStyle:'short'}).format(day),'date style');
			assert(i.formatTime('09:45')===new Intl.DateTimeFormat('de-DE',{timeStyle:'short'}).format(new Date(2000,0,1,9,45)),'time');
			const now=Date.now; Date.now=()=>Date.parse('2026-10-14T12:00:00Z');
			try { assert(i.formatRelative('2026-10-14T11:58:00Z') === '2 minutes ago','relative past'); assert(i.formatRelative('2026-10-15T12:00:00Z') === 'tomorrow','relative future'); } finally { Date.now=now; }
			assert(i.formatList(['Ada','Ben','Cy']) === new Intl.ListFormat('en',{type:'conjunction'}).format(['Ada','Ben','Cy']),'list');
			const descriptor=Object.getOwnPropertyDescriptor(Intl,'DurationFormat');
			Object.defineProperty(Intl,'DurationFormat',{value:undefined,configurable:true});
			try { assert(i.formatDuration(120000)===i.t('common.duration.minutes',{count:2}),'duration catalog fallback'); } finally { if (descriptor) Object.defineProperty(Intl,'DurationFormat',descriptor); else delete Intl.DurationFormat; }
			if (typeof Intl.DurationFormat==='function') assert(i.formatDuration(3661000)===new Intl.DurationFormat('en',{style:'short'}).format({hours:1,minutes:1,seconds:1}),'native duration');
		""")

	def test_partial_translation_stale_entry_plural_fallback_and_rtl(self):
		self.run_js(r"""
			const index={source:'en',namespaces:['probe','shell'],locales:[{code:'en',dir:'ltr'},{code:'ru',dir:'ltr'},{code:'ar',dir:'rtl'}]};
			const source={'probe.only_en':{text:'English {account}'},'probe.english_plural':{plural:'count',forms:{one:'one {count}',other:'other {count}'}},'probe.points':{plural:'count',forms:{one:'one {count} {account}',other:'other {count} {account}'}},'probe.label':{text:'English label'}};
			const catalogs={en:source,ru:{'probe.label':{text:'Stale translation',src:'00000000'},'probe.points':{plural:'count',forms:{one:'one {count} {account}',few:'few {count} {account}',many:'many {count} {account}',other:'other {count} {account}'}}},ar:{'probe.points':{plural:'count',forms:{other:'fallback {count} {account}'}}}};
			const shell={en:{'shell.mock.example':{text:'English mock {account}'},'shell.demo.example':{text:'English demo'},'shell.real_label':{text:'English label'}},ru:{'shell.real_label':{text:'Target label',src:'00000000'}},ar:{}};
			const fetch=window.fetch;
			window.fetch=async(input)=> { const url=new URL(input,location.href); if(url.pathname.endsWith('/index.json')) return new Response(JSON.stringify(index)); const match=url.pathname.match(/\/([^/]+)\/(probe|shell)\.json$/); if(!match) throw Error('Unexpected test fetch'); return new Response(JSON.stringify({namespace:match[2],messages:(match[2]==='shell'?shell:catalogs)[match[1]]})); };
			try {
				await i.init({lang:'ru',dir:'ltr',formatLocale:'de-DE'});
				assert(i.has('probe.only_en') && strip(i.t('probe.only_en',{account:'Ada'}))==='English Ada','English fallback');
				assert(strip(i.t('shell.mock.example',{account:'Ada'}))==='English mock Ada','missing mock key English source');
				assert(i.t('shell.demo.example')==='English demo','missing demo key English source');
				assert(i.t('shell.real_label')==='Target label','real target entry retained');
				assert(strip(i.t('probe.english_plural',{count:21}))==='other 21','English fallback grammar');
				assert(i.t('probe.label')==='Stale translation','stale entry still used');
				for(const [count,category] of [[1,'one'],[2,'few'],[5,'many'],[1.5,'other']]) assert(strip(i.t('probe.points',{count,account:'Ada'})).startsWith(category+' '),'Intl plural '+category);
				await i.init({lang:'ar',dir:'rtl',formatLocale:'de-DE'});
				assert(i.t('shell.mock.example',{account:'Ada'})==='English mock \u2068Ada\u2069' && i.t('shell.demo.example')==='English demo','RTL missing mock and demo English source');
				assert(i.t('probe.points',{count:2300,account:'Ada'}) === 'fallback \u20682.300\u2069 \u2068Ada\u2069','missing form other and bidi isolation');
				assert(document.documentElement.lang==='ar' && document.documentElement.dir==='rtl','document locale');
			} finally { window.fetch=fetch; await i.init({lang:'en',dir:'ltr',formatLocale:'en-US'}); }
		""")

	def test_pseudo_locales_only_through_mock_query(self):
		self.run_js("""
			const original=i.t('tray.menu.open');
			for(const lang of ['x-long','x-rtl']) {
				const url=new URL(location.href); url.searchParams.set('lang',lang); history.replaceState(null,'',url);
				await i.init({lang,dir:lang==='x-rtl'?'rtl':'ltr',formatLocale:'de-DE'});
				const text=i.t('tray.menu.open');
				assert(text.length >= Math.ceil(original.length*1.4) && text!==original,'accent and expansion');
				assert(!i.t('errors.state.signed_out',{account:'Ada'}).includes('{account}'),'placeholder names preserved');
				assert(i.t('errors.state.signed_out',{account:'Ada'}).includes('\u2068Ada\u2069'),'pseudo insertion');
				assert(document.documentElement.dir===(lang==='x-rtl'?'rtl':'ltr'),'pseudo direction');
				assert(i.formatNumber(2300)==='2.300' && i.formatList(['A','B'])==='A and B','pseudo Intl uses English, Windows region unchanged');
			}
			const url=new URL(location.href); url.searchParams.delete('lang'); history.replaceState(null,'',url);
			for(const lang of ['x-long','x-rtl']) {
				await i.init({lang,dir:lang==='x-rtl'?'rtl':'ltr',formatLocale:'en-US'});
				assert(document.documentElement.lang==='en' && document.documentElement.dir==='ltr' && i.t('tray.menu.open')===original,'pseudo not a shipped locale');
			}
		""")
