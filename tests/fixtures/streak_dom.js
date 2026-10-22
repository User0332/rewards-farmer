// Execute the production DOM reader on synthetic nodes, never a browser.
const fs = require('node:fs');
const {script, limit} = JSON.parse(fs.readFileSync(0, 'utf8'));
let clicks = 0, navigation = 0, scrolls = 0;
class Element {
	constructor(tag, text = '', attrs = {}, children = []) {
		this.tagName = tag.toUpperCase(); this.attrs = attrs; this.id = attrs.id || '';
		this.attributes = Object.keys(attrs).map(name => ({name}));
		this.classList = (attrs.class || '').split(' ').filter(Boolean);
		this.children = children; this.parentElement = null;
		this.childNodes = text ? [{nodeType: 3, textContent: text}] : [];
		for (const child of children) { child.parentElement = this; this.childNodes.push(child); }
		this.innerText = [text, ...children.filter(child => !child.attrs.hidden).map(child => child.innerText)].filter(Boolean).join('\n');
	}
	getAttribute(name) { return this.attrs[name] || null; }
	getClientRects() { return this.attrs.hidden ? [] : [{}]; }
	closest(selector) { let node = this; while (node) { if (selector === '[role="progressbar"]' ? node.attrs.role === 'progressbar' : node.attrs.hidden || node.attrs['aria-hidden'] === 'true') return node; node = node.parentElement; } return null; }
	querySelectorAll() { return this.children.flatMap(child => [child, ...child.querySelectorAll('*')]); }
	click() { clicks++; }
	scrollIntoView() { scrolls++; }
}
function read(roots, max = limit) {
	const document = {querySelectorAll(selector) { return selector === '#streaks' ? roots.filter(node => node.id === 'streaks') : roots.filter(node => node.tagName === 'SECTION' || node.attrs.role === 'region'); }};
	const window = {location: {assign() { navigation++; }}, open() { navigation++; }};
	return new Function('document', 'getComputedStyle', 'performance', 'TextEncoder', 'window', `return function() {${script}}`)(document, () => ({display: 'block', visibility: 'visible', opacity: '1'}), {now: () => 0}, TextEncoder, window)(max);
}
const tile = (name, text) => new Element('button', name, {'role': 'button', 'aria-label': name + ' user@example.test', 'data-state': 'secret-value', class: 'tile daily'}, [new Element('span', text)]);
const small = read([
	new Element('section', '', {id: 'streaks'}, [tile('Daily set streak', 'Day 2 of 7'), tile('Visual search streak', 'Day 1 of 7'), new Element('span', '', {'aria-label': 'Bing search streak 4 of 7'}), new Element('button', 'hidden address user@example.test', {hidden: true})]),
	new Element('section', 'Big Bonus puzzle: collect the pieces for 1,000 points', {id: 'bonus'}),
	new Element('section', 'Unrelated shopping offers', {id: 'shopping'})
]);
const large = read([new Element('section', '', {id: 'streaks'}, Array.from({length: 50}, () => tile('Daily set streak ' + 'word '.repeat(100), String.fromCodePoint(0x20000).repeat(1000))))]);
const absent = read([new Element('section', 'Unrelated news')]);
const empty = read([new Element('section', '', {id: 'streaks'})]);
const bar = new Element('div', '', {role: 'progressbar', 'aria-label': 'Stamps collected', 'aria-valuenow': '5', 'aria-valuemax': '12', 'aria-valuetext': '5 of 12 stamps', class: 'grid grid-cols-6'},
	Array.from({length: 15}, (_, index) => new Element('div', '', {class: index < 5 ? 'stamp earned' : 'stamp', 'aria-label': 'Stamp user@example.test'})));
const deep = new Element('span', '30');
let wrapped = deep;
for (let index = 0; index < 15; index++) wrapped = new Element('div', '', {}, [wrapped]);
const expanded = read([new Element('section', 'Streaks', {id: 'streaks'}, [bar, wrapped])]);
const full = new Element('section', '', {id: 'streaks'}, [
	new Element('h2', 'Streaks'), new Element('button', 'Daily streaks reset in 3h', {'aria-label': 'About Streaks'}),
	new Element('p', '1,000 BONUS', {class: 'bg-rewardsBgBonusGradient text-pageHeader'}),
	new Element('p', 'Earn 12 stamps for big bonus points'),
	new Element('div', '', {role: 'progressbar', 'aria-label': 'Stamps collected', 'aria-valuenow': '5', 'aria-valuemax': '12', class: 'grid grid-cols-6'},
		Array.from({length: 12}, () => new Element('div', '', {class: 'stamp earned'}))),
	...[['Bing Search Streak', 6, 100, '3', '100', 'Search: 1/1'], ['Daily Set Streak', 6, 100, '30', '100', 'Activity: 3/3'],
		['Bing App Streak', 0, 5, '+5', '50', 'Check-in: 0/1'], ['Visual Search Streak', 2, 5, '+5', '100', 'Activity: 0/1']].map(([name, day, next, points, end, today]) => {
		let reward = new Element('div', '', {}, [new Element('span', points), new Element('span', end)]);
		for (let depth = 0; depth < 13; depth++) reward = new Element('div', '', {}, [reward]);
		return new Element('div', '', {}, [new Element('p', name, {class: 'text-globalBody2Strong'}), new Element('div', `Day ${day} of 7 streak completed.`),
			new Element('div', `Complete the next day to earn ${next} points.`), reward, new Element('div', today, {class: 'text-end'})]);
	})
]);
const full6 = read([full], 6144 - 128), full8 = read([full], 8192 - 128);
// Real-shaped volume from the masked 2026-10-21 observation, not account data.
const stampClasses = 'grid aspect-square place-content-center rounded-cornerCircular bg-bgCtrlBrandRest forced-colors:bg-[Highlight] w-full max-w-8';
const buttonClasses = 'inline-flex items-center justify-center rounded-cornerMedium border-none bg-transparent px-4 py-2 text-globalBody2Strong text-textPrimary hover:bg-bgCtrlSubtleHover focus-visible:outline';
const real = new Element('section', '', {id: 'streaks'}, [
	new Element('h2', 'Streaks'), new Element('button', 'Daily streaks reset in 3h', {'aria-label': 'About Streaks', class: buttonClasses}),
	new Element('button', '', {'aria-label': 'Streaks', class: buttonClasses}, [new Element('p', '1,000 BONUS', {class: 'bg-rewardsBgBonusGradient text-pageHeader'}),
		new Element('p', 'Earn 12 stamps for big bonus points'), new Element('span', 'Learn more')]),
	new Element('div', '', {role: 'progressbar', 'aria-label': 'Stamps collected', 'aria-valuenow': '9', 'aria-valuemax': '12', class: 'grid grid-cols-6'},
		Array.from({length: 12}, () => new Element('div', '', {class: stampClasses}))),
	...[['Bing Search Streak', 6, 100, '3', '100', 'Search: 0/1'], ['Daily Set Streak', 6, 100, '30', '100', 'Activity: 0/3'],
		['Bing App Streak', 0, 5, '+5', '50', 'Check-in: 0/1'], ['Visual Search Streak', 2, 5, '+5', '100', 'Activity: 0/1']].map(([name, day, next, points, end, today]) => {
		let reward = new Element('div', '', {}, [new Element('span', points), new Element('span', end)]);
		for (let depth = 0; depth < 13; depth++) reward = new Element('div', '', {}, [reward]);
		return new Element('div', '', {}, [new Element('p', name, {class: 'text-globalBody2Strong'}), new Element('div', `Day ${day} of 7 streak completed.`),
			new Element('div', `Complete the next day to earn ${next} points.`), reward, new Element('div', today, {class: 'text-end'})]);
	})
]);
const realShape = read([real]);
process.stdout.write(JSON.stringify({small, large, absent, empty, expanded, full6, full8, realShape, clicks, navigation, scrolls}));
