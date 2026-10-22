const catalogRoot = new URL("../i18n/", import.meta.url);
const placeholder = /\{([a-z][a-z0-9_]*)\}/g;
const isolateStart = "\u2068";
const isolateEnd = "\u2069";
const accents = "àƀçďëƒğħïĵķľɱñôþɋŕšţüṽŵẋÿž";
let source = Object.create(null);
let messages = Object.create(null);
let language = "en";
let formatLocale = "en-US";
let pluralRules = new Intl.PluralRules("en");
const sourcePluralRules = new Intl.PluralRules("en");
let initialization = 0;

function intlLanguage() {
    return language.startsWith("x-") ? "en" : language;
}

function pseudoText(text) {
    // Leave placeholder names intact so layout probes exercise the real callsites.
    const parts = text.split(/(\{[a-z][a-z0-9_]*\})/g);
    const accented = parts.map((part) => part.startsWith("{") ? part : part.replace(/[a-z]/gi, (letter) => {
        const value = accents[letter.toLowerCase().charCodeAt(0) - 97];
        return letter === letter.toUpperCase() ? value.toUpperCase() : value;
    })).join("");
    return `[${accented}${"~".repeat(Math.ceil(text.length * 0.4))}]`;
}

function pseudoEntry(entry) {
    if (entry.forms) {
        return { ...entry, forms: Object.fromEntries(Object.entries(entry.forms).map(([form, text]) => [form, pseudoText(text)])) };
    }
    return { ...entry, text: pseudoText(entry.text) };
}

async function loadCatalog(locale, namespace) {
    const response = await fetch(new URL(`${encodeURIComponent(locale)}/${encodeURIComponent(namespace)}.json`, catalogRoot));
    if (response.status === 404) {
        // Partial translations and not-yet-shipped namespaces use the English fallback.
        return Object.create(null);
    }
    if (!response.ok) throw new Error(`Catalog ${locale}/${namespace}: HTTP ${response.status}`);
    const catalog = await response.json();
    if (catalog.namespace !== namespace || !catalog.messages || typeof catalog.messages !== "object") {
        throw new Error(`Invalid catalog ${locale}/${namespace}`);
    }
    return catalog.messages;
}

export async function init({ lang, dir, formatLocale: region }) {
    const generation = ++initialization;
    const response = await fetch(new URL("index.json", catalogRoot));
    if (!response.ok) throw new Error(`Catalog index: HTTP ${response.status}`);
    const index = await response.json();
    const query = new URLSearchParams(window.location.search);
    const isPseudo = ["x-long", "x-rtl"].includes(lang) && query.has("mock") && query.get("lang") === lang;
    const locale = index.locales.find((entry) => entry.code.toLowerCase() === String(lang).toLowerCase());
    const selected = isPseudo ? lang : locale?.code || index.source;
    const englishFiles = await Promise.all(index.namespaces.map((namespace) => loadCatalog(index.source, namespace)));
    const english = Object.assign(Object.create(null), ...englishFiles);
    let translated = english;
    if (isPseudo) {
        translated = Object.fromEntries(Object.entries(english).map(([key, entry]) => [key, pseudoEntry(entry)]));
    } else if (selected !== index.source) {
        const files = await Promise.all(index.namespaces.map((namespace) => loadCatalog(selected, namespace)));
        translated = Object.assign(Object.create(null), ...files);
    }
    if (generation !== initialization) return;
    source = english;
    messages = translated;
    language = selected;
    formatLocale = region || "en-US";
    pluralRules = new Intl.PluralRules(intlLanguage());
    document.documentElement.lang = selected;
    const selectedLocale = locale || index.locales.find((entry) => entry.code === index.source);
    document.documentElement.dir = isPseudo ? (selected === "x-rtl" ? "rtl" : "ltr") : (locale ? dir || locale.dir : selectedLocale?.dir || "ltr");
}

export function has(key) {
    return Object.hasOwn(messages, key) || Object.hasOwn(source, key);
}

export function t(key, params = {}) {
    const translated = Object.hasOwn(messages, key);
    let entry = translated ? messages[key] : source[key];
    if (!entry) {
        console.error(`Missing i18n key: ${key}`);
        return key;
    }
    let text = entry.text;
    const values = { ...params };
    if (entry.forms) {
        const driver = entry.plural;
        const count = Number(params[driver]);
        // English fallback text keeps English grammar even when the UI language differs.
        const category = (translated ? pluralRules : sourcePluralRules).select(count);
        text = entry.forms[category] ?? entry.forms.other;
        if (text === undefined) {
            entry = source[key];
            text = entry?.forms?.[sourcePluralRules.select(count)] ?? entry?.forms?.other;
        }
        values[driver] = formatNumber(count);
    }
    if (typeof text !== "string") {
        console.error(`Invalid i18n entry: ${key}`);
        return key;
    }
    return text.replace(placeholder, (match, name) => {
        if (!Object.hasOwn(values, name)) {
            console.error(`Missing i18n parameter: ${key}.{${name}}`);
            return match;
        }
        return `${isolateStart}${String(values[name])}${isolateEnd}`;
    });
}

export function localize(root) {
    const selector = "[data-i18n], [data-i18n-attr]";
    const elements = [...root.querySelectorAll(selector)];
    if (root.matches?.(selector)) elements.unshift(root);
    for (const element of elements) {
        if (element.dataset.i18n) element.textContent = t(element.dataset.i18n);
        for (const pair of (element.dataset.i18nAttr || "").split(";")) {
            const separator = pair.indexOf(":");
            if (separator < 0) continue;
            const attribute = pair.slice(0, separator).trim();
            const key = pair.slice(separator + 1).trim();
            if (attribute && key) element.setAttribute(attribute, t(key));
        }
    }
}

export function formatNumber(n) {
    return new Intl.NumberFormat(formatLocale).format(n);
}

function dateValue(iso) {
    // A calendar date is local, unlike an ISO timestamp with its explicit offset.
    if (typeof iso === "string" && /^\d{4}-\d{2}-\d{2}$/.test(iso)) {
        const [year, month, day] = iso.split("-").map(Number);
        return new Date(year, month - 1, day);
    }
    if (typeof iso === "string" && /^\d{2}:\d{2}$/.test(iso)) {
        const [hours, minutes] = iso.split(":").map(Number);
        return new Date(2000, 0, 1, hours, minutes);
    }
    return iso instanceof Date ? iso : new Date(iso);
}

export function formatDate(iso, style = "medium") {
    return new Intl.DateTimeFormat(formatLocale, { dateStyle: style }).format(dateValue(iso));
}

export function formatTime(iso) {
    return new Intl.DateTimeFormat(formatLocale, { timeStyle: "short" }).format(dateValue(iso));
}

export function formatRelative(iso) {
    const seconds = (dateValue(iso).getTime() - Date.now()) / 1000;
    const scales = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60], ["second", 1]];
    const [unit, scale] = scales.find(([, size]) => Math.abs(seconds) >= size) || scales.at(-1);
    return new Intl.RelativeTimeFormat(intlLanguage(), { numeric: "auto" }).format(Math.round(seconds / scale), unit);
}

export function formatDuration(ms) {
    const totalSeconds = Math.max(0, Math.floor(ms / 1000));
    if (typeof Intl.DurationFormat === "function") {
        return new Intl.DurationFormat(intlLanguage(), { style: "short" }).format({
            hours: Math.floor(totalSeconds / 3600),
            minutes: Math.floor(totalSeconds / 60) % 60,
            seconds: totalSeconds % 60,
        });
    }
    return t("common.duration.minutes", { count: Math.round(Math.max(0, ms) / 60000) });
}

export function formatList(items) {
    return new Intl.ListFormat(intlLanguage(), { type: "conjunction" }).format(items);
}
