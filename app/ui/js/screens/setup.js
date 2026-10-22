import { el } from "../dom.js";
import { activeStates, statusKeys, signinFlow } from "./accounts/signin.js";
import { scheduleTime } from "../schedule-time.js";

const dayIds = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

export async function mount(view, ctx) {
    const { bridge, i18n, store, router, components: c } = ctx;
    const t = i18n.t;
    let alive = true;
    let step = ctx.params.step;
    let accounts = store.get("accounts") || [];
    let settings = null;
    let legacyInstall = null;
    let schedule = null;
    let draft = null;
    let savedWizard = null;
    let legacyChoice = null;
    let busy = false;
    let error = null;
    let warnings = [];
    let autostart = true;
    let firstRun = true;
    let completed = false;
    let imported = false;
    let daysOpen = false;
    let restoring = false;
    let recoveryPending = null;
    let recoveryError = null;
    let recoveryNotice = null;
    const root = el("section", { class: "screen setup-screen", "data-screen": "setup" });
    view.append(root);
    const flow = signinFlow(ctx, { changed: render, connected: refreshAccounts });
    const button = (id, props) => {
        const node = c.button.create(props);
        node.id = id;
        return node;
    };
    const note = (key, params) => el("p", { class: "setup-copy", text: t(key, params) });
    function accountIdentity(account) {
        const row = c.accountRow.create({ account });
        if (!Number.isFinite(account.balance)) row.querySelector(".account-row-balance")?.remove();
        return row;
    }
    const errorBanner = (err) => c.banner.create({ tone: "error", messageKey: err.messageKey, params: err.params });
    async function recover(kind) {
        if (busy || recoveryPending) return;
        recoveryPending = kind;
        recoveryError = null;
        recoveryNotice = null;
        render();
        try {
            if (kind === "export") {
                const result = await bridge.call("export_diagnostics");
                if (alive && result.path) recoveryNotice = { messageKey: "setup.recovery.exported", params: { path: result.path } };
            } else {
                let changes;
                if (kind === "edge") {
                    const result = await bridge.call("pick_path", { kind: "edge" });
                    if (!alive || !result.path) return;
                    changes = { "advanced.edge_path": result.path };
                } else changes = { "advanced.driver_path": "" };
                const saved = await bridge.call("save_settings", { changes });
                if (alive) {
                    store.set("settings", saved);
                    recoveryNotice = { messageKey: kind === "edge" ? "setup.recovery.edge_saved" : "setup.recovery.driver_reset" };
                }
            }
        } catch (err) { if (alive) recoveryError = err; }
        finally { recoveryPending = null; render(); }
    }
    function recoveryPanel() {
        const status = flow.status;
        const visibleErrors = [error?.messageKey, flow.error?.messageKey, status?.state === "failed" ? status.message_key : null].filter(Boolean);
        if (!visibleErrors.length && status?.state !== "failed") return null;
        const browserError = visibleErrors.some((key) => key.startsWith("errors.browser.")) || (status?.state === "failed" && ["edge_missing", "driver_missing", "driver_mismatch"].includes(status.error_code));
        const driverError = visibleErrors.some((key) => key === "errors.browser.driver_missing" || key === "errors.browser.driver_mismatch") || (status?.state === "failed" && ["driver_missing", "driver_mismatch"].includes(status.error_code));
        const children = [el("h2", { text: t("setup.recovery.title") }), note("setup.recovery.body")];
        if (browserError) children.push(note("setup.recovery.edge_help"));
        if (driverError) children.push(note("setup.recovery.driver_help"));
        const actions = [button("setup-export-diagnostics", { labelKey: "setup.recovery.export", busy: recoveryPending === "export", disabled: busy || Boolean(recoveryPending), onClick: () => recover("export") })];
        if (browserError) actions.push(button("setup-choose-edge", { labelKey: "setup.recovery.choose_edge", busy: recoveryPending === "edge", disabled: busy || Boolean(recoveryPending), onClick: () => recover("edge") }));
        if (driverError) actions.push(button("setup-reset-driver", { labelKey: "setup.recovery.reset_driver", disabled: busy || Boolean(recoveryPending), onClick: () => recover("driver") }));
        children.push(el("div", { class: "control-actions" }, actions));
        if (recoveryError) {
            const fields = Object.entries(recoveryError.fields || {});
            const fieldReasonsOnly = recoveryError.code === "settings.invalid" && fields.length > 0 && fields.every(([field]) => ["advanced.edge_path", "advanced.driver_path"].includes(field));
            if (!fieldReasonsOnly) children.push(errorBanner(recoveryError));
            for (const [field, code] of fields) {
                if (!["advanced.edge_path", "advanced.driver_path"].includes(field)) continue;
                const messageKey = `errors.settings.${code}`;
                const id = field === "advanced.edge_path" ? "setup-edge-path-error" : "setup-driver-path-error";
                children.push(el("p", { id, class: "setup-field-error", role: "alert", "data-recovery-field": field, text: t(i18n.has(messageKey) ? messageKey : recoveryError.messageKey, { ...recoveryError.params, name: field === "advanced.edge_path" ? "msedge.exe" : "msedgedriver.exe" }) }));
                const control = actions.find((node) => node.id === (field === "advanced.edge_path" ? "setup-choose-edge" : "setup-reset-driver"));
                control?.setAttribute("aria-invalid", "true");
                control?.setAttribute("aria-describedby", id);
            }
        }
        if (recoveryNotice) children.push(c.banner.create({ tone: "success", ...recoveryNotice }));
        return el("section", { class: "setup-recovery", "aria-label": t("setup.recovery.title"), "aria-live": "polite" }, children);
    }
    function syncAccounts(next) {
        accounts = next;
        store.set("accounts", accounts);
        store.set("appInfo", { ...store.get("appInfo"), has_accounts: accounts.length > 0 });
    }
    async function refreshAccounts() {
        try { const data = await bridge.call("list_accounts"); if (alive) { syncAccounts(data.accounts); render(); } }
        catch (err) { if (alive) { error = err; render(); } }
    }
    async function load() {
        error = null;
        try {
            const [list, saved, legacy] = await Promise.all([bridge.call("list_accounts"), bridge.call("get_settings"), bridge.call("get_legacy_install")]);
            if (!alive) return;
            syncAccounts(list.accounts);
            settings = saved;
            store.set("settings", saved);
            schedule = saved.schedule;
            draft ||= {
                "schedule.mode": saved.values["schedule.mode"],
                "schedule.window_start": saved.values["schedule.window_start"],
                "schedule.window_end": saved.values["schedule.window_end"],
                "schedule.time": saved.values["schedule.time"],
                "schedule.days": [...saved.values["schedule.days"]],
            };
            legacyInstall = legacy;
            if (step > 1 && !accounts.length) { step = 1; router.navigate("#/setup/1"); }
        } catch (err) { if (alive) error = err; }
        render();
    }
    async function startSignin(confirm = false) {
        error = null;
        try { await flow.start("add_account", confirm ? { confirm_migration: true } : {}); }
        catch (err) {
            if (!alive || err.code !== "accounts.migration_needed") return;
            flow.clearError();
            const choice = await c.dialog.open({
                titleKey: "accounts.migration.title", bodyKey: err.messageKey, params: err.params,
                actions: [{ id: "cancel", labelKey: "common.action.cancel", variant: "ghost" }, { id: "confirm", labelKey: "accounts.migration.confirm", variant: "primary" }],
            });
            if (alive && choice === "confirm") await startSignin(true);
        }
    }
    async function signinAgain() {
        try { await flow.start("sign_in_again", { account_id: flow.status.account_id }); } catch { /* The status panel keeps the bridge error. */ }
    }
    async function perform(action) {
        if (busy) return;
        busy = true;
        error = null;
        warnings = [];
        render();
        try { await action(); }
        catch (err) { if (alive) error = err; }
        finally { busy = false; render(); }
    }
    function statusPanel() {
        const status = flow.status;
        if (!status) return flow.error ? errorBanner(flow.error) : null;
        const active = activeStates.has(status.state);
        const children = [];
        if (active) children.push(c.spinner.create({ labelKey: statusKeys[status.state] }));
        children.push(c.banner.create({
            tone: status.error_code === "terms_of_use" ? "warning" : status.state === "signed_in" ? "success" : ["failed", "busy", "timed_out"].includes(status.state) ? "warning" : "info",
            messageKey: flow.cancelling ? "accounts.signin.cancelling" : status.message_key || statusKeys[status.state],
            params: { account: accounts.find((a) => a.id === status.account_id)?.label || t("accounts.default_label", { number: 1 }) },
        }));
        if (flow.error) children.push(errorBanner(flow.error));
        if (active) children.push(button("setup-cancel-signin", { labelKey: "common.action.cancel", onClick: flow.cancel, disabled: flow.cancelling }));
        else if (status.error_code === "terms_of_use" || ["failed", "timed_out", "cancelled", "busy"].includes(status.state)) {
            children.push(button("setup-retry-signin", { labelKey: "accounts.signin.retry", onClick: () => accounts.some((a) => a.id === status.account_id) ? signinAgain() : startSignin(), disabled: flow.pending }));
        }
        return el("div", { class: "setup-status", "data-signin-state": status.state, "aria-live": "polite" }, children);
    }
    function actionFooter(primary, secondary) {
        return el("footer", { class: "setup-actions" }, [primary, secondary]);
    }
    function renderSignin() {
        const children = [note("setup.signin.body"), note("setup.signin.privacy")];
        const status = statusPanel();
        if (status) children.push(status);
        if (accounts.length) children.push(...accounts.map(accountIdentity));
        if (!accounts.length && !flow.status) children.push(button("setup-signin", { labelKey: "accounts.action.signin", variant: "primary", busy: flow.pending, onClick: () => startSignin() }));
        if (imported) children.push(c.banner.create({ tone: "success", messageKey: "setup.legacy.imported" }));
        const canContinue = accounts.length > 0 && !activeStates.has(flow.status?.state) && !flow.pending && flow.status?.error_code !== "terms_of_use";
        children.push(actionFooter(button("setup-continue", { labelKey: "common.action.continue", variant: "primary", size: "large", disabled: !canContinue || busy, onClick: () => router.navigate("#/setup/2") })));
        const panel = c.card.create({ titleKey: "setup.signin.title", children });
        const rows = [panel];
        if (legacyInstall?.found && !accounts.length) rows.push(el("section", { class: "setup-import" }, [
            el("h2", { text: t("setup.legacy.title") }), note("setup.legacy.body"),
            button("setup-import", { labelKey: "setup.legacy.import", disabled: busy || flow.pending || activeStates.has(flow.status?.state), onClick: () => perform(async () => {
                const data = await bridge.call("import_legacy_install");
                if (alive) { syncAccounts(data.accounts); imported = true; }
            }) }),
        ]));
        return rows;
    }
    function renderSchedule() {
        const mode = draft["schedule.mode"];
        const fields = [c.segmented.create({ id: "setup-mode", labelKey: "setup.schedule.mode", options: [
            { value: "automatic", labelKey: "setup.schedule.automatic" }, { value: "fixed", labelKey: "setup.schedule.fixed" }, { value: "manual", labelKey: "setup.schedule.manual" },
        ], value: mode, onChange: (value) => { draft["schedule.mode"] = value; error = null; render("setup-mode"); } })];
        if (mode === "automatic") fields.push(note("setup.schedule.automatic_body", { checks: i18n.formatNumber(settings.values["schedule.checks"]) }), c.timeRange.create({ id: "setup-window", labelKey: "setup.schedule.window", start: draft["schedule.window_start"], end: draft["schedule.window_end"], onChange: ({ start, end }) => { draft["schedule.window_start"] = start; draft["schedule.window_end"] = end; } }));
        if (mode === "fixed") fields.push(note("setup.schedule.fixed_body"), c.timeInput.create({ id: "setup-time", labelKey: "setup.schedule.time", value: draft["schedule.time"], onChange: (value) => { draft["schedule.time"] = value; } }));
        if (mode === "manual") fields.push(note("setup.schedule.manual_body"));
        if (mode !== "manual") {
            const toggle = el("summary", { class: "log-toggle" }, [el("span", { text: t("setup.schedule.days_toggle") }), c.icon.create({ name: daysOpen ? "chevron-up" : "chevron-down" })]);
            const days = el("details", { class: "setup-days", open: daysOpen }, [toggle, c.weekdayPicker.create({ id: "setup-days", labelKey: "setup.schedule.days", days: draft["schedule.days"], onChange: (value) => { draft["schedule.days"] = value; } })]);
            days.addEventListener("toggle", () => { daysOpen = days.open; toggle.replaceChild(c.icon.create({ name: daysOpen ? "chevron-up" : "chevron-down" }), toggle.lastElementChild); });
            fields.push(days, note("setup.schedule.independent"));
        }
        if (error?.fields) {
            for (const [field, code] of Object.entries(error.fields)) if (field.startsWith("schedule.")) {
                const messageKey = `errors.settings.${code}`;
                fields.push(el("p", { class: "setup-field-error", role: "alert", "data-field-error": field, text: t(messageKey, { ...error.params, checks: settings.values["schedule.checks"] }) }));
            }
        }
        if (schedule?.legacy_task && !legacyChoice) fields.push(c.banner.create({ tone: "warning", messageKey: "setup.schedule.legacy_notice", actions: [
            button("setup-replace-legacy", { labelKey: "setup.schedule.replace", disabled: busy, onClick: () => perform(async () => {
                const next = await bridge.call("replace_legacy_task");
                if (alive) { schedule = next; legacyChoice = "replace"; }
            }) }),
            button("setup-keep-legacy", { labelKey: "setup.schedule.keep", disabled: busy, onClick: () => { legacyChoice = "keep"; render("setup-continue"); } }),
        ] }));
        if (legacyChoice) fields.push(c.banner.create({ tone: "info", messageKey: legacyChoice === "keep" ? "setup.schedule.kept" : "setup.schedule.replaced" }));
        const needsChoice = Boolean(schedule?.legacy_task && !legacyChoice);
        if (needsChoice) fields.push(note("setup.schedule.legacy_choose"));
        const background = el("section", { class: "setup-background" }, [
            c.switchControl.create({ id: "setup-autostart", labelKey: "setup.done.autostart", descriptionKey: "setup.done.autostart_body", checked: autostart, disabled: busy, onChange: (checked) => { autostart = checked; } }),
        ]);
        const footer = actionFooter(button("setup-continue", { labelKey: "common.action.continue", variant: "primary", size: "large", disabled: needsChoice || busy, busy, onClick: () => perform(async () => {
            const changes = { "schedule.mode": mode, "app.autostart": autostart };
            if (mode !== "manual") changes["schedule.days"] = draft["schedule.days"];
            if (mode === "automatic") { changes["schedule.window_start"] = draft["schedule.window_start"]; changes["schedule.window_end"] = draft["schedule.window_end"]; }
            if (mode === "fixed") changes["schedule.time"] = draft["schedule.time"];
            const signature = JSON.stringify(changes);
            if (savedWizard !== signature) {
                const data = await bridge.call("save_settings", { changes });
                if (!alive) return;
                settings = data; schedule = data.schedule; warnings = data.warnings; savedWizard = signature;
                store.set("settings", data);
            }
            if (alive) router.navigate("#/setup/3");
        }) }), button("setup-back", { labelKey: "common.action.back", variant: "ghost", disabled: busy, onClick: () => router.navigate("#/setup/1") }));
        return [c.card.create({ titleKey: "setup.schedule.title", children: [el("div", { class: "setup-schedule-grid" }, [el("div", { class: "setup-schedule-main" }, fields), background]), footer] })];
    }
    async function startFirstRun() {
        await bridge.call("start_run");
        if (alive) router.navigate("#/live");
    }
    function renderDone() {
        if (completed) return [c.banner.create({ tone: "success", messageKey: "setup.done.finished" }), el("footer", { class: "setup-actions" }, [
            button("setup-open-overview", { labelKey: "setup.done.open_overview", onClick: () => router.navigate("#/overview"), disabled: busy }),
            button("setup-retry-run", { labelKey: "setup.done.retry_run", variant: "primary", busy, onClick: () => perform(startFirstRun) }),
        ])];
        const mode = draft["schedule.mode"];
        const failedSchedule = mode !== "manual" && (!schedule || schedule.error_code || !schedule.task_exists || !schedule.in_sync);
        const when = mode === "manual" ? t("overview.schedule.manual_next") : failedSchedule || !schedule.next_at ? null : scheduleTime(schedule, store.get("overview")?.generated_at || new Date().toISOString(), i18n, store.get("appInfo")?.format_locale);
        const scheduleText = mode === "manual" ? t("setup.schedule.manual_body") : mode === "fixed" ? t("setup.summary.fixed", { time: i18n.formatTime(draft["schedule.time"]) }) : t("setup.summary.automatic", { checks: settings.values["schedule.checks"], start: i18n.formatTime(draft["schedule.window_start"]), end: i18n.formatTime(draft["schedule.window_end"]) });
        const days = draft["schedule.days"];
        const daysText = dayIds.every((day) => days.includes(day)) ? t("setup.summary.every_day") : i18n.formatList(days.map((day) => new Intl.DateTimeFormat(store.get("appInfo")?.format_locale || "en-US", { weekday: "short" }).format(new Date(2026, 0, 5 + dayIds.indexOf(day)))));
        const summary = el("dl", { class: "setup-summary" }, [
            el("div", { class: "setup-summary-row", "data-summary": "account" }, [el("dt", { text: t("overview.account") }), el("dd", {}, accounts.map(accountIdentity))]),
            el("div", { class: "setup-summary-row", "data-summary": "schedule" }, [el("dt", { text: t("settings.section.schedule") }), el("dd", {}, [el("p", { text: scheduleText }), mode === "manual" ? null : el("p", { class: "setup-summary-detail", text: daysText })])]),
            when ? el("div", { class: "setup-summary-row", "data-summary": "next" }, [el("dt", { text: t(mode === "fixed" ? "overview.next_run" : "overview.next_check") }), el("dd", { class: "tabular", text: when })]) : null,
        ]);
        const children = [summary];
        if (failedSchedule) children.push(c.banner.create({ tone: "warning", messageKey: "errors.app.schedule_failed" }));
        children.push(el("section", { class: "setup-first-run-choice" }, c.switchControl.create({ id: "setup-first-run", labelKey: "setup.done.first_run", descriptionKey: "setup.done.first_run_body", checked: firstRun, disabled: busy, onChange: (checked) => { firstRun = checked; render("setup-first-run"); } })));
        const finish = button("setup-finish", { labelKey: firstRun ? "setup.done.finish_run" : "setup.done.finish", variant: "primary", size: "large", busy, onClick: () => perform(async () => {
            await bridge.call("complete_setup");
            if (!alive) return;
            completed = true;
            store.set("appInfo", { ...store.get("appInfo"), setup_done: true, has_accounts: accounts.length > 0 });
            if (firstRun) await startFirstRun();
            else router.navigate("#/overview");
        }) });
        children.push(actionFooter(finish, button("setup-back", { labelKey: "common.action.back", variant: "ghost", disabled: busy, onClick: () => router.navigate("#/setup/2") })));
        return [c.card.create({ titleKey: "setup.done.title", children })];
    }
    function render(focusId) {
        if (!alive) return;
        const focused = root.contains(document.activeElement) ? document.activeElement.id : null;
        c.dispose(root);
        root.replaceChildren(el("header", { class: "screen-header setup-heading" }, el("h1", { text: t("setup.title") })));
        const steps = el("ol", { class: "setup-steps", "aria-label": t("setup.steps.label") }, ["setup.steps.signin", "setup.steps.schedule", "setup.steps.done"].map((key, index) => el("li", { "aria-current": step === index + 1 ? "step" : null, class: step > index + 1 ? "is-complete" : "" }, [el("span", { class: "setup-step-number", text: i18n.formatNumber(index + 1) }), el("span", { text: t(key) })])));
        const sidebar = el("aside", { class: "setup-sidebar" }, [steps]);
        const content = el("div", { class: "setup-content" }, [c.riskNotice.create()]);
        if (error) content.append(errorBanner(error));
        for (const warning of warnings || []) content.append(c.banner.create({ tone: "warning", messageKey: warning.message_key, params: warning.params }));
        if (!settings) content.append(c.emptyState.create({ titleKey: "setup.load.title", action: { labelKey: "common.action.retry", onClick: load } }));
        else content.append(...(step === 1 ? renderSignin() : step === 2 ? renderSchedule() : renderDone()));
        root.append(el("div", { class: "setup-layout" }, [sidebar, content]));
        const recovery = recoveryPanel();
        if (recovery) content.append(recovery);
        if (error?.fields) for (const [field, code] of Object.entries(error.fields)) {
            const description = root.querySelector(`[data-field-error="${field}"]`);
            const control = field === "schedule.window_start" ? root.querySelector("#setup-window-start") : field === "schedule.window_end" ? root.querySelector("#setup-window-end") : field === "schedule.time" ? root.querySelector("#setup-time") : root.querySelector("#setup-days");
            if (description && control) {
                description.id = `setup-error-${code}`;
                control.setAttribute("aria-invalid", "true");
                control.setAttribute("aria-describedby", description.id);
                if (field === "schedule.days") { daysOpen = true; root.querySelector(".setup-days").open = true; }
            }
        }
        const nextFocus = document.getElementById(focusId || focused);
        if (nextFocus && !nextFocus.disabled) (nextFocus.matches("button, input") ? nextFocus : nextFocus.querySelector('[tabindex="0"]'))?.focus({ preventScroll: true });
        else if (focused) (root.querySelector("#setup-cancel-signin:not(:disabled), #setup-retry-signin:not(:disabled), #setup-continue:not(:disabled), #setup-open-overview") || view).focus({ preventScroll: true });
    }
    await load();
    flow.seed();
    const activeId = store.get("overview")?.signin_active?.account_id;
    if (activeId && !flow.status) { restoring = true; await flow.restore(activeId); restoring = false; }
    return {
        update(params) {
            step = params.step;
            if (step > 1 && (!accounts.length || restoring || activeStates.has(flow.status?.state) || flow.status?.error_code === "terms_of_use")) { step = 1; router.navigate("#/setup/1"); }
            render();
        },
        unmount() { alive = false; flow.dispose(); c.dispose(root); },
    };
}
