import { el } from "../../js/dom.js";

export function mount(view, ctx) {
    const c = ctx.components;
    const labelKey = "shell.demo.label";
    const options = ["a", "b", "c"].map((value) => ({ value, labelKey: `shell.demo.option_${value}` }));
    const account = ctx.store.get("accounts")[0];
    const overview = ctx.store.get("overview").accounts[0];
    const summary = { run_id: "synthetic-run", started_at: "2026-10-14T19:45:00Z", active: false, result_key: "errors.run.ok", points_earned: 150 };
    const samples = {
        button: () => el("div", { class: "control-actions" }, [c.button.create({ labelKey, variant: "primary", icon: "play" }), c.button.create({ labelKey, variant: "primary", size: "large" }), c.button.create({ labelKey, disabled: true }), c.button.create({ labelKey, busy: true })]),
        iconButton: () => c.iconButton.create({ labelKey, icon: "refresh" }),
        switchControl: () => c.switchControl.create({ id: "demo-switch", labelKey, descriptionKey: "shell.demo.description", checked: true, onChange: (value) => { view.dataset.switchValue = String(value); } }),
        select: () => c.select.create({ id: "demo-select", labelKey, options, value: "a", onChange: (value) => { view.dataset.selectValue = value; } }),
        textField: () => c.textField.create({ id: "demo-text", labelKey, descriptionKey: "shell.demo.description", value: "Synthetic label", maxLength: 40, autocomplete: "off", onInput: (value) => { view.dataset.textValue = value; }, onCommit: (value) => { view.dataset.textCommit = value; } }),
        segmented: () => c.segmented.create({ id: "demo-segmented", labelKey, options, value: "a", onChange: (value) => { view.dataset.segmentValue = value; } }),
        timeInput: () => c.timeInput.create({ id: "demo-time", labelKey, value: "09:45", onChange: (value) => { view.dataset.timeValue = value; } }),
        timeRange: () => c.timeRange.create({ id: "demo-range", labelKey, start: "07:00", end: "22:00", onChange: (value) => { view.dataset.rangeValue = JSON.stringify(value); } }),
        weekdayPicker: () => c.weekdayPicker.create({ id: "demo-days", labelKey, days: ["mon", "wed", "fri"], onChange: (value) => { view.dataset.daysValue = value.join(","); } }),
        card: () => c.card.create({ titleKey: "shell.demo.label", children: el("p", { text: ctx.i18n.t("shell.demo.description") }) }),
        stat: () => c.stat.create({ labelKey: "shell.mock.balance", value: ctx.i18n.formatNumber(12340), subKey: "shell.mock.points" }),
        taskList: () => c.taskList.create({ tasks: overview.tasks_today.map((task) => ({ ...task, reason_key: task.task === "visual_search" ? "shell.mock.no_streak" : task.reason_key })), compact: true }),
        pointsChart: () => c.pointsChart.create({ series: overview.chart.map((point, index) => ({ ...point, balance: [7, 8].includes(index) ? null : point.balance })), mode: "balance" }),
        goalCard: () => c.goalCard.create({ goal: overview.goal, onSet: (value) => { view.dataset.goalValue = String(value); return ctx.bridge.call("set_goal", { account_id: account.id, target: value }); }, onClear: () => { view.dataset.goalValue = "cleared"; return ctx.bridge.call("set_goal", { account_id: account.id, target: null }); } }),
        runRow: () => c.runRow.create({ summary, onOpen: (value) => { view.dataset.runValue = value; } }),
        logPanel: () => c.logPanel.create({ runId: "synthetic-run", open: true, onToggle: (value) => { view.dataset.logValue = String(value); } }),
        banner: () => c.banner.create({ tone: "info", messageKey: "shell.demo.description", actions: [{ labelKey: "common.action.dismiss", variant: "ghost" }] }),
        toast: () => c.button.create({ labelKey: "shell.demo.label", onClick: () => c.toast.show({ tone: "success", messageKey: "shell.demo.description", timeoutMs: 0 }) }),
        dialog: () => c.button.create({ labelKey: "shell.demo.label", onClick: () => c.dialog.open({ titleKey: "shell.demo.dialog.title", bodyKey: "shell.demo.dialog.body", actions: [{ id: "cancel", labelKey: "common.action.cancel", variant: "secondary" }, { id: "remove", labelKey: "common.action.remove", variant: "danger" }] }).then((value) => { view.dataset.dialogValue = value; }) }),
        emptyState: () => c.emptyState.create({ icon: "history", titleKey: "shell.demo.label", bodyKey: "shell.demo.description" }),
        progressRing: () => c.progressRing.create({ value: 0.65, labelKey: "shell.demo.progress" }),
        accountRow: () => c.accountRow.create({ account, actions: [{ labelKey: "common.action.edit", variant: "ghost" }] }),
        riskNotice: () => c.riskNotice.create(),
        spinner: () => c.spinner.create({ labelKey: "common.action.working" }),
        icon: () => el("div", { class: "control-actions" }, ["check", "warning", "info", "back", "forward"].map((name) => c.icon.create({ name }))),
    };
    const root = el("section", { class: "screen" }, [el("header", { class: "screen-header" }, el("h1", { text: ctx.i18n.t("shell.demo.title") })), el("div", { class: "component-demo" }, Object.entries(samples).map(([name, build]) => el("section", { "data-component": name }, [el("h2", { text: ctx.i18n.t("shell.demo.component", { name }) }), build()]))), el("p", { class: "synthetic-note", text: ctx.i18n.t("shell.mock.label") })]);
    view.append(root);
    return { unmount() { c.dispose(root); } };
}
