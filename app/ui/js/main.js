import * as bridge from "./bridge.js";
import * as i18n from "./i18n.js";
import * as store from "./store.js";
import * as router from "./router.js";
import * as components from "./components/index.js";
import { el } from "./dom.js";
import { joinLog } from "./components/log-stream.js";
import { accountAvatar } from "./components/kit.js";

const context = { bridge, i18n, store, router, components };
let liveRevision = 0;
let overviewRevision = 0;
let logQueue = Promise.resolve();
let settingsQueue = Promise.resolve();
let runningLanguage;
let reloadPage = () => window.location.reload();
function report(error) { components.toast.show({ tone: "error", messageKey: error.messageKey || "errors.app.internal", params: error.params }); }
async function refreshOverview({ reportError = true } = {}) {
    const token = ++overviewRevision;
    try { const data = await bridge.call("get_overview"); if (token === overviewRevision) store.set("overview", data); }
    catch (error) { if (reportError) report(error); else console.warn("Initial get_overview error rendered by screen", error.code); }
}
function setLive(data) { store.set("live", { run: null, log: null, last: null, ...store.get("live"), ...data }); }
function registerPushes() {
    bridge.on("run.started", (data) => { liveRevision++; setLive({ run: data, log: { text: "", next_offset: 0 }, last: null }); });
    bridge.on("run.snapshot", (data) => { liveRevision++; setLive({ run: data }); });
    bridge.on("run.log", (data) => {
        logQueue = logQueue.then(async () => {
            const live = store.get("live");
            if (live?.run?.run_id !== data.run_id) return;
            const original = live.log;
            const prior = original || { text: "", next_offset: 0 };
            const joined = await joinLog(prior, data, bridge.call);
            const current = store.get("live");
            if (current?.run?.run_id === data.run_id && current.log === original && joined !== prior) setLive({ log: joined });
        }).catch(report);
    });
    bridge.on("run.finished", async (data) => {
        const token = ++liveRevision;
        setLive({ last: data });
        try { const run = await bridge.call("get_run", { run_id: data.run_id }); if (token === liveRevision) setLive({ run }); } catch (error) { report(error); }
    });
    bridge.on("overview.changed", refreshOverview);
    bridge.on("signin.status", (data) => store.set("signin", data));
    bridge.on("theme.changed", ({ theme }) => { if (["dark", "light"].includes(theme)) { document.documentElement.dataset.theme = theme; store.set("appInfo", { ...store.get("appInfo"), theme }); } });
    bridge.on("navigate", ({ route }) => router.navigate(route));
    bridge.on("update.available", (data) => store.set("update", data));
    bridge.on("settings.changed", () => {
        settingsQueue = settingsQueue.then(async () => {
            const info = await bridge.call("app_info");
            if (info.lang !== runningLanguage.lang || info.dir !== runningLanguage.dir) reloadPage();
        }).catch(report);
    });
}
function renderFrame() {
    const nav = document.getElementById("app-nav");
    nav.setAttribute("aria-label", i18n.t("shell.nav.label"));
    nav.replaceChildren(el("div", { class: "nav-brand" }, [el("img", { src: "img/app-icon.svg", alt: "", width: 32, height: 32 }), el("span", { text: i18n.t("shell.window.title") })]), el("div", { class: "nav-links" }, ["overview", "stats", "live", "history", "accounts", "settings"].map((screen) => el("a", { class: "nav-link", href: `#/${screen}`, "data-screen": screen }, [components.icon.create({ name: screen === "stats" ? "chart" : screen }), el("span", { class: "nav-link-label", text: i18n.t(`shell.nav.${screen}`) }), screen === "live" ? el("span", { class: "nav-active", hidden: true }, [el("span", { class: "nav-active-dot", "aria-hidden": "true" }), el("span", { class: "sr-only", text: i18n.t("shell.nav.live_active") })]) : null]))), el("a", { class: "nav-account", href: "#/accounts" }));
    const accountFooter = (accounts = []) => {
        const footer = nav.querySelector(".nav-account");
        const first = accounts[0];
        footer.replaceChildren(accountAvatar(first), el("div", { class: "nav-account-copy" }, el("p", { class: "account-label", text: first?.label || i18n.t("shell.account.none") })));
    };
    const activeRun = (live) => { nav.querySelector(".nav-active").hidden = !live?.run?.active; };
    const update = (data) => {
        const slot = document.getElementById("app-notices");
        components.dispose(slot);
        slot.replaceChildren();
        if (!data?.available) return;
        slot.append(components.banner.create({ tone: "info", messageKey: "shell.update.available", params: { version: data.latest }, actions: [components.button.create({ labelKey: "shell.update.download", variant: "ghost", onClick: () => bridge.call("open_external", { url: data.url }).catch(report) }), components.iconButton.create({ labelKey: "common.action.dismiss", icon: "close", onClick: () => bridge.call("dismiss_update", { version: data.latest }).then(() => store.set("update", null)).catch(report) })] }));
    };
    accountFooter(store.get("accounts")); activeRun(store.get("live")); update(store.get("update"));
    store.on("accounts", accountFooter); store.on("live", activeRun); store.on("update", update);
}
async function boot() {
    const fixture = new URLSearchParams(window.location.search).get("mock");
    if (fixture) {
        const installed = await (await import("./mock-bridge.js")).install();
        if (installed && fixture === "shell/reload") reloadPage = (await import("../mock/shell/reload.js")).location.reload;
    }
    const appInfo = await bridge.call("app_info");
    await i18n.init({ lang: appInfo.lang, dir: appInfo.dir, formatLocale: appInfo.format_locale });
    document.documentElement.dataset.theme = appInfo.theme;
    document.documentElement.dataset.reducedMotion = String(appInfo.reduced_motion);
    store.set("appInfo", appInfo);
    runningLanguage = { lang: appInfo.lang, dir: appInfo.dir };
    store.set("live", { run: null, log: null, last: null });
    components.configure(context);
    registerPushes();
    const revision = liveRevision;
    const initialScreen = !appInfo.setup_done || !appInfo.has_accounts ? "setup" : fixture ? router.parse(window.location.hash)?.screen || "overview" : "overview";
    const initialError = (method, error) => {
        const owners = { get_live: ["live"], list_accounts: ["accounts", "setup"] };
        if (owners[method]?.includes(initialScreen)) console.warn(`Initial ${method} error rendered by screen`, error.code);
        else report(error);
    };
    await Promise.all([refreshOverview({ reportError: initialScreen !== "overview" }), bridge.call("get_live").then((data) => { if (revision === liveRevision) setLive(data); }).catch((error) => initialError("get_live", error)), bridge.call("list_accounts").then((data) => store.set("accounts", data.accounts)).catch((error) => initialError("list_accounts", error))]);
    renderFrame();
    await router.start(context);
    await bridge.call("ui_ready");
    document.getElementById("app").dataset.ready = "true";
}
boot().catch((error) => {
    console.error("App initialization failed", error);
    const view = document.getElementById("view");
    if (i18n.has("shell.error.title")) view.replaceChildren(components.emptyState.create({ titleKey: "shell.error.title", bodyKey: "shell.error.body", icon: "error", action: { labelKey: "common.action.retry", onClick: () => window.location.reload() } }));
    document.getElementById("app").dataset.ready = "error";
});
