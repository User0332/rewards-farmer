import { el, svg } from "../dom.js";
import { TASK_ICONS, localDay, groupDays, needsAttention, taskShares, taskGroups, bandLabelFits } from "./history-points.js";

export function mount(view, ctx) {
    const { bridge, i18n, router, components: c } = ctx;
    const { t, formatDate, formatTime, formatNumber } = i18n;
    const root = el("section", { class: "screen screen-stack history-screen", "data-screen": "history", "aria-busy": "true" });
    view.replaceChildren(root);
    let alive = true;
    let generation = 0;
    let params = ctx.params;
    let runs = [];
    let nextBefore = null;
    let initialized = false;
    let selectedDay = null;
    let directId = null;
    let pageBusy = false;
    let selectedButton = null;
    let detailEpoch = 0;
    const details = new Map();
    const pendingDetails = new Map();
    const logPanels = new Map();
    const narrow = matchMedia("(max-width: 1000px)");
    let dateHost;
    let pageHost;
    let notices;
    let more;

    const action = (labelKey, onClick, variant = "secondary") => c.button.create({ labelKey, onClick, variant });
    const result = run => run.result_key ? t(run.result_key) : t("common.run.active");
    const points = value => t("common.points.total", { count: value });

    function errorBanner(error, retry) {
        return c.banner.create({ tone: "error", messageKey: error.messageKey || "errors.app.internal", params: error.params, actions: retry ? [action("common.action.retry", retry)] : [] });
    }

    function disclosure(className, label, children = []) {
        const summary = el("summary", {}, [el("span", { text: label }), c.icon.create({ name: "chevron-down" })]);
        const node = el("details", { class: className }, [summary, ...children]);
        node.addEventListener("toggle", () => {
            summary.setAttribute("aria-expanded", String(node.open));
            summary.lastElementChild.replaceWith(c.icon.create({ name: node.open ? "chevron-up" : "chevron-down" }));
        });
        return node;
    }

    function exceptionMarks(items) {
        const marks = [];
        if (items.some(needsAttention)) marks.push(el("span", { class: "history-attention" }, [c.icon.create({ name: "warning" }), el("span", { text: t("history.mark.attention") })]));
        if (items.some(run => run.trigger === "app")) marks.push(el("span", { class: "history-app-run" }, [svg("svg", { class: "icon", "aria-hidden": "true" }, [svg("use", { href: "img/history-icons.svg#hand" })]), el("span", { text: t("history.mark.app_run") })]));
        const waiting = items.find(run => run.deferred);
        if (waiting) {
            const label = t("history.mark.waited", { minutes: waiting.deferred.gave_up ? 40 : Math.ceil(waiting.deferred.waited_s / 60) });
            marks.push(el("span", { class: "history-wait-mark", role: "img", "aria-label": label, title: label }, c.icon.create({ name: "clock" })));
        }
        return marks;
    }

    function completeDay(day) {
        return !nextBefore || runs.some(run => localDay(run.started_at) < day);
    }

    function mergePage(data, append = true) {
        runs = [...new Map([...(append ? runs : []), ...data.runs].map(run => [run.run_id, run])).values()];
        nextBefore = data.next_before;
        initialized = true;
    }

    async function fetchPage(append, stamp) {
        const data = await bridge.call("get_history", { limit: 50, include_checks: true, ...(append && nextBefore ? { before: nextBefore } : {}) });
        if (!alive || stamp !== generation) return false;
        mergePage(data, append);
        return true;
    }

    function loadRun(id) {
        if (details.has(id)) return Promise.resolve(details.get(id));
        if (pendingDetails.has(id)) return pendingDetails.get(id);
        const epoch = detailEpoch;
        const pending = bridge.call("get_run", { run_id: id }).then(run => { if (alive && epoch === detailEpoch) details.set(id, run); return run; }).finally(() => { if (pendingDetails.get(id) === pending) pendingDetails.delete(id); });
        pendingDetails.set(id, pending);
        return pending;
    }

    function dayItems(day) {
        const items = (groupDays(runs).get(day) || []).map(run => details.get(run.run_id) || run);
        const linked = details.get(directId);
        if (linked && localDay(linked.started_at) === day && !items.some(run => run.run_id === directId)) items.push(linked);
        return items.sort((a, b) => new Date(b.started_at) - new Date(a.started_at));
    }

    function focusAfterRoute(node) {
        requestAnimationFrame(() => requestAnimationFrame(() => { if (alive && node.isConnected) { node.focus({ preventScroll: true }); if (directId) node.scrollIntoView({ block: "start", behavior: "instant" }); } }));
    }

    function stateNotice(account) {
        const code = (account.state_after || account.state_before)?.unreadable;
        if (!code) return null;
        if (["signed_out", "terms_of_use", "page_blank"].includes(code)) return c.banner.create({ tone: "warning", messageKey: `errors.state.${code}`, params: { account: account.label } });
        const names = code.split(",").map(name => t(`common.section.${name}`));
        return c.banner.create({ tone: "warning", messageKey: "errors.state.sections", params: { sections: i18n.formatList(names) } });
    }

    function renderDates() {
        dateHost.replaceChildren();
        const links = el("ul", { class: "history-date-items" });
        dateHost.append(links);
        const today = localDay(new Date()), previous = new Date();
        previous.setDate(previous.getDate() - 1);
        const yesterday = localDay(previous);
        for (const [day, list] of groupDays(runs)) {
            const relative = day === today ? "history.day.today" : day === yesterday ? "history.day.yesterday" : null;
            const button = el("a", { href: `#/history/${encodeURIComponent(`day:${day}`)}`, class: "history-date", "data-date": day, "aria-current": day === selectedDay ? "date" : null, onClick: () => { selectedButton = day; } }, [
                relative ? el("span", { class: "history-relative", text: t(relative) }) : null,
                el("time", { dateTime: day, text: formatDate(list[0].started_at) }),
                el("span", { class: "history-date-marks" }, exceptionMarks(dayItems(day))),
            ]);
            button.addEventListener("keydown", event => {
                const buttons = [...dateHost.querySelectorAll("a")], index = buttons.indexOf(button);
                const next = event.key === "ArrowDown" ? Math.min(index + 1, buttons.length - 1) : event.key === "ArrowUp" ? Math.max(index - 1, 0) : event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : null;
                if (next !== null) { event.preventDefault(); buttons[next].focus(); }
            });
            links.append(el("li", {}, [button]));
        }
        more.hidden = !nextBefore;
        root.classList.toggle("history-has-day", Boolean(selectedDay));
    }

    function scaffold() {
        for (const log of logPanels.values()) if (!root.contains(log)) log._dispose?.();
        c.dispose(root);
        logPanels.clear();
        root.replaceChildren();
        notices = el("div", { class: "history-notices", "aria-live": "polite" });
        dateHost = el("nav", { class: "history-date-list", "aria-label": t("history.title") });
        pageHost = el("section", { class: "history-day-page", "aria-labelledby": "history-day-heading" });
        more = action("history.more", loadMore);
        more.hidden = !nextBefore;
        root.append(el("header", { class: "screen-header" }, [el("h1", { text: t("history.title") }), action("history.refresh", () => navigate(params, true))]), notices,
            el("div", { class: "history-workspace" }, [el("div", { class: "history-date-rail" }, [dateHost, more]), pageHost]));
    }

    function technicalDetails(account) {
        const entries = account.tasks.filter(task => task.detail || task.progress);
        if (!account.error?.detail && !entries.length) return null;
        const summary = el("summary", { class: "log-toggle" }, [el("span", { text: t("history.technical.title") }), c.icon.create({ name: "chevron-down" })]);
        const disclosure = el("details", { class: "history-technical log-panel" }, [summary]);
        disclosure.addEventListener("toggle", () => summary.replaceChild(c.icon.create({ name: disclosure.open ? "chevron-up" : "chevron-down" }), summary.lastElementChild));
        if (account.error?.detail) disclosure.append(el("pre", { dir: "ltr", text: account.error.detail }));
        for (const task of entries) {
            disclosure.append(el("h3", { text: t("history.technical.task", { task: t(task.name_key) }) }), el("pre", { dir: "ltr", text: [task.detail, task.progress].filter(Boolean).join("\n") }));
        }
        return disclosure;
    }

    function taskEntry(task, inactive = false) {
        const row = el("div", { class: "history-task-row", "data-task": task.task, "data-status": task.status }, [
            svg("svg", { class: "icon history-task-icon", "aria-hidden": "true" }, [svg("use", { href: `img/history-icons.svg#${TASK_ICONS[task.task]}` })]),
            el("span", { class: "history-task-name", text: t(task.name_key) }),
            el("span", { class: "history-task-status sr-only", text: t(`common.task_status.${task.status}`) }),
        ]);
        if (["fail", "running"].includes(task.status) || (!Number.isFinite(task.points) && task.status === "ok")) {
            const mark = c.icon.create({ name: task.status === "fail" ? "close" : task.status === "ok" ? "check" : "live" });
            mark.classList.add("history-task-mark");
            row.append(mark);
        }
        if (inactive) row.append(el("span", { class: "history-task-idle-state", "aria-hidden": "true", text: t(`common.task_status.${task.status}`) }));
        if (Number.isFinite(task.points)) row.append(el("strong", { class: "history-task-points tabular", text: formatNumber(task.points) }));
        const entry = el("div", { class: `history-task-entry history-source-${task.task}` }, [row]);
        return entry;
    }

    function pointsBand(detail) {
        const shares = taskShares(detail.accounts.flatMap(account => account.tasks));
        if (!shares.length) return null;
        let offset = 0;
        const band = svg("svg", { class: "history-points-band", width: "100%", height: "36", "aria-hidden": "true" });
        const labels = [];
        for (const item of shares) {
            const width = item.share * 100;
            const start = document.documentElement.dir === "rtl" ? 100 - offset - width : offset;
            band.append(svg("rect", { x: `${start}%`, y: 0, width: `${width}%`, height: 16, class: `history-source-${item.task}`, "data-task": item.task, "data-points": item.points }));
            offset += width;
            const label = svg("text", { x: `${start + width / 2}%`, y: 32, "text-anchor": "middle", class: "history-band-value", "data-task": item.task, visibility: "hidden" }, formatNumber(item.points));
            band.append(label);
            labels.push({ label, share: item.share });
        }
        let active = true;
        const fit = () => {
            if (!active || !band.isConnected) return;
            const width = band.getBoundingClientRect().width;
            for (const { label, share } of labels) label.setAttribute("visibility", bandLabelFits(share, width, label.getComputedTextLength()) ? "visible" : "hidden");
        };
        const observer = new ResizeObserver(fit);
        observer.observe(band);
        const frame = requestAnimationFrame(fit);
        document.fonts.ready.then(fit);
        band._dispose = () => { active = false; observer.disconnect(); cancelAnimationFrame(frame); };
        return el("section", { class: "history-points" }, [el("h3", { text: t("history.sources.title") }), band]);
    }

    function runBlock(detail, older = false) {
        const block = el("article", { class: "history-run-block", "data-run-id": detail.run_id, "data-kind": detail.kind, tabIndex: -1 });
        const facts = el("header", { class: "history-run-summary" }, [el("time", { dateTime: detail.started_at, text: formatTime(detail.started_at) }),
            el("div", { class: "history-run-marks" }, exceptionMarks([detail])), el("span", { class: "sr-only", text: result(detail) }),
            detail.result !== "failed" && Number.isFinite(detail.points_earned) ? el("strong", { class: "history-run-points tabular", text: points(detail.points_earned) }) : null]);
        block.append(facts);
        const groups = taskGroups(detail.accounts);
        for (const task of groups.failed) {
            block.append(el("div", { class: "history-task-failure", "data-task": task.task, "data-status": task.status }, [
                svg("svg", { class: "icon history-task-icon", "aria-hidden": "true" }, [svg("use", { href: `img/history-icons.svg#${TASK_ICONS[task.task]}` })]),
                el("p", {}, [el("span", { class: "history-failure-name", text: t(task.name_key) }), " ", el("span", { class: "history-task-reason reason", text: t(task.reason_key || "errors.task.error") })]),
            ]));
        }
        if (detail.result === "failed") {
            const account = detail.accounts.find(item => item.error);
            block.append(c.banner.create({ tone: "error", messageKey: account?.error?.message_key || detail.result_key || "errors.run.failed", params: { account: account?.label || "" } }));
        }
        const deferred = detail.deferred ?? null;
        if (deferred) {
            const wait = facts.querySelector(".history-wait-mark");
            if (wait) { wait.removeAttribute("role"); wait.append(el("span", { text: t("history.mark.waited", { minutes: deferred.gave_up ? 40 : Math.ceil(deferred.waited_s / 60) }) })); }
            block.append(el("p", { class: "muted history-deferred", text: deferred.gave_up ? t("history.deferred.gave_up") : t("history.deferred.waited", { count: Math.ceil(deferred.waited_s / 60) }) }));
        }
        for (const warning of detail.warnings) {
            const account = detail.accounts.find(item => item.account_id === warning.account_id);
            block.append(c.banner.create({ tone: "warning", messageKey: warning.message_key, params: { account: account?.label || "" } }));
        }
        const checkOnly = detail.kind === "check" && detail.accounts.every(account => account.tasks.every(task => ["not_run", "off"].includes(task.status)));
        if (checkOnly && !detail.accounts.length) block.append(el("p", { class: "muted history-decision", text: result(detail) }));
        if (!checkOnly && detail.result !== "failed") {
            const band = pointsBand(detail);
            const tiles = el("div", { class: "history-run-tasks" });
            for (const account of detail.accounts) {
                const tasks = account.tasks.filter(task => groups.tiles.includes(task));
                if (tasks.length) tiles.append(el("section", { class: "history-account" }, [detail.accounts.length > 1 ? el("h2", {}, [el("bdi", { text: account.label })]) : null, el("div", { class: "history-task-list" }, tasks.map(task => taskEntry(task)))]));
            }
            if (older && (band || groups.tiles.length)) {
                const source = disclosure("history-older-tasks history-idle", t("history.sources.title"), [tiles]);
                if (band) source.querySelector("summary").insertBefore(band.querySelector(".history-points-band"), source.querySelector("summary").lastElementChild);
                block.append(source);
            } else {
                if (band) block.append(band);
                if (groups.tiles.length) block.append(tiles);
            }
        }
        if (!checkOnly && groups.noPoints.length) {
            const rows = el("div", { class: "history-idle-body" });
            for (const account of detail.accounts) {
                const tasks = account.tasks.filter(task => groups.noPoints.includes(task));
                if (tasks.length) rows.append(el("section", { class: "history-account" }, [detail.accounts.length > 1 ? el("h2", {}, [el("bdi", { text: account.label })]) : null, ...tasks.map(task => taskEntry(task, true))]));
            }
            block.append(disclosure("history-idle history-no-points", t("history.tasks.no_points", { count: groups.noPoints.length }), [rows]));
        }
        for (const account of detail.accounts) {
            const body = el("div", {}, [detail.accounts.length > 1 ? el("h2", {}, [el("bdi", { text: account.label })]) : null]);
            if (checkOnly && account.decision) body.append(el("p", { class: "muted history-decision", text: t(account.decision.reason_key, { points: account.decision.open_points, min_points: formatNumber(account.decision.min_points ?? 0) }) }));
            if (account.error && detail.result !== "failed") body.append(c.banner.create({ tone: "error", messageKey: account.error.message_key, params: { account: account.label } }));
            if (!checkOnly) {
                const unreadable = stateNotice(account);
                if (unreadable) body.append(unreadable);
            }
            const technical = technicalDetails(account);
            if (technical) body.append(technical);
            if (body.children.length) block.append(el("section", { class: "history-account" }, [body]));
        }
        if (detail.log_size > 0) {
            const log = logPanels.get(detail.run_id) || c.logPanel.create({ runId: detail.run_id, open: false });
            logPanels.set(detail.run_id, log);
            block.append(log);
        }
        return block;
    }

    function checkGroup(items, open = false) {
        const body = el("div", { class: "history-check-body" });
        const group = disclosure("history-check-group", t("history.checks.day_count", { count: items.length }), [body]);
        group.dataset.checkCount = items.length;
        const marks = el("span", { class: "history-check-marks" }, exceptionMarks(items).filter(mark => mark.classList.contains("history-attention")));
        group.querySelector("summary").insertBefore(marks, group.querySelector("summary").lastElementChild);
        let rendered = false, loading = false;
        const populate = async () => {
            if (rendered || loading) return;
            loading = true;
            const stamp = generation;
            try {
                const checks = await Promise.all(items.map(run => loadRun(run.run_id)));
                if (!alive || stamp !== generation || !group.isConnected) return;
                body.replaceChildren(...checks.map(run => runBlock(run)));
                rendered = true;
                const linked = [...body.querySelectorAll(".history-run-block")].find(node => node.dataset.runId === directId);
                if (linked) focusAfterRoute(linked);
                const attention = marks.querySelector(".history-attention");
                if (!attention && checks.some(needsAttention)) marks.append(...exceptionMarks(checks).filter(mark => mark.classList.contains("history-attention")));
            } catch (error) { if (alive && stamp === generation) body.replaceChildren(errorBanner(error, populate)); }
            finally { loading = false; }
        };
        group.addEventListener("toggle", () => { if (group.open) populate(); });
        if (open) { group.open = true; populate(); }
        return group;
    }

    function renderDay(focus = false) {
        for (const log of logPanels.values()) log.remove();
        c.dispose(pageHost);
        pageHost.replaceChildren();
        const items = dayItems(selectedDay);
        if (!items.length) return;
        const back = action("history.back", () => router.navigate("#/history"), "ghost");
        back.classList.add("history-back");
        const heading = el("h2", { id: "history-day-heading", class: "history-day-title", tabIndex: -1, text: formatDate(items[0].started_at) });
        const now = new Date(), yesterday = new Date(); yesterday.setDate(now.getDate() - 1);
        const relative = selectedDay === localDay(now) ? "history.day.today" : selectedDay === localDay(yesterday) ? "history.day.yesterday" : null;
        pageHost.dataset.date = selectedDay;
        pageHost.append(back, el("header", { class: "history-day-header" }, [heading, relative ? el("span", { class: "history-relative", text: t(relative) }) : null]));
        const dayRuns = items.filter(item => item.kind !== "check");
        const expandedRun = dayRuns.find(run => Number.isFinite(run.points_earned) && run.points_earned > 0) || dayRuns[0];
        dayRuns.forEach(run => pageHost.append(runBlock(run, run !== expandedRun)));
        const checks = items.filter(item => item.kind === "check");
        if (checks.length) pageHost.append(checkGroup(checks, checks.some(run => run.run_id === directId)));
        root.classList.add("history-has-day");
        if (items.some(run => run.active)) root.classList.add("history-has-active");
        else root.classList.remove("history-has-active");
        if (focus) {
            const linked = directId ? [...pageHost.querySelectorAll(".history-run-block")].find(node => node.dataset.runId === directId) : null;
            focusAfterRoute(linked || heading);
        }
    }

    async function settleDay(day, stamp, focus) {
        selectedDay = day;
        renderDates();
        const cursors = new Set();
        while (!completeDay(day)) {
            const before = nextBefore;
            if (cursors.has(before)) throw Object.assign(new Error("History cursor repeated"), { messageKey: "errors.app.internal" });
            cursors.add(before);
            if (!await fetchPage(true, stamp)) return;
            renderDates();
            if (nextBefore === before) throw Object.assign(new Error("History cursor did not advance"), { messageKey: "errors.app.internal" });
        }
        await Promise.all(dayItems(day).filter(run => run.kind !== "check").map(run => loadRun(run.run_id)));
        if (!alive || stamp !== generation) return;
        renderDates();
        renderDay(focus);
    }

    async function loadMore() {
        if (pageBusy || !nextBefore) return;
        const stamp = generation;
        pageBusy = true;
        more.disabled = true;
        try { if (await fetchPage(true, stamp)) renderDates(); }
        catch (error) { if (alive && stamp === generation) notices.replaceChildren(errorBanner(error, loadMore)); }
        finally { if (alive && stamp === generation) { pageBusy = false; more.disabled = false; } }
    }

    async function navigate(next, refresh = false) {
        params = next;
        const stamp = ++generation;
        const oldDay = selectedDay;
        pageBusy = false;
        selectedDay = null;
        directId = null;
        if (refresh) { detailEpoch += 1; details.clear(); pendingDetails.clear(); initialized = false; }
        scaffold();
        root.classList.remove("history-has-day");
        root.setAttribute("aria-busy", "true");
        pageHost.append(c.spinner.create({ labelKey: "history.loading" }));
        try {
            const token = params.runId;
            const dayRoute = /^day:\d{4}-\d{2}-\d{2}$/.test(token || "");
            let target = dayRoute ? token.slice(4) : null;
            if (token && !dayRoute) {
                directId = token;
                const run = await loadRun(token);
                if (!alive || stamp !== generation) return;
                target = localDay(run.started_at);
            }
            if (!initialized && !await fetchPage(false, stamp)) return;
            if (!alive || stamp !== generation) return;
            if (!runs.length && !directId) {
                pageHost.replaceChildren();
                notices.append(c.emptyState.create({ icon: "history", titleKey: "history.empty.title", bodyKey: "history.empty.body", action: { labelKey: "history.empty.overview", onClick: () => router.navigate("#/overview") } }));
                return;
            }
            renderDates();
            const groups = groupDays(runs), today = localDay(new Date());
            target ||= narrow.matches ? null : (refresh && oldDay && groups.has(oldDay) ? oldDay : groups.has(today) ? today : groups.keys().next().value);
            if (target) await settleDay(target, stamp, Boolean(token));
            else {
                pageHost.replaceChildren();
                const day = selectedButton || oldDay;
                const button = [...dateHost.querySelectorAll("a")].find(node => node.dataset.date === day);
                if (button) focusAfterRoute(button);
            }
        } catch (error) {
            if (alive && stamp === generation) { c.dispose(pageHost); pageHost.replaceChildren(); notices.replaceChildren(errorBanner(error, () => navigate(params, refresh))); }
        } finally { if (alive && stamp === generation) root.setAttribute("aria-busy", "false"); }
    }
    const offLive = ctx.store.on("live", state => {
        const run = state?.run;
        if (alive && run && selectedDay === localDay(run.started_at) && details.has(run.run_id)) { details.set(run.run_id, run); renderDay(); renderDates(); }
    });
    const offFinish = bridge.on("run.finished", () => { if (alive) navigate(params, true); });
    const resize = () => { if (alive) navigate(params); };
    narrow.addEventListener("change", resize);
    navigate(params);
    return { update: navigate, unmount() { alive = false; generation += 1; offLive(); offFinish(); narrow.removeEventListener("change", resize); for (const log of logPanels.values()) if (!root.contains(log)) log._dispose?.(); c.dispose(root); } };
}
