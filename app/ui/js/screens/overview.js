import { el } from "../dom.js";
import { scheduleTime } from "../schedule-time.js";
export { scheduleTime };

const calendarDay = (date) => Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());

export function snapshotDate(generatedAt, i18n, now = new Date()) {
    return calendarDay(new Date(generatedAt)) === calendarDay(now) ? i18n.t("overview.date_today") : i18n.formatDate(generatedAt);
}

export function mount(view, ctx) {
    const { bridge, i18n, store, router, components: c } = ctx;
    const t = i18n.t;
    const number = (value) => value === null || value === undefined ? t("common.value.unknown") : i18n.formatNumber(value);
    let selectedId = null;
    let chartMode = "balance";
    let active = true;
    let starting = false;
    let savingGoal = false;
    let chartNode = null;
    let revision = 0;

    function report(error) {
        if (active) c.toast.show({ tone: "error", messageKey: error.messageKey, params: error.params });
    }
    async function refresh() {
        const token = ++revision;
        try {
            const data = await bridge.call("get_overview");
            if (active && token === revision) store.set("overview", data);
        } catch (error) { report(error); }
    }
    async function startRun() {
        const overview = store.get("overview");
        const account = overview?.accounts.find((item) => item.account_id === selectedId);
        if (starting || !account || account.paused || overview.active_run || overview.signin_active || store.get("live")?.run?.active || ["signed_out", "terms_of_use"].includes(account.state?.unreadable)) return;
        starting = true;
        render();
        try {
            await bridge.call("start_run", selectedId ? { account_ids: [selectedId] } : {});
            if (active) router.navigate("#/live");
        } catch (error) { report(error); }
        finally {
            starting = false;
            if (active) {
                render();
                view.querySelector("#overview-run-now:not(:disabled)")?.focus({ preventScroll: true });
            }
        }
    }
    async function setGoal(target) {
        if (savingGoal || !selectedId) return;
        const accountId = selectedId;
        savingGoal = true;
        try {
            const data = await bridge.call("set_goal", { account_id: accountId, target });
            if (active) {
                const overview = store.get("overview");
                if (overview) store.set("overview", { ...overview, accounts: overview.accounts.map((account) => account.account_id === accountId ? { ...account, goal: data.goal } : account) });
            }
        } catch (error) { report(error); }
        finally {
            savingGoal = false;
            if (active) {
                render();
                view.querySelector(".overview-goal button")?.focus({ preventScroll: true });
            }
        }
    }
    function readable(account) {
        return Boolean(account.state && account.state_is_today && account.state.unreadable === null);
    }
    function openWork(account) {
        if (!readable(account)) return t("common.value.unknown");
        const state = account.state;
        const parts = [];
        const cards = state.daily_set_open + state.cards_open + state.quests_open;
        if (cards > 0) parts.push(t("overview.open.cards", { count: cards }));
        if (state.searches_earned !== null && state.searches_max !== null) {
            const remaining = Math.max(0, state.searches_max - state.searches_earned);
            if (remaining > 0) parts.push(t("overview.open.search_points", { count: remaining }));
        } else parts.push(t("overview.open.searches_unknown"));
        return parts.length ? i18n.formatList(parts) : t("overview.open.none");
    }
    function taskRows(account) {
        return account.tasks_today.map((task) => {
            const row = { ...task };
            if (task.still_open === true && !["skip", "fail", "off"].includes(task.status)) row.status = "open";
            if (!readable(account) || row.status !== "open") return row;
            const state = account.state;
            if (task.task === "searches" && state.searches_earned !== null && state.searches_max !== null) {
                row.progress = t("overview.task.search_progress", { earned: number(state.searches_earned), max: number(state.searches_max) });
            }
            const count = { daily_set: state.daily_set_open, misc_cards: state.cards_open, quests: state.quests_open }[task.task];
            if (typeof count === "number") row.progress = t("overview.task.open_count", { count });
            return row;
        });
    }
    function stateNotice(account) {
        const code = account.state?.unreadable;
        if (code) {
            const wholePage = ["signed_out", "terms_of_use", "page_blank"].includes(code);
            const params = wholePage ? { account: account.label } : { sections: i18n.formatList(code.split(",").map((section) => t(`common.section.${section}`))) };
            return c.banner.create({ tone: "warning", messageKey: wholePage ? `errors.state.${code}` : "errors.state.sections", params, actions: [
                { labelKey: "overview.accounts", variant: "ghost", onClick: () => router.navigate("#/accounts") },
            ] });
        }
        return null;
    }
    function scheduleRegion(schedule, generatedAt) {
        const healthy = schedule.mode !== "manual" && schedule.task_exists && schedule.in_sync && !schedule.error_code;
        const region = el("section", { class: "summary-divider overview-schedule", "data-region": "schedule" });
        const labelKey = schedule.next_is_check ? "overview.next_check" : "overview.next_run";
        region.append(el("p", { class: "subtle", text: t(labelKey) }));
        const when = scheduleTime(schedule, generatedAt, i18n, store.get("appInfo")?.format_locale);
        region.append(el("a", { class: "next-time overview-next-link", href: "#/settings/schedule", text: when }));
        region.append(el("p", { class: "next-description", text: t(`overview.schedule.${schedule.mode}`) }));
        if (healthy) region.append(el("p", { class: "subtle", text: t("overview.schedule.independent") }));
        else if (schedule.mode !== "manual") region.append(el("a", { class: "overview-schedule-warning", href: "#/settings/schedule", text: t(schedule.error_code ? "overview.schedule.error" : !schedule.task_exists ? "overview.schedule.missing" : "overview.schedule.out_of_sync") }));
        if (schedule.legacy_task) region.append(el("a", { class: "overview-schedule-warning", href: "#/settings/schedule", text: t("overview.schedule.legacy") }));
        return region;
    }
    function goalRegion(account) {
        const region = el("section", { class: "summary-divider overview-goal", "data-region": "goal" });
        region.append(c.goalCard.create({ goal: account.goal, onSet: savingGoal ? null : setGoal, onClear: savingGoal ? null : () => setGoal(null) }));
        if (savingGoal) region.append(el("p", { role: "status", text: t("overview.goal.saving") }));
        return region;
    }
    function streaksRegion(streaks) {
        if (!streaks) return null;
        const known = (value) => Number.isInteger(value) && value >= 0;
        const captured = new Date(streaks.captured_at);
        const today = calendarDay(captured) === calendarDay(new Date());
        const content = [];
        const bonus = streaks.big_bonus;
        if (bonus && [bonus.points, bonus.stamps_total, bonus.stamps_collected].some(known)) {
            const copy = el("div", { class: "overview-stamps-copy" }, el("h3", { text: t("overview.streaks.big_bonus") }));
            if (known(bonus.points)) copy.append(el("p", { class: "overview-bonus-points tabular", text: t("common.points.total", { count: bonus.points }) }));
            const totalKnown = known(bonus.stamps_total), collectedKnown = known(bonus.stamps_collected);
            let stampLabel = null;
            if (totalKnown && collectedKnown) stampLabel = t("overview.streaks.stamps_progress", { collected: number(bonus.stamps_collected), total: number(bonus.stamps_total) });
            else if (totalKnown) stampLabel = t("overview.streaks.stamps_total", { count: bonus.stamps_total });
            else if (collectedKnown) stampLabel = t("overview.streaks.stamps_collected", { count: bonus.stamps_collected });
            if (stampLabel) copy.append(el("p", { class: "overview-stamps-label", text: stampLabel }));
            const block = el("div", { class: "overview-streak-bonus" }, copy);
            if (totalKnown && collectedKnown && bonus.stamps_total > 0) {
                const stamps = el("div", { class: "overview-stamp-grid", role: "img", "aria-label": stampLabel, "data-collected-known": collectedKnown });
                for (let index = 0; index < bonus.stamps_total; index++) {
                    const collected = collectedKnown && index < bonus.stamps_collected;
                    stamps.append(el("span", { class: `overview-stamp${collected ? " is-collected" : ""}`, "aria-hidden": "true" }, collected ? c.icon.create({ name: "check" }) : []));
                }
                block.append(stamps);
            }
            content.push(block);
        }
        const names = { bing_search: "overview.streaks.bing_search", daily_set: "common.task.daily_set", visual_search: "common.task.visual_search" };
        const rows = el("ul", { class: "overview-streak-list" });
        for (const id of Object.keys(names)) {
            const item = streaks.items?.find((entry) => entry.id === id);
            if (!item || !known(item.day)) continue;
            const nameId = `overview-streak-${id}`;
            const copy = el("div", { class: "overview-streak-copy" }, el("h3", { id: nameId, class: "overview-streak-name", text: t(names[id]) }));
            if (known(item.next_points)) copy.append(el("p", { class: "overview-streak-next", text: t("overview.streaks.next", { count: item.next_points }) }));
            const progress = el("div", { class: "overview-streak-progress" });
            const lengthKnown = known(item.length);
            const dayLabel = lengthKnown ? t("overview.streaks.day_of", { day: number(item.day), length: number(item.length) }) : t("overview.streaks.day", { day: number(item.day) });
            progress.append(el("p", { class: "overview-streak-day tabular", text: dayLabel, "aria-hidden": lengthKnown && item.length > 0 ? "true" : null }));
            if (lengthKnown && item.length > 0) {
                const track = el("div", { class: "overview-streak-track", role: "img", "aria-label": dayLabel });
                for (let index = 0; index < item.length; index++) track.append(el("span", { class: `overview-streak-step${index < item.day ? " is-complete" : ""}`, "aria-hidden": "true" }));
                progress.append(track);
            }
            if (today && known(item.today_done) && known(item.today_total)) progress.append(el("p", { class: "overview-streak-today tabular", text: t("overview.streaks.today", { done: number(item.today_done), total: number(item.today_total) }) }));
            rows.append(el("li", { class: "overview-streak-row", "data-streak": id, "aria-labelledby": nameId }, [copy, progress]));
        }
        if (rows.childElementCount) content.push(rows);
        if (Number.isFinite(captured.getTime())) content.push(el("p", { class: "overview-streak-captured" }, el("time", { datetime: streaks.captured_at, text: t("overview.streaks.as_of", { date: i18n.formatDate(captured), time: i18n.formatTime(captured) }) })));
        const card = c.card.create({ titleKey: "overview.streaks.title", children: content });
        card.id = "overview-streaks";
        card.classList.add("overview-streaks");
        card.dataset.region = "streaks";
        const heading = card.querySelector("h2");
        heading.id = "overview-streaks-title";
        card.setAttribute("aria-labelledby", heading.id);
        return card;
    }
    function render() {
        if (!active) return;
        const focusedId = view.contains(document.activeElement) ? document.activeElement.id : null;
        c.dispose(view);
        const overview = store.get("overview");
        const root = el("section", { class: "screen overview-screen", "data-screen": "overview" });
        const header = el("header", { class: "screen-header" }, el("div", {}, [el("h1", { text: t("overview.title") }), overview ? el("p", { text: snapshotDate(overview.generated_at, i18n) }) : null]));
        root.append(header);
        if (!overview) {
            root.append(c.emptyState.create({ icon: "error", titleKey: "overview.load.title", bodyKey: "overview.load.body", action: { labelKey: "common.action.retry", onClick: refresh } }));
            view.replaceChildren(root);
            return;
        }
        const accounts = overview.accounts;
        if (!accounts.some((account) => account.account_id === selectedId)) selectedId = accounts[0]?.account_id || null;
        const account = accounts.find((item) => item.account_id === selectedId);
        if (!account) {
            root.append(c.emptyState.create({ icon: "accounts", titleKey: "overview.no_accounts.title", bodyKey: "overview.no_accounts.body", action: { labelKey: "overview.accounts", variant: "primary", onClick: () => router.navigate("#/accounts") } }));
            view.replaceChildren(root);
            return;
        }
        if (accounts.length > 1) header.append(c.select.create({ id: "overview-account", labelKey: "overview.account", value: selectedId, options: accounts.map((item) => ({ value: item.account_id, label: item.label })), onChange: (id) => { selectedId = id; render(); } }));
        const notice = stateNotice(account);
        if (notice) root.append(el("div", { class: "overview-notice" }, notice));
        const todayKnown = Boolean(account.state_is_today && account.state && !["signed_out", "terms_of_use", "page_blank"].includes(account.state.unreadable) && (account.state.unreadable || Number.isFinite(account.state.earned_today)));
        let plan = null;
        if (todayKnown) {
            const planContent = [el("div", { class: "open-work", "data-region": "open" }, [el("p", { text: t("overview.still_open") }), el("strong", { text: openWork(account) })])];
            planContent.push(c.taskList.create({ tasks: taskRows(account) }));
            plan = c.card.create({ titleKey: "overview.tasks_today", children: planContent });
            plan.classList.add("overview-tasks");
            plan.dataset.region = "tasks";
        }
        const live = store.get("live")?.run;
        const running = Boolean(overview.active_run || live?.active);
        const signinRequired = ["signed_out", "terms_of_use"].includes(account.state?.unreadable);
        const disabled = running || Boolean(overview.signin_active) || account.paused || signinRequired || starting;
        const runButton = c.button.create({ labelKey: "overview.run_now", variant: "primary", size: "large", icon: "live", disabled, busy: starting, onClick: startRun });
        runButton.id = "overview-run-now";
        const balanceKnown = account.state?.balance !== null && account.state?.balance !== undefined;
        const balance = c.stat.create({ labelKey: "overview.balance", value: number(account.state?.balance) });
        if (!balanceKnown) balance.classList.add("is-unknown");
        const balanceContent = todayKnown || balanceKnown ? el("div", { class: "overview-balance" }, [balance, balanceKnown ? el("p", { class: "balance-unit", text: t("overview.points_unit") }) : null]) : null;
        if (!todayKnown && balanceKnown) balanceContent.append(el("p", { class: "overview-balance-captured subtle" }, el("time", { datetime: account.state.at, text: t("overview.streaks.as_of", { date: i18n.formatDate(account.state.at), time: i18n.formatTime(account.state.at) }) })));
        const runReason = signinRequired ? el("div", { class: "overview-run-reason" }, [
            el("p", { text: t(account.state.unreadable === "signed_out" ? "overview.run_signed_out" : "overview.run_terms") }),
            el("a", { href: "#/accounts", text: t("overview.accounts") }),
        ]) : account.paused ? el("a", { href: "#/accounts", class: "overview-run-reason", text: t("overview.account_paused") }) : null;
        const summary = c.card.create({ children: [
            balanceContent,
            todayKnown ? el("p", { class: "earned-line", "data-region": "earned" }, [el("span", { text: t("overview.earned_today") }), el("strong", { text: account.state_is_today && account.state?.earned_today !== null && account.state?.earned_today !== undefined ? t("overview.earned_value", { points: number(account.state.earned_today) }) : t("common.value.unknown") })]) : null,
            scheduleRegion(overview.schedule, overview.generated_at),
            runButton,
            running ? el("a", { href: "#/live", class: "overview-run-reason", text: t("overview.run_active") }) : overview.signin_active ? el("p", { class: "overview-run-reason", text: t("overview.signin_active") }) : runReason,
            goalRegion(account),
        ] });
        summary.classList.add("overview-summary");
        summary.dataset.region = "summary";
        root.append(el("div", { class: `overview-spread${todayKnown ? "" : " is-known-only"}` }, [plan, summary]));
        const streaks = todayKnown ? streaksRegion(overview.streaks) : null;
        if (streaks) root.append(streaks);
        const choice = c.segmented.create({ id: "overview-chart-mode", labelKey: "overview.chart.mode", value: chartMode, options: [{ value: "balance", labelKey: "common.chart.balance" }, { value: "earned", labelKey: "common.chart.earned" }], onChange: (value) => { chartMode = value; chartNode = c.pointsChart.update(chartNode, { series: account.chart, mode: chartMode }); } });
        chartNode = c.pointsChart.create({ series: account.chart, mode: chartMode });
        const chart = c.card.create({ titleKey: "overview.chart.title", actions: [choice], children: [chartNode] });
        chart.classList.add("overview-chart");
        chart.dataset.region = "chart";
        root.append(chart);
        if (new URLSearchParams(window.location.search).has("mock")) root.append(el("p", { class: "synthetic-note", text: t("overview.synthetic") }));
        view.replaceChildren(root);
        if (focusedId) document.getElementById(focusedId)?.focus({ preventScroll: true });
    }
    let liveActive = Boolean(store.get("live")?.run?.active);
    const unsubscribe = [store.on("overview", render), store.on("live", (value) => {
        const nextActive = Boolean(value?.run?.active);
        if (nextActive !== liveActive) { liveActive = nextActive; render(); }
    })];
    render();
    return { update: render, unmount() { active = false; revision++; unsubscribe.forEach((off) => off()); c.dispose(view); } };
}
