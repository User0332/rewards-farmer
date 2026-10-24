import { el } from "../dom.js";

export function mount(view, ctx) {
    const { bridge, i18n, store, router, components: c } = ctx;
    const t = i18n.t;
    const number = (value) => Number.isFinite(value) ? i18n.formatNumber(value) : "";
    let active = true;
    let epoch = 0;
    let request = 0;
    let stoppingId = null;
    let stopBusy = false;
    let previousRun;
    let previousLast;
    let logRunId = null;
    let logNode = null;
    const root = el("section", { class: "screen live-screen", "data-screen": "live" });
    const subtitle = el("p");
    const heading = el("div", {}, [el("h1", { text: t("live.title") }), subtitle]);
    const header = el("header", { class: "screen-header" }, heading);
    const content = el("div", { class: "screen-stack live-content" });
    const logHost = el("div", { class: "live-log-host" });
    root.append(header, content, logHost);
    view.append(root);

    function report(error) {
        if (active) c.toast.show({ tone: "error", messageKey: error.messageKey, params: error.params });
    }
    async function refresh() {
        const token = ++request;
        const currentEpoch = epoch;
        try {
            const data = await bridge.call("get_live");
            if (!active || token !== request || epoch !== currentEpoch) return;
            const current = store.get("live") || { last: null };
            store.set("live", { ...current, run: data.run, log: data.log });
            const last = current.last || store.get("overview")?.last_run;
            if (!data.run && last) {
                const finalEpoch = epoch;
                const run = await bridge.call("get_run", { run_id: last.run_id });
                if (active && token === request && epoch === finalEpoch) store.set("live", { ...store.get("live"), run, last });
            }
        } catch (error) { report(error); }
    }
    async function stopRun(runId) {
        const run = store.get("live")?.run;
        if (stopBusy || stoppingId === runId || run?.run_id !== runId || !run.active || !["app", "scheduled"].includes(run.trigger)) return;
        stoppingId = runId;
        stopBusy = true;
        render();
        try { await bridge.call("stop_run", { run_id: runId }); }
        catch (error) { stoppingId = null; report(error); }
        finally {
            stopBusy = false;
            if (active) {
                render();
                view.querySelector("#live-run-action:not(:disabled)")?.focus({ preventScroll: true });
            }
        }
    }
    function stateNotice(state, account) {
        if (!state?.unreadable) return null;
        const whole = ["signed_out", "terms_of_use", "page_blank"].includes(state.unreadable);
        return c.banner.create({ tone: "warning", messageKey: whole ? `errors.state.${state.unreadable}` : "errors.state.sections", params: whole ? { account: account.label } : { sections: i18n.formatList(state.unreadable.split(",").map((section) => t(`common.section.${section}`))) } });
    }
    function decisionNotice(account) {
        return account.decision ? el("p", { class: "live-decision", text: t(account.decision.reason_key, { points: account.decision.open_points, min_points: number(account.decision.min_points) }) }) : null;
    }
    function errorNotice(account) {
        return account.error ? c.banner.create({ tone: "error", messageKey: account.error.message_key, params: { account: account.label } }) : null;
    }
    function warningNotice(warning, run) {
        const account = run.accounts.find((item) => item.account_id === warning.account_id);
        return c.banner.create({ tone: "warning", messageKey: warning.message_key, params: { account: account?.label || i18n.formatList(run.accounts.map((item) => item.label)) } });
    }
    function accountPlan(account, run, foldDiagnostics) {
        const state = account.state_after || account.state_before;
        const checkOnly = run.kind === "check" && account.decision?.run === false && account.tasks.every(task => ["not_run", "off"].includes(task.status));
        const tasks = account.tasks.map((task) => {
            if (run.kind === "check" && (account.status === "skipped" || account.decision?.run === false)) return { ...task, reason_key: null };
            if (task.task === "searches" && Number.isFinite(account.search?.earned) && Number.isFinite(account.search?.max) && task.status === "running") return { ...task, progress: t("live.search_progress", { earned: number(account.search.earned), max: number(account.search.max) }) };
            return task;
        });
        const head = el("header", { class: "live-account-heading" }, [
            el("h2", { text: account.label }),
            run.accounts.length > 1 && Number.isFinite(account.points_earned) ? el("p", { class: "live-account-points tabular", text: t("common.points.earned", { count: account.points_earned }) }) : null,
        ]);
        const children = [head];
        if (!foldDiagnostics) {
            children.push(checkOnly ? decisionNotice(account) : null, errorNotice(account), stateNotice(state, account));
        }
        if (!checkOnly) children.push(el("ul", { class: "live-task-list", "aria-label": t("overview.tasks_today") }, tasks.map((task) => {
            const state = i18n.has(`common.task_status.${task.status}`) ? `common.task_status.${task.status}` : "common.task_status.none";
            const names = { ok: "check", fail: "close", running: "live", skip: "minus", off: "minus", not_run: "minus", pending: "minus" };
            const row = el("div", { class: "live-task-row", "data-task": task.task, "data-status": task.status }, [el("span", { class: "live-task-mark", role: "img", "aria-label": t(state) }, [c.icon.create({ name: names[task.status] })]), el("span", { class: "live-task-name", text: t(task.name_key) }), task.status === "running" && typeof task.progress === "string" ? el("span", { class: "live-task-progress tabular", text: task.progress }) : null]);
            return el("li", { class: "live-task-entry" }, [row, task.status === "fail" && task.reason_key ? el("p", { class: "live-task-reason", text: t(task.reason_key) }) : null]);
        })));
        const card = c.card.create({ children });
        card.classList.add("live-account-plan");
        card.dataset.account = account.account_id;
        return card;
    }
    function render() {
        if (!active) return;
        const focusedId = content.contains(document.activeElement) ? document.activeElement.id : null;
        const live = store.get("live");
        const run = live?.run;
        const last = live?.last || store.get("overview")?.last_run;
        previousRun = run;
        previousLast = live?.last;
        c.dispose(content);
        content.replaceChildren();
        subtitle.textContent = run ? `${i18n.formatDate(run.started_at)} ${i18n.formatTime(run.started_at)}` : "";
        subtitle.hidden = !run;
        if (!run) {
            delete root.dataset.run;
            delete root.dataset.active;
            content.append(c.emptyState.create({ titleKey: "live.idle.title", action: { labelKey: "live.overview", onClick: () => router.navigate("#/overview") } }));
            if (last) content.append(c.card.create({ titleKey: "live.last_run", children: [c.runRow.create({ summary: last, oneLine: true, onOpen: (id) => router.navigate(`#/history/${encodeURIComponent(id)}`) })] }));
        } else {
            root.dataset.run = run.run_id;
            root.dataset.active = String(run.active);
            if (!run.active) stoppingId = null;
            const stoppable = ["app", "scheduled"].includes(run.trigger);
            const waiting = run.active && stoppable && (run.stop_requested || stoppingId === run.run_id);
            const action = run.active ? stoppable ? c.button.create({ labelKey: waiting ? "live.stop_waiting" : "live.stop", variant: "secondary", icon: "close", disabled: waiting, busy: stopBusy, onClick: () => stopRun(run.run_id) }) : null : c.button.create({ labelKey: "live.history", variant: "ghost", onClick: () => router.navigate(`#/history/${encodeURIComponent(run.run_id)}`) });
            if (action) action.id = "live-run-action";
            const failed = !run.active && ["failed", "crashed", "config_error"].includes(run.result);
            const primaryAccount = failed ? run.accounts.find((account) => account.error) : null;
            const result = !run.active && run.result_key ? c.banner.create({ tone: failed ? "error" : run.result === "ok" ? "success" : "info", messageKey: run.result_key }) : null;
            if (failed) {
                const primary = primaryAccount ? errorNotice(primaryAccount) : result;
                if (primary) { primary.classList.add("live-primary-error"); content.append(primary); }
            }
            const points = Number.isFinite(run.points_earned) ? c.stat.create({ labelKey: "live.points_earned", value: number(run.points_earned) }) : null;
            const summary = el("section", { class: "live-summary", "aria-label": t("live.title") }, [
                points, el("div", { class: "live-progress" }, [action]),
                waiting ? el("p", { class: "live-stop-note", role: "status", text: t("live.stop_note") }) : null,
                run.active && !stoppable ? el("p", { class: "live-external-run", text: t("errors.app.run_not_stoppable") }) : null,
            ]);
            summary.dataset.region = "points";
            content.append(summary);
            if (failed) {
                const toggle = el("summary", { class: "log-toggle" }, [el("span", { text: t("live.more_details") }), c.icon.create({ name: "chevron-down" })]);
                const details = el("details", { class: "live-more-details log-panel" }, toggle);
                details.addEventListener("toggle", () => toggle.replaceChild(c.icon.create({ name: details.open ? "chevron-up" : "chevron-down" }), toggle.lastElementChild));
                const diagnostics = el("div", { class: "screen-stack live-diagnostics" });
                if (primaryAccount && result) diagnostics.append(result);
                for (const warning of run.warnings) diagnostics.append(warningNotice(warning, run));
                for (const account of run.accounts) {
                    if (account !== primaryAccount && account.error) diagnostics.append(errorNotice(account));
                    const decision = decisionNotice(account);
                    const notice = stateNotice(account.state_after || account.state_before, account);
                    if (decision) diagnostics.append(decision);
                    if (notice) diagnostics.append(notice);
                }
                if (diagnostics.childElementCount) { details.append(diagnostics); content.append(details); }
            } else for (const warning of run.warnings) content.append(warningNotice(warning, run));
            const plans = el("div", { class: "live-plans" }, run.accounts.map((account) => accountPlan(account, run, failed)));
            content.append(plans);
        }
        const nextLogId = run?.run_id || last?.run_id || null;
        if (nextLogId !== logRunId) {
            if (logNode) c.dispose(logNode);
            logHost.replaceChildren();
            logRunId = nextLogId;
            logNode = nextLogId ? c.logPanel.create({ runId: nextLogId, open: false }) : null;
            if (logNode) logHost.append(logNode);
        }
        if (focusedId) document.getElementById(focusedId)?.focus({ preventScroll: true });
    }
    const offLive = store.on("live", (value) => {
        epoch++;
        if (value?.run !== previousRun || value?.last !== previousLast) render();
    });
    const offOverview = store.on("overview", () => { if (!store.get("live")?.run) render(); });
    render();
    refresh();
    return { update: render, unmount() { active = false; request++; offLive(); offOverview(); c.dispose(root); } };
}
