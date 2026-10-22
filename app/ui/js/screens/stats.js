import { el } from "../dom.js";

export function mount(view, ctx) {
    const { bridge, i18n, store, router, components: c } = ctx;
    const t = i18n.t;
    const number = (value) => i18n.formatNumber(value);
    const root = el("section", { class: "screen stats-screen", "data-screen": "stats" });
    let alive = true;
    let revision = 0;
    let range = 30;
    let accountId = null;
    let data = null;
    let error = null;
    let loading = false;
    let selectedDate = null;
    const formatLocale = store.get("appInfo")?.format_locale || "en-US";
    const locale = new Intl.Locale(formatLocale);
    const weekStart = locale.weekInfo?.firstDay || locale.getWeekInfo?.().firstDay || 1;
    const taskName = (task) => t(`common.task.${task}`);
    const openRun = (id) => router.navigate(`#/history/${encodeURIComponent(id)}`);
    view.append(root);

    async function load() {
        const token = ++revision;
        loading = true;
        error = null;
        render();
        try {
            const next = await bridge.call("get_stats", { range, account_id: accountId, week_start: weekStart });
            if (!alive || token !== revision) return;
            data = next;
            accountId = next.account_id;
            store.set("stats", next);
        } catch (failure) {
            if (!alive || token !== revision) return;
            error = failure;
        } finally {
            if (alive && token === revision) { loading = false; render(); }
        }
    }

    function panel(titleKey, children) {
        return el("section", { class: "card stats-panel" }, [el("h2", { text: t(titleKey) }), ...[children].flat()]);
    }

    function metric(labelKey, value) {
        return Number.isFinite(value) ? el("div", { class: "stats-metric" }, [el("p", { class: "stats-metric-value tabular", text: number(value) }), el("p", { class: "stats-metric-label", text: t(labelKey) })]) : null;
    }

    function progressText(day) {
        if (!Number.isFinite(day.earned)) return null;
        if (Number.isFinite(day.possible) && day.possible >= 0) {
            const values = [el("span", { text: t("stats.day.points", { earned: number(day.earned), possible: number(day.possible) }) })];
            if (day.possible === 0) return el("span", { class: "stats-day-progress tabular" }, values);
            const percent = new Intl.NumberFormat(formatLocale, { style: "percent", maximumFractionDigits: 1 }).format(day.earned / day.possible);
            return el("span", { class: "stats-day-progress tabular" }, [...values, el("strong", { text: percent })]);
        }
        return el("span", { class: "stats-day-progress tabular", text: t("common.points.total", { count: day.earned }) });
    }

    function dayRuns(day) {
        const host = el("section", { class: "stats-day-runs", "aria-label": t("stats.day.runs", { date: i18n.formatDate(day.date) }) });
        const runs = (day.runs || []).filter((run) => run.kind === "run");
        for (const run of runs) {
            const entry = el("div", { class: "stats-run-entry" });
            const row = c.runRow.create({ summary: run, onOpen: openRun, oneLine: true, timeOnly: true });
            row.classList.add("stats-day-run");
            entry.append(row);
            if (["partial", "failed"].includes(run.result)) for (const problem of run.problems || []) {
                const reason = problem.reason_key ? t(problem.reason_key) : t(problem.task ? "errors.task.error" : "errors.run.failed");
                entry.append(el("p", { class: "stats-run-reason reason", text: problem.task ? `${taskName(problem.task)}: ${reason}` : reason }));
            }
            host.append(entry);
        }
        return host;
    }

    function daySources(day) {
        const tasks = ["daily_set", "explore_on_bing", "visual_search", "misc_cards", "quests", "searches", "bonus"];
        return day.sources?.runs > 0 ? el("dl", { class: "stats-source-list" }, tasks.map((task) => el("div", { class: "stats-source-row", "data-task": task }, [el("dt", { text: taskName(task) }), el("dd", { class: "tabular", text: number(day.sources.points[task]) })]))) : el("p", { class: "stats-source-empty", text: t("stats.sources.day_empty") });
    }

    function renderCalendar(days, selectDay) {
        const grid = el("div", { class: "stats-calendar-grid", role: "group", "aria-label": t("stats.calendar.title") });
        const names = el("div", { class: "stats-calendar-weekdays", "aria-hidden": "true" });
        const formatter = new Intl.DateTimeFormat(formatLocale, { weekday: "narrow" });
        for (let index = 0; index < 7; index++) names.append(el("span", { text: formatter.format(new Date(2026, 0, 5 + (weekStart - 1 + index) % 7)) }));
        if (days.length) {
            const firstDate = new Date(`${days[0].date}T12:00:00`);
            const offset = (firstDate.getDay() + 7 - weekStart % 7) % 7;
            for (let index = 0; index < offset; index++) grid.append(el("span", { class: "stats-calendar-spacer", "aria-hidden": "true" }));
        }
        const detail = el("div", { class: "stats-calendar-detail", "aria-live": "polite", id: "stats-calendar-detail" });
        const runHost = el("div", { class: "stats-day-runs-host", id: "stats-day-runs" });
        if (!days.some((day) => day.date === selectedDate)) selectedDate = data.today;
        const selectedSummary = (day) => {
            detail.replaceChildren(el("h3", { text: i18n.formatDate(day.date) }));
            const progress = progressText(day);
            if (progress) detail.append(progress);
        };
        const markers = { done: "check", missed: "close" };
        days.forEach((day, index) => {
            const state = t(`stats.state.${day.state}`);
            const date = i18n.formatDate(day.date);
            const label = t("stats.calendar.day", { date, state });
            const button = el("button", { id: `stats-day-${day.date}`, type: "button", class: `stats-day state-${day.state}${day.date === data.today ? " is-today" : ""}${selectedDate === day.date ? " is-selected" : ""}`, "data-date": day.date, "data-state": day.state, "aria-label": label, "aria-describedby": "stats-calendar-detail", "aria-pressed": String(selectedDate === day.date), "aria-controls": "stats-day-runs stats-day-sources", title: label });
            if (markers[day.state]) button.append(c.icon.create({ name: markers[day.state] }));
            else if (day.state === "today_open") button.append(el("span", { class: "stats-day-open", "aria-hidden": "true" }));
            const show = () => selectedSummary(day);
            if (selectedDate === day.date) show();
            button.addEventListener("pointerenter", show);
            button.addEventListener("focus", show);
            button.addEventListener("pointerleave", () => { const selected = days.find((entry) => entry.date === selectedDate); if (selected) selectedSummary(selected); });
            button.addEventListener("blur", () => { const selected = days.find((entry) => entry.date === selectedDate); if (selected) selectedSummary(selected); });
            button.addEventListener("click", () => {
                selectedDate = day.date;
                show();
                grid.querySelectorAll(".stats-day").forEach((node) => { node.setAttribute("aria-pressed", String(node === button)); node.classList.toggle("is-selected", node === button); });
                runHost.replaceChildren(dayRuns(day));
                selectDay(day);
            });
            button.addEventListener("keydown", (event) => {
                const step = { ArrowDown: 1, ArrowUp: -1, ArrowRight: document.documentElement.dir === "rtl" ? -7 : 7, ArrowLeft: document.documentElement.dir === "rtl" ? 7 : -7 }[event.key];
                const target = step ? days[index + step] : null;
                if (target) { event.preventDefault(); grid.querySelector(`[data-date="${target.date}"]`)?.focus(); }
            });
            grid.append(button);
        });
        const selected = days.find((day) => day.date === selectedDate);
        if (selected) { runHost.append(dayRuns(selected)); selectDay(selected); }
        const calendar = panel("stats.calendar.title", [el("div", { class: "stats-calendar-dates" }, [el("span", { text: days[0] ? i18n.formatDate(days[0].date) : "" }), el("span", { text: days.length ? i18n.formatDate(days.at(-1).date) : "" })]), el("div", { class: "stats-calendar" }, [names, grid]), detail, runHost]);
        calendar.classList.add("stats-calendar-panel");
        return calendar;
    }

    function render() {
        const focusId = root.contains(document.activeElement) ? document.activeElement.id : null;
        c.dispose(root);
        root.replaceChildren();
        const controls = [];
        if (data?.accounts.length > 1) controls.push(c.select.create({ id: "stats-account", labelKey: "stats.account", value: accountId || "all", options: [{ value: "all", labelKey: "stats.account.all" }, ...data.accounts.map((account) => ({ value: account.account_id, label: account.label }))], onChange: (value) => { accountId = value === "all" ? null : value; load(); } }));
        controls.push(c.segmented.create({ id: "stats-range", labelKey: "stats.range", value: String(range), options: [7, 30, 90].map((value) => ({ value: String(value), labelKey: `stats.range.${value}` })), onChange: (value) => { range = Number(value); load(); } }));
        root.append(el("header", { class: "screen-header" }, [el("h1", { text: t("stats.title") }), el("div", { class: "stats-controls" }, controls)]));
        root.setAttribute("aria-busy", String(loading));
        if (error) root.append(c.banner.create({ tone: "error", messageKey: error.messageKey || "errors.app.internal", params: error.params, actions: [{ labelKey: "common.action.retry", onClick: load }] }));
        if (!data) {
            if (loading) root.append(c.spinner.create({ labelKey: "stats.loading" }));
            else if (!error) root.append(c.emptyState.create({ icon: "chart", titleKey: "stats.empty.title", bodyKey: "stats.empty.body" }));
            return;
        }
        for (const warning of data.warnings) root.append(c.banner.create({ tone: "warning", messageKey: `stats.warning.${warning.code}` }));
        const { totals: metrics, days } = data;
        const selectedDays = days.slice(-data.range);
        root.append(el("div", { class: "stats-metrics" }, [metric("stats.week", metrics.week_earned), metric("stats.month", metrics.month_earned), metric("stats.streak.title", data.streak.current)]));
        const sourceHeading = el("h2", { id: "stats-day-sources-title" });
        const sourceRows = el("div", { class: "stats-sources-content" });
        const sources = el("section", { id: "stats-day-sources", class: "card stats-panel stats-sources-panel", "aria-labelledby": sourceHeading.id }, [sourceHeading, sourceRows]);
        const selectDay = (day) => {
            sourceHeading.textContent = t("stats.sources.day", { date: i18n.formatDate(day.date) });
            sources.dataset.date = day.date;
            sourceRows.replaceChildren(daySources(day));
        };
        root.append(el("div", { class: "stats-main-grid" }, [renderCalendar(selectedDays, selectDay), sources]));
        if (focusId) root.querySelector(`#${focusId}`)?.focus();
    }

    const unsubscribe = [bridge.on("overview.changed", load), bridge.on("run.finished", load)];
    load();
    return { update: load, unmount() { alive = false; revision++; unsubscribe.forEach((off) => off()); c.dispose(root); root.remove(); } };
}
