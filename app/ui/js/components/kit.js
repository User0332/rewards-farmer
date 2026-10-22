import * as defaultI18n from "../i18n.js";
import * as defaultBridge from "../bridge.js";
import * as defaultStore from "../store.js";
import { el, append, svg } from "../dom.js";
import { appendLogPage, joinLog } from "./log-stream.js";
import { enhanceChart } from "./chart-explore.js";

let services = { i18n: defaultI18n, bridge: defaultBridge, store: defaultStore };
const records = new WeakMap();
const accountPictures = new Map();
let nextId = 0;
export const DIRECTIONAL_ICONS = new Set(["chevron-left", "chevron-right", "arrow-left", "arrow-right", "back", "forward"]);
export function configure(context) { services = { ...services, ...context }; }
export function accountInitial(account) {
    const identity = [account?.name, account?.email, account?.label].find((value) => typeof value === "string" && value.trim())?.trim() || "";
    return [...identity][0]?.toUpperCase() || "";
}
export function accountAvatar(account) {
    const root = el("span", { class: "avatar", "aria-hidden": "true", text: accountInitial(account) });
    const id = account?.id;
    const rev = account?.picture_rev;
    if (!id || typeof rev !== "string" || !rev) return root;
    root.dataset.pictureRev = rev;
    let revisions = accountPictures.get(id);
    if (!revisions) { revisions = new Map(); accountPictures.set(id, revisions); }
    let picture = revisions.get(rev);
    if (!picture) {
        // Share the in-flight request and negative result across every avatar.
        picture = Promise.resolve().then(() => services.bridge.call("get_account_picture", { account_id: id })).then((answer) => {
            if (answer.account_id !== id || answer.rev !== rev || typeof answer.data_url !== "string" || !/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(answer.data_url)) return null;
            return answer.data_url;
        }).catch(() => null);
        revisions.set(rev, picture);
    }
    picture.then((dataUrl) => {
        if (!dataUrl || root.dataset.pictureRev !== rev) return;
        const image = el("img", { class: "avatar-picture", alt: "", decoding: "async" });
        image.addEventListener("load", () => {
            if (root.dataset.pictureRev === rev) root.replaceChildren(image);
        }, { once: true });
        // A corrupt local picture leaves the existing initial in place.
        image.src = dataUrl;
    });
    return root;
}
const t = (key, params) => services.i18n.t(key, params);
const num = (n) => services.i18n.formatNumber(n);
const unknown = () => t("common.value.unknown");
const keyText = (props, key = "labelKey") => t(props[key], props.params || {});
const idFor = (props) => props.id || `control-${++nextId}`;
function optionText(option) { return option.labelKey ? t(option.labelKey, option.params) : option.label; }
function attrs(node, values) { for (const [key, value] of Object.entries(values)) node.setAttribute(key, String(value)); return node; }
function icon(name) {
    return svg("svg", { class: `icon${DIRECTIONAL_ICONS.has(name) ? " icon-directional" : ""}`, "aria-hidden": "true", focusable: "false" }, [svg("use", { href: `img/icons.svg#i-${name}` })]);
}
function actions(items) { return el("div", { class: "control-actions" }, (items || []).map((item) => item instanceof Node ? item : createComponent("button", item))); }
function field(props) {
    const root = el("div", { class: "field" });
    const id = idFor(props);
    root.append(el("label", { class: "field-label", for: id, text: keyText(props) }));
    return { root, id };
}
function textField(props) {
    const root = el("div", { class: "field text-field" });
    const input = el("input");
    const label = el("label", { class: "field-label" });
    const description = el("p", { class: "field-description" });
    const error = el("p", { class: "field-error" });
    root.append(label, description, input, error);
    let current;
    let updating = false;
    let stableId = idFor(props);
    root._fieldUpdate = (next) => {
        updating = true;
        const focused = document.activeElement === input;
        const start = input.selectionStart;
        const end = input.selectionEnd;
        const direction = input.selectionDirection;
        current = next;
        stableId = next.id || stableId;
        input.id = stableId;
        label.htmlFor = stableId;
        label.textContent = keyText(next);
        description.id = `${stableId}-description`;
        description.hidden = !next.descriptionKey;
        description.textContent = next.descriptionKey ? t(next.descriptionKey) : "";
        error.id = `${stableId}-error`;
        error.hidden = !next.error;
        error.textContent = next.error ? t(next.error.messageKey, next.error.params) : "";
        const type = ["text", "number", "password", "url"].includes(next.type) ? next.type : "text";
        if (input.type !== type) input.type = type;
        const value = String(next.value ?? "");
        if (input.value !== value) input.value = value;
        input.disabled = Boolean(next.disabled);
        for (const [attr, value] of Object.entries({ min: next.min, max: next.max, step: next.step, maxlength: next.maxLength, autocomplete: next.autocomplete, placeholder: next.placeholderKey ? t(next.placeholderKey) : undefined })) {
            if (value === undefined || value === null) input.removeAttribute(attr);
            else input.setAttribute(attr, String(value));
        }
        input.setAttribute("aria-invalid", String(Boolean(next.error)));
        const described = [next.descriptionKey ? description.id : null, next.error ? error.id : null].filter(Boolean);
        if (described.length) input.setAttribute("aria-describedby", described.join(" "));
        else input.removeAttribute("aria-describedby");
        if (focused && !input.disabled) input.focus({ preventScroll: true });
        if (focused && start !== null && ["text", "password"].includes(input.type)) {
            input.setSelectionRange(Math.min(start, input.value.length), Math.min(end, input.value.length), direction || "none");
        }
        updating = false;
    };
    input.addEventListener("input", () => current.onInput?.(input.value));
    input.addEventListener("blur", () => { if (!updating) current.onCommit?.(input.value); });
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); current.onCommit?.(input.value); }
    });
    root._fieldUpdate(props);
    return root;
}

function chartScale(numbers, mode) {
    if (mode === "earned") {
        const high = Math.max(0, ...numbers);
        const target = Math.max(1, high / 3);
        const magnitude = 10 ** Math.floor(Math.log10(target));
        const step = Math.max(1, ([1, 2, 2.5, 5, 10].find((value) => value >= target / magnitude) || 10) * magnitude);
        const max = Math.max(step, Math.ceil(high / step) * step);
        return { min: 0, max, ticks: Array.from({ length: Math.round(max / step) + 1 }, (_, index) => index * step) };
    }
    const low = mode === "earned" ? Math.min(0, ...numbers) : Math.min(...numbers);
    const high = Math.max(...numbers);
    const rawStep = (high - low || Math.max(2, Math.abs(high) * 0.1)) / 2;
    const magnitude = 10 ** Math.floor(Math.log10(rawStep));
    const fraction = rawStep / magnitude;
    const nice = [1, 1.5, 2, 2.5, 5, 10].reduce((best, value) => Math.abs(value - fraction) < Math.abs(best - fraction) ? value : best);
    const step = Math.max(1, nice * magnitude);
    const min = Math.floor(low / step) * step;
    const ticks = [min, min + step, min + 2 * step];
    return { min, max: Math.max(high, ticks[2]), ticks };
}
const builders = {
    icon: (p) => icon(p.name),
    textField,
    button(p) {
        const node = el("button", { class: `button button-${p.variant || "secondary"}${p.size === "large" ? " button-large" : ""}`, type: "button", disabled: p.disabled || p.busy });
        if (p.busy) node.append(createComponent("spinner", { labelKey: "common.action.working" }));
        else if (p.icon) node.append(icon(p.icon));
        node.append(el("span", { text: keyText(p) }));
        attrs(node, { "aria-busy": Boolean(p.busy) });
        if (p.onClick) node.addEventListener("click", p.onClick);
        return node;
    },
    iconButton(p) {
        const node = el("button", { class: "button button-ghost icon-button", type: "button", "aria-label": keyText(p), title: keyText(p) }, [icon(p.icon)]);
        if (p.onClick) node.addEventListener("click", p.onClick);
        return node;
    },
    switchControl(p) {
        const id = idFor(p);
        const labelId = `${id}-label`;
        const descId = `${id}-description`;
        const copy = el("div", { class: "switch-copy" }, [el("label", { id: labelId, for: id, class: "field-label", text: keyText(p) })]);
        if (p.descriptionKey) copy.append(el("p", { id: descId, class: "field-description", text: t(p.descriptionKey) }));
        const control = el("button", { id, class: "switch-control", type: "button", role: "switch", disabled: p.disabled, "aria-checked": Boolean(p.checked), "aria-labelledby": labelId, "aria-describedby": p.descriptionKey ? descId : null });
        control.addEventListener("click", () => {
            const checked = control.getAttribute("aria-checked") !== "true";
            control.setAttribute("aria-checked", String(checked));
            p.onChange?.(checked);
        });
        return el("div", { class: "switch-row" }, [copy, control]);
    },
    select(p) {
        const { root, id } = field(p);
        const input = el("select", { id }, (p.options || []).map((option) => el("option", { value: option.value, text: optionText(option) })));
        input.value = p.value ?? "";
        input.addEventListener("change", () => p.onChange?.(input.value));
        root.append(input);
        return root;
    },
    segmented(p) {
        const id = idFor(p);
        const root = el("div", { class: "field", id });
        root.append(el("span", { id: `${id}-label`, class: "field-label", text: keyText(p) }));
        const group = el("div", { class: "segmented-options", role: "radiogroup", "aria-labelledby": `${id}-label` });
        const choose = (button) => {
            for (const peer of group.children) { peer.setAttribute("aria-checked", String(peer === button)); peer.tabIndex = peer === button ? 0 : -1; }
            p.onChange?.(button.dataset.value);
        };
        (p.options || []).forEach((option, index) => {
            const selected = option.value === p.value || (p.value === undefined && index === 0);
            const button = el("button", { class: "segment", role: "radio", type: "button", "data-value": option.value, "aria-checked": selected, tabIndex: selected ? 0 : -1, text: optionText(option) });
            button.addEventListener("click", () => choose(button));
            button.addEventListener("keydown", (event) => {
                const rtl = document.documentElement.dir === "rtl";
                const directions = { ArrowRight: rtl ? -1 : 1, ArrowLeft: rtl ? 1 : -1, ArrowDown: 1, ArrowUp: -1 };
                const peers = [...group.children];
                let target;
                if (Object.hasOwn(directions, event.key)) target = peers[(peers.indexOf(button) + directions[event.key] + peers.length) % peers.length];
                if (event.key === "Home") target = peers[0];
                if (event.key === "End") target = peers.at(-1);
                if (target) { event.preventDefault(); choose(target); target.focus(); }
            });
            group.append(button);
        });
        root.append(group);
        return root;
    },
    timeInput(p) {
        const { root, id } = field(p);
        const input = el("input", { id, type: "time", value: p.value || "" });
        input.addEventListener("change", () => p.onChange?.(input.value));
        root.append(input);
        return root;
    },
    timeRange(p) {
        const { root, id } = field(p);
        root.querySelector("label").htmlFor = `${id}-start`;
        const start = el("input", { id: `${id}-start`, type: "time", value: p.start, "aria-label": t("common.time.start") });
        const end = el("input", { id: `${id}-end`, type: "time", value: p.end, "aria-label": t("common.time.end") });
        for (const input of [start, end]) input.addEventListener("change", () => p.onChange?.({ start: start.value, end: end.value }));
        root.append(el("div", { class: "time-range-inputs" }, [start, el("span", { text: t("common.time.to") }), end]));
        return root;
    },
    weekdayPicker(p) {
        const id = idFor(p);
        const root = el("div", { class: "field" });
        root.append(el("span", { class: "field-label", id: `${id}-label`, text: keyText(p) }));
        const locale = services.store.get("appInfo")?.format_locale || "en-US";
        const localeObject = new Intl.Locale(locale);
        const firstDay = localeObject.weekInfo?.firstDay || localeObject.getWeekInfo?.().firstDay || 1;
        const names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
        const formatter = new Intl.DateTimeFormat(locale, { weekday: "short" });
        const selected = new Set(p.days || []);
        const group = el("div", { id, class: "weekdays", role: "group", "aria-labelledby": `${id}-label` });
        for (let index = 0; index < 7; index++) {
            const dayIndex = (firstDay - 1 + index) % 7;
            const day = names[dayIndex];
            const button = el("button", { type: "button", class: "button weekday", "data-day": day, "aria-pressed": selected.has(day), text: formatter.format(new Date(2026, 0, 5 + dayIndex)) });
            button.addEventListener("click", () => {
                selected.has(day) ? selected.delete(day) : selected.add(day);
                button.setAttribute("aria-pressed", String(selected.has(day)));
                p.onChange?.(names.filter((name) => selected.has(name)));
            });
            group.append(button);
        }
        root.append(group);
        return root;
    },
    card(p) {
        const root = el("section", { class: "card" });
        if (p.titleKey || p.actions?.length) root.append(el("header", { class: "card-header" }, [p.titleKey ? el("h2", { text: t(p.titleKey) }) : null, actions(p.actions)]));
        root.append(el("div", { class: "card-body" }, p.children || []));
        return root;
    },
    stat(p) {
        return el("div", { class: `stat${p.tone ? ` tone-${p.tone}` : ""}` }, [el("p", { class: "stat-label", text: keyText(p) }), el("p", { class: "stat-value", text: p.value ?? unknown() }), p.subKey ? el("p", { class: "stat-sub", text: t(p.subKey, p.subParams) }) : null]);
    },
    taskList(p) {
        const root = el("ul", { class: `task-list${p.compact ? " is-compact" : ""}` });
        for (const task of p.tasks || []) {
            const status = task.status === "ok" && task.still_open === true ? "open" : task.status;
            let statusKey = `common.task_status.${status}`;
            if (!services.i18n.has(statusKey)) statusKey = "common.task_status.none";
            const markerName = status === "ok" ? "check" : ["skip", "off"].includes(status) ? "minus" : status === "fail" ? "close" : null;
            const marker = el("span", { class: "task-marker", "aria-hidden": "true" }, markerName ? icon(markerName) : status === "running" ? el("span", { class: "spinner-disc" }) : null);
            const reason = task.reason_key ? el("span", { class: "task-reason", text: t(task.reason_key) }) : null;
            const state = el("span", { class: "task-state" }, [el("span", { text: task.progress ?? t(statusKey) }), reason]);
            root.append(el("li", { class: "task-row", "data-task": task.task, "data-status": status }, [marker, el("span", { class: "task-name", text: t(task.name_key || `common.task.${task.task}`) }), state]));
        }
        return root;
    },
    pointsChart(p) {
        const mode = p.mode === "balance" ? "balance" : "earned";
        const series = p.series || [];
        const root = el("div", { class: "points-chart", "data-mode": mode });
        const table = el("table");
        const accessibleTable = el("div", { class: "sr-only chart-table" }, [table]);
        table.append(el("caption", { text: t(`common.chart.${mode}`) }), el("thead", {}, el("tr", {}, [el("th", { scope: "col", text: t("common.chart.date") }), el("th", { scope: "col", text: t(`common.chart.${mode}`) })])));
        table.append(el("tbody", {}, series.map((point) => el("tr", {}, [el("th", { scope: "row", text: services.i18n.formatDate(point.date) }), el("td", { text: point[mode] === null || point[mode] === undefined ? unknown() : num(point[mode]) })]))));
        const numbers = series.map((point) => point[mode]).filter((value) => typeof value === "number" && Number.isFinite(value));
        if (!numbers.length) { root.append(el("p", { class: "chart-empty", text: t("common.chart.empty") }), accessibleTable); return root; }
        const { min, max, ticks } = chartScale(numbers, mode);
        const range = max - min || 1;
        const rtl = document.documentElement.dir === "rtl";
        const start = rtl ? 52 : 74;
        const x = (index) => start + (rtl ? 1 - index / Math.max(1, series.length - 1) : index / Math.max(1, series.length - 1)) * 794;
        const y = (value) => 138 - (value - min) / range * 110;
        const plot = el("div", { class: "chart-plot" });
        const labels = svg("svg", { class: "chart-labels", "aria-hidden": "true", preserveAspectRatio: "xMidYMid meet" });
        const label = (name, value, px, py) => {
            const anchor = name === "chart-tick" ? "middle" : rtl ? "start" : "end";
            labels.append(svg("text", { class: `chart-label ${name}`, x: `${px / 920 * 100}%`, y: `${py / 180 * 100}%`, "text-anchor": anchor, "dominant-baseline": name === "chart-latest" ? "auto" : "middle" }, value));
        };
        const image = svg("svg", { class: "chart-svg", viewBox: "0 0 920 180", role: "img", "aria-label": t(`common.chart.${mode}`), preserveAspectRatio: "none" });
        plot.append(image);
        for (const value of ticks) {
            image.append(svg("line", { x1: start, x2: start + 794, y1: y(value), y2: y(value), class: "chart-rule" }));
            label("chart-y", num(value), rtl ? 856 : 64, y(value));
        }
        const segments = [];
        let segment = [];
        series.forEach((point, index) => {
            if (typeof point[mode] !== "number" || !Number.isFinite(point[mode])) { if (segment.length) segments.push(segment); segment = []; }
            else segment.push([x(index), y(point[mode])]);
        });
        if (segment.length) segments.push(segment);
        if (mode === "earned") {
            const width = Math.max(3, Math.min(22, 794 / Math.max(1, series.length - 1) * 0.62));
            series.forEach((point, index) => {
                if (typeof point.earned !== "number" || !Number.isFinite(point.earned)) {
                    image.append(svg("line", { class: "chart-unknown-day", "data-index": index, x1: x(index) - 2, x2: x(index) + 2, y1: 142, y2: 142 }));
                } else image.append(svg("rect", { class: "chart-day-bar", "data-index": index, "data-value": point.earned, x: x(index) - width / 2, y: y(point.earned), width, height: Math.max(0, 138 - y(point.earned)), rx: 2 }));
            });
        } else {
            for (const part of segments) image.append(part.length === 1 ? svg("circle", { cx: part[0][0], cy: part[0][1], r: 3, class: "chart-point" }) : svg("path", { d: part.map(([px, py], index) => `${index ? "L" : "M"}${px},${py}`).join(" "), class: "chart-line" }));
        }
        const formatLocale = services.store.get("appInfo")?.format_locale || "en-US";
        const tickFormatter = new Intl.DateTimeFormat(formatLocale, { month: "short", day: "numeric" });
        for (const index of new Set([0, Math.floor((series.length - 1) / 2), series.length - 1])) {
            const point = series[index];
            if (point) {
                const [year, month, day] = point.date.split("-").map(Number);
                label("chart-tick", tickFormatter.format(new Date(year, month - 1, day)), x(index), 167);
            }
        }
        const latestIndex = series.findLastIndex((point) => typeof point[mode] === "number" && Number.isFinite(point[mode]));
        if (latestIndex >= 0) {
            const value = series[latestIndex][mode];
            image.append(svg("circle", { cx: x(latestIndex), cy: y(value), r: 3.5, class: "chart-point chart-latest-point" }));
            image.querySelector(`.chart-day-bar[data-index="${latestIndex}"]`)?.classList.add("is-latest");
            label("chart-latest", num(value), x(latestIndex) + (rtl ? 8 : -8), Math.max(16, y(value) - 10));
        }
        plot.append(labels);
        root.append(plot, accessibleTable);
        return enhanceChart(root, series, mode, services);
    },
    goalCard(p) {
        const root = el("div", { class: "goal-card" });
        let active = true;
        let goal = p.goal;
        const restore = () => {
            root.replaceChildren();
            const copy = el("div", { class: "goal-copy" });
            if (goal) {
                copy.append(el("p", { text: t("common.goal.remaining", { count: goal.remaining }) }), el("p", { class: "subtle", text: goal.days === null || goal.days === undefined ? t("common.goal.estimate_unknown") : t("common.goal.days", { count: goal.days }) }));
            } else copy.append(el("p", { text: t("common.goal.none") }));
            root.append(copy);
            if (p.onSet) root.append(createComponent("button", { labelKey: goal ? "common.goal.edit" : "common.goal.set", variant: "ghost", onClick: edit }));
        };
        const edit = () => {
            const id = `goal-target-${++nextId}`;
            const presets = [5000, 6500, 10000, 15000, 25000];
            let choice = goal && !presets.includes(goal.target) ? "custom" : String(goal?.target || 10000);
            let customValue = String(goal?.target || 10000);
            let busy = false;
            let custom = createComponent("textField", { id, labelKey: "common.goal.target", type: "number", min: 100, max: 1000000, step: 1, value: customValue, onInput: (value) => { customValue = value; }, onCommit: (value) => { customValue = value; } });
            custom.hidden = choice !== "custom";
            const picker = createComponent("select", { id: `${id}-preset`, labelKey: "common.goal.choose", value: choice, options: [...presets.map((value) => ({ value: String(value), labelKey: "common.goal.preset_points", params: { count: value } })), { value: "custom", labelKey: "common.goal.custom" }], onChange: (value) => { choice = value; custom.hidden = choice !== "custom"; if (!custom.hidden) custom.querySelector("input").focus(); } });
            const form = el("form", { class: "goal-editor" }, [picker, custom]);
            const commit = async (clear = false) => {
                if (busy) return;
                const target = choice === "custom" ? Number(customValue) : Number(choice);
                if (!clear && (!Number.isInteger(target) || target < 100 || target > 1000000)) {
                    custom = updateComponent(custom, { error: { messageKey: "errors.settings.out_of_range", params: { min: 100, max: 1000000 } } });
                    custom.hidden = false;
                    custom.querySelector("input").focus();
                    return;
                }
                busy = true;
                for (const button of form.querySelectorAll("button")) button.disabled = true;
                try {
                    const result = await (clear ? p.onClear() : p.onSet(target));
                    if (!active) return;
                    if (clear) goal = null;
                    else if (result && Object.hasOwn(result, "goal")) goal = result.goal;
                    restore();
                    root.querySelector("button")?.focus();
                } catch (error) {
                    if (active) { custom = updateComponent(custom, { error: { messageKey: error.messageKey || "errors.app.internal", params: error.params } }); custom.hidden = false; }
                } finally { busy = false; for (const button of form.querySelectorAll("button")) button.disabled = false; }
            };
            const save = createComponent("button", { labelKey: "common.action.save", variant: "primary" });
            save.type = "submit";
            const cancel = createComponent("button", { labelKey: "common.action.cancel", variant: "ghost", onClick: () => { restore(); root.querySelector("button")?.focus(); } });
            form.append(actions([save, cancel, p.onClear ? createComponent("button", { labelKey: "common.goal.clear", variant: "ghost", onClick: () => commit(true) }) : null].filter(Boolean)));
            form.addEventListener("submit", (event) => { event.preventDefault(); commit(); });
            root.replaceChildren(form);
            picker.querySelector("select").focus();
        };
        root._dispose = () => { active = false; };
        restore();
        return root;
    },
    runRow(p) {
        const summary = p.summary;
        if (p.oneLine) {
            const state = summary.active ? "running" : summary.result === "ok" ? "ok" : summary.result === "skipped" ? "skip" : "fail";
            const resultKey = summary.result_key || "common.run.active";
            const when = el("span", { class: "run-when", text: p.timeOnly ? services.i18n.formatTime(summary.started_at) : `${services.i18n.formatDate(summary.started_at)} ${services.i18n.formatTime(summary.started_at)}` });
            const mark = !summary.active && summary.result === "partial" ? el("span", { class: "sr-only", text: t(resultKey) }) : el("span", { class: "run-status", "data-status": state, role: "img", "aria-label": t(resultKey) }, icon(state === "ok" ? "check" : state === "fail" ? "close" : state === "running" ? "live" : "minus"));
            const root = el("button", { class: "run-row is-one-line", type: "button", "data-run-id": summary.run_id }, [when, mark]);
            if (Number.isFinite(summary.points_earned)) root.append(el("span", { class: "run-points tabular", text: t("common.points.total", { count: summary.points_earned }) }));
            root.append(icon("chevron-right"));
            root.addEventListener("click", () => p.onOpen?.(summary.run_id));
            return root;
        }
        const root = el("button", { class: "run-row", type: "button" });
        const date = el("span", { text: services.i18n.formatDate(summary.started_at) });
        const time = el("span", { class: "muted", text: ` ${services.i18n.formatTime(summary.started_at)}` });
        const result = p.resultText ?? (summary.result_key ? t(summary.result_key) : t("common.run.active"));
        const copy = el("span", { class: "run-copy" }, [...(p.separateResult ? [el("span", { class: "run-when" }, [date, time])] : [date, time]), el("span", { class: "run-result", text: `${p.separateResult ? "" : " "}${result}` })]);
        if (p.meta) copy.append(el("span", { class: "run-meta", text: p.meta }));
        for (const problem of p.problemLines || []) copy.append(el("span", { class: "run-problem", text: problem }));
        root.append(icon(summary.active ? "live" : "history"), copy);
        if (!p.hidePoints) root.append(el("span", { class: "tabular", text: summary.points_earned === null ? unknown() : t("common.points.earned", { count: summary.points_earned }) }));
        root.append(icon("chevron-right"));
        root.addEventListener("click", () => p.onOpen?.(summary.run_id));
        return root;
    },
    logPanel(p) {
        const root = el("section", { class: "log-panel", dir: "ltr" });
        const text = el("pre", { class: "log-text", dir: "ltr", hidden: !p.open, "aria-label": t("common.log.title") });
        const toggle = el("button", { class: "log-toggle", type: "button", "aria-expanded": Boolean(p.open) }, [el("span", { text: t("common.log.title") }), icon(p.open ? "chevron-up" : "chevron-down")]);
        const status = el("p", { class: "field-error", hidden: true, role: "status" });
        const more = createComponent("button", { labelKey: "common.log.load_more", variant: "ghost" });
        more.classList.add("log-more");
        let opened = Boolean(p.open);
        let active = true;
        let loading = false;
        let loaded = false;
        let eof = false;
        let log = { text: "", next_offset: 0 };
        let queue = Promise.resolve();
        let pendingPages = 0;
        const refresh = () => {
            text.hidden = !opened;
            text.textContent = log.text;
            more.hidden = !opened || (loaded && eof);
            more.disabled = loading;
            more.setAttribute("aria-busy", String(loading));
        };
        const enqueue = (fn) => {
            queue = queue.then(async () => {
                if (!active) return;
                loading = true;
                refresh();
                try { await fn(); if (active) status.hidden = true; }
                catch (error) { if (active) { status.textContent = t(error.messageKey || "errors.app.internal", error.params); status.hidden = false; } }
                finally { loading = false; if (active) refresh(); }
            });
        };
        const load = () => {
            if (!p.runId || !opened || loading || pendingPages > 0) return;
            pendingPages++;
            enqueue(async () => {
                try {
                    const page = await services.bridge.call("get_run_log", { run_id: p.runId, offset: log.next_offset });
                    const joined = await appendLogPage(log, page, p.runId, services.bridge.call);
                    if (active) { log = joined; loaded = true; eof = page.eof === true; }
                } finally { pendingPages--; }
            });
        };
        toggle.addEventListener("click", () => {
            opened = !opened;
            toggle.setAttribute("aria-expanded", String(opened));
            toggle.lastChild.replaceWith(icon(opened ? "chevron-up" : "chevron-down"));
            p.onToggle?.(opened);
            refresh();
            if (!loaded) load();
        });
        const off = services.bridge.on("run.log", (data) => {
            if (data.run_id !== p.runId || !active) return;
            enqueue(async () => {
                const joined = await joinLog(log, data, services.bridge.call);
                if (active) { log = joined; loaded = true; }
            });
        });
        more.addEventListener("click", load);
        root.append(toggle, text, status, more);
        root._dispose = () => { active = false; off(); };
        refresh();
        load();
        return root;
    },
    banner(p) {
        const tone = p.tone || "info";
        return el("div", { class: `banner tone-${tone}`, role: ["error", "warning"].includes(tone) ? "alert" : "status" }, [icon(tone === "success" ? "check" : tone === "error" ? "error" : tone), el("p", { class: "banner-message", text: t(p.messageKey, p.params) }), actions(p.actions)]);
    },
    emptyState(p) { return el("section", { class: "empty-state" }, [p.icon ? icon(p.icon) : null, el("h2", { text: t(p.titleKey) }), p.bodyKey ? el("p", { text: t(p.bodyKey) }) : null, p.action instanceof Node ? p.action : p.action ? createComponent("button", p.action) : null]); },
    progressRing(p) {
        const value = Math.max(0, Math.min(1, Number(p.value) || 0));
        return el("div", { class: "progress-ring", role: "progressbar", "aria-label": t(p.labelKey, p.params), "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": Math.round(value * 100) }, [svg("svg", { viewBox: "0 0 48 48", "aria-hidden": "true" }, [svg("circle", { class: "ring-track", cx: 24, cy: 24, r: 20 }), svg("circle", { class: "ring-fill", cx: 24, cy: 24, r: 20, "stroke-dasharray": `${value * 125.664} 125.664` })]), el("span", { text: t(p.labelKey, p.params) })]);
    },
    accountRow(p) {
        const a = p.account;
        const statusKey = a.paused ? "common.account.paused" : a.signed_in === false ? "common.account.signed_out" : a.signed_in === true ? "common.account.ready" : "common.account.unknown";
        return el("div", { class: "account-row", "data-account": a.id }, [accountAvatar(a), el("div", { class: "account-row-copy" }, [el("p", { class: "account-row-label", text: a.label }), a.email ? el("p", { class: "account-row-email" }, el("bdi", { dir: "auto", text: a.email })) : null, el("p", { class: "account-row-status", text: t(statusKey) })]), el("span", { class: "account-row-balance tabular", text: a.balance === null ? t("accounts.balance.none") : t("common.points.total", { count: a.balance }) }), actions(p.actions)]);
    },
    riskNotice() { return el("aside", { class: "risk-notice" }, [icon("warning"), el("p", { text: t("common.risk_notice") })]); },
    spinner(p) { return el("span", { class: "spinner", role: "status" }, [el("span", { class: "spinner-disc", "aria-hidden": "true" }), el("span", { class: "sr-only", text: t(p.labelKey) })]); },
};

export function createComponent(name, props = {}) {
    const node = builders[name](props);
    records.set(node, { name, props });
    return node;
}
export function updateComponent(node, props = {}) {
    const record = records.get(node);
    if (!record) throw new Error("Unknown component instance");
    const nextProps = { ...record.props, ...props };
    if (record.name === "textField") {
        if (!Object.hasOwn(props, "value")) nextProps.value = node.querySelector("input").value;
        node._fieldUpdate(nextProps);
        records.set(node, { name: record.name, props: nextProps });
        return node;
    }
    const next = builders[record.name](nextProps);
    const focused = node.contains(document.activeElement);
    dispose(node);
    node.replaceWith(next);
    records.set(next, { name: record.name, props: nextProps });
    if (focused) (next.querySelector("button, input, select") || next).focus();
    return next;
}
export function dispose(root) {
    root._dispose?.();
    for (const node of root.querySelectorAll("*")) node._dispose?.();
}

export function showToast({ tone = "info", messageKey, params, timeoutMs = 5000 }) {
    const region = document.getElementById("toasts");
    const item = builders.banner({ tone, messageKey, params });
    item.classList.add("toast");
    const close = builders.iconButton({ icon: "close", labelKey: "common.action.dismiss", onClick: () => item.remove() });
    item.append(close);
    region.append(item);
    if (timeoutMs > 0) {
        let timer = setTimeout(() => item.remove(), timeoutMs);
        const pause = () => { clearTimeout(timer); timer = null; };
        const resume = () => { if (!timer) timer = setTimeout(() => item.remove(), timeoutMs); };
        item.addEventListener("pointerenter", pause);
        item.addEventListener("focusin", pause);
        item.addEventListener("pointerleave", resume);
        item.addEventListener("focusout", resume);
        window.addEventListener("pagehide", pause, { once: true });
    }
    return item;
}

const dialogRequests = [];
let activeDialog = null;

function dialogTabbables(dialog) {
    return [...dialog.querySelectorAll("button, input, select, textarea, a[href], [tabindex]")].filter((node) => !node.disabled && node.tabIndex >= 0 && !node.closest("[hidden], [inert]") && node.getClientRects().length && getComputedStyle(node).visibility !== "hidden").sort((first, second) => {
        const a = first.tabIndex > 0 ? first.tabIndex : Infinity;
        const b = second.tabIndex > 0 ? second.tabIndex : Infinity;
        return a === b ? 0 : a - b;
    });
}

function nextDialog() {
    if (activeDialog || !dialogRequests.length) return;
    const request = dialogRequests.shift();
    const { titleKey, bodyKey, params = {}, actions: items = [] } = request.props;
    const dialog = document.getElementById("dialog");
    activeDialog = request;
    let result = "cancel";
    let closing = false;
    const finish = (value) => {
        if (closing || activeDialog !== request) return;
        closing = true;
        result = value;
        dialog.close(String(value));
    };
    const trap = (event) => {
        if (event.key === "Escape") { event.preventDefault(); finish("cancel"); return; }
        if (event.key !== "Tab") return;
        event.preventDefault();
        const nodes = dialogTabbables(dialog);
        if (!nodes.length) { dialog.focus(); return; }
        const index = nodes.indexOf(document.activeElement);
        const next = index < 0 ? (event.shiftKey ? nodes.length - 1 : 0) : (index + (event.shiftKey ? -1 : 1) + nodes.length) % nodes.length;
        nodes[next].focus();
    };
    const cancel = (event) => { event.preventDefault(); finish("cancel"); };
    const close = () => {
        dialog.removeEventListener("keydown", trap, true);
        dialog.removeEventListener("cancel", cancel);
        dialog.removeEventListener("close", close);
        activeDialog = null;
        const opener = request.opener?.isConnected ? request.opener : document.getElementById("view");
        opener?.focus({ preventScroll: true });
        request.resolve(result);
        queueMicrotask(nextDialog);
    };
    dialog.replaceChildren(el("h2", { id: "dialog-title", text: t(titleKey, params) }), el("p", { id: "dialog-body", text: t(bodyKey, params) }), actions(items.map((item) => builders.button({ ...item, onClick: () => finish(item.id) }))));
    attrs(dialog, { "aria-labelledby": "dialog-title", "aria-describedby": "dialog-body", tabindex: -1 });
    dialog.addEventListener("keydown", trap, true);
    dialog.addEventListener("cancel", cancel);
    dialog.addEventListener("close", close, { once: true });
    try {
        dialog.showModal();
        (dialogTabbables(dialog)[0] || dialog).focus();
    } catch (error) {
        dialog.removeEventListener("keydown", trap, true);
        dialog.removeEventListener("cancel", cancel);
        dialog.removeEventListener("close", close);
        activeDialog = null;
        request.reject(error);
        queueMicrotask(nextDialog);
    }
}

export function openDialog(props) {
    const dialog = document.getElementById("dialog");
    const focused = document.activeElement;
    const opener = dialog.contains(focused) ? activeDialog?.opener || document.getElementById("view") : focused;
    return new Promise((resolve, reject) => {
        dialogRequests.push({ props, opener, resolve, reject });
        nextDialog();
    });
}
