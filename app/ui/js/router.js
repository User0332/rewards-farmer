import { el } from "./dom.js";
let context;
let active;
let instance;
let generation = 0;
const screens = new Set(["setup", "overview", "stats", "live", "history", "accounts", "settings"]);
const sections = new Set(["schedule", "run", "notifications", "app", "advanced"]);

export function parse(hash) {
    const match = /^#\/([a-z]+)(?:\/([^/]+))?$/.exec(hash);
    if (!match || !screens.has(match[1])) return null;
    const [, screen, raw] = match;
    let part;
    try { part = raw === undefined ? undefined : decodeURIComponent(raw); } catch { return null; }
    if (screen === "setup" && !["1", "2", "3"].includes(part)) return null;
    if (screen === "settings" && part && !sections.has(part)) return null;
    if (["overview", "stats", "live", "accounts"].includes(screen) && part) return null;
    return { screen, hash, params: screen === "setup" ? { step: Number(part) } : screen === "history" ? { runId: part } : screen === "settings" ? { section: part } : {} };
}
export function current() { return active; }
export function navigate(hash) {
    if (!parse(hash)) return false;
    if (window.location.hash === hash) render();
    else window.location.hash = hash;
    return true;
}
export async function start(ctx) {
    context = ctx;
    window.addEventListener("hashchange", render);
    const info = ctx.store.get("appInfo");
    const mock = new URLSearchParams(window.location.search).has("mock");
    const route = !info.setup_done || !info.has_accounts ? "#/setup/1" : mock ? parse(window.location.hash)?.hash || "#/overview" : "#/overview";
    if (window.location.hash !== route) window.history.replaceState(null, "", route);
    await render();
}
async function render() {
    const token = ++generation;
    let route = parse(window.location.hash);
    if (!route) { navigate("#/overview"); return; }
    const info = context.store.get("appInfo");
    if ((!info.setup_done || !info.has_accounts) && route.screen !== "setup") { navigate("#/setup/1"); return; }
    const view = document.getElementById("view");
    view.setAttribute("aria-busy", "true");
    document.getElementById("app").classList.toggle("is-setup", route.screen === "setup");
    for (const link of document.querySelectorAll("#app-nav a[data-screen]")) {
        if (link.dataset.screen === route.screen) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
    }
    const mockShell = new URLSearchParams(window.location.search).get("mock")?.startsWith("shell/");
    try {
        if (active?.screen === route.screen && instance?.update) await instance.update(route.params);
        else {
            const module = await import(mockShell ? "../mock/shell/placeholder.js" : `./screens/${route.screen}.js`);
            if (token !== generation) return;
            instance?.unmount?.();
            context.components.dispose(view);
            view.replaceChildren();
            instance = await module.mount(view, { ...context, params: route.params });
        }
        if (token !== generation) return;
        active = route;
        document.title = context.i18n.t(mockShell ? `shell.route.${route.screen}.title` : `${route.screen}.title`);
    } catch (error) {
        if (token !== generation) return;
        console.error("Screen load failed", route.screen, error);
        instance?.unmount?.();
        instance = null;
        active = route;
        context.components.dispose(view);
        view.replaceChildren(el("section", { class: "screen" }, [el("h1", { text: context.i18n.t(`shell.route.${route.screen}.title`) }), context.components.emptyState.create({ icon: "error", titleKey: "shell.error.title", bodyKey: "shell.error.body", action: { labelKey: "common.action.retry", onClick: render } })]));
        document.title = context.i18n.t(`shell.route.${route.screen}.title`);
    } finally {
        if (token === generation) { view.setAttribute("aria-busy", "false"); view.focus({ preventScroll: true }); view.scrollTop = 0; view.dataset.route = route.hash; }
    }
}
