import { el } from "../../js/dom.js";
import { mount as mountDemo } from "./demo.js";

export function mount(view, ctx) {
    let demo;
    const render = () => {
        demo?.unmount();
        ctx.components.dispose(view);
        view.replaceChildren();
        const { screen } = ctx.router.parse(window.location.hash);
        const query = new URLSearchParams(window.location.search);
        if (query.get("mock") === "shell/components") { demo = mountDemo(view, ctx); return; }
        const t = ctx.i18n.t;
        const root = el("section", { class: "screen" }, [el("header", { class: "screen-header" }, el("div", {}, [el("h1", { text: t(`shell.route.${screen}.title`) }), el("p", { text: t("shell.mock.today") })]))]);
        if (screen === "overview") {
            const overview = ctx.store.get("overview");
            const account = overview.accounts[0];
            const tasks = account.tasks_today.map((task) => ({ ...task, reason_key: task.task === "visual_search" ? "shell.mock.no_streak" : task.reason_key, progress: { misc_cards: t("shell.mock.one_open"), searches: t("shell.mock.searches"), quests: t("shell.mock.quests") }[task.task] }));
            const plan = ctx.components.card.create({ titleKey: "shell.mock.tasks", children: [el("div", { class: "open-work" }, [el("p", { text: t("shell.mock.open_label") }), el("strong", { text: t("shell.mock.open_value") })]), ctx.components.taskList.create({ tasks })] });
            plan.classList.add("overview-tasks");
            const summary = ctx.components.card.create({ children: [ctx.components.stat.create({ labelKey: "shell.mock.balance", value: ctx.i18n.formatNumber(account.state.balance) }), el("p", { class: "balance-unit", text: t("shell.mock.points") }), el("p", { class: "earned-line" }, [el("span", { text: t("shell.mock.earned") }), el("strong", { text: `+${ctx.i18n.formatNumber(account.state.earned_today)}` })]), el("section", { class: "summary-divider" }, [el("p", { class: "subtle", text: t("shell.mock.next_label") }), el("p", { class: "next-time", text: t("shell.mock.next_time") }), el("p", { class: "next-description" }, [el("span", { text: t("shell.mock.automatic") }), el("br"), el("span", { class: "subtle", text: t("shell.mock.independent") })]), ctx.components.button.create({ labelKey: "shell.mock.run_now", variant: "primary", onClick: () => ctx.components.toast.show({ tone: "info", messageKey: "shell.mock.label" }) })]), el("p", { class: "summary-divider muted", text: t("shell.mock.goal") })] });
            summary.classList.add("overview-summary");
            root.append(el("div", { class: "overview-spread" }, [plan, summary]));
            const chart = ctx.components.card.create({ titleKey: "shell.mock.chart", children: [ctx.components.pointsChart.create({ series: account.chart, mode: "balance" })] });
            chart.classList.add("overview-chart");
            root.append(chart);
        } else {
            root.append(ctx.components.emptyState.create({ icon: screen === "setup" ? "person" : screen, titleKey: `shell.route.${screen}.title`, bodyKey: "shell.mock.placeholder", action: { labelKey: "common.action.continue", variant: "primary", onClick: () => ctx.router.navigate(screen === "setup" ? "#/setup/2" : "#/overview") } }));
            if (screen === "setup") root.append(ctx.components.riskNotice.create());
        }
        root.append(el("p", { class: "synthetic-note", text: t("shell.mock.label") }));
        view.append(root);
    };
    render();
    return { update: render, unmount() { demo?.unmount(); ctx.components.dispose(view); } };
}
