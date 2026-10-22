const methodNames = ["ui_ready", "app_info", "open_external", "get_overview", "get_stats", "get_live", "get_history", "get_run", "get_run_log", "start_run", "stop_run", "set_goal", "get_settings", "save_settings", "get_schedule_status", "replace_legacy_task", "pick_path", "list_ollama_models", "export_diagnostics", "check_updates", "dismiss_update", "get_legacy_install", "import_legacy_install", "complete_setup", "list_accounts", "get_account_picture", "add_account", "sign_in_again", "get_signin_status", "cancel_signin", "rename_account", "set_account_paused", "remove_account"];
const failure = (method) => ({ ok: false, error: { code: "internal", message_key: "errors.app.internal", params: {}, detail: `Mock method ${method} has no matching response`, fields: null } });

function jsonEqual(first, second) {
    if (first === second) return true;
    if (first === null || second === null || typeof first !== "object" || typeof second !== "object") return false;
    if (Array.isArray(first) !== Array.isArray(second)) return false;
    if (Array.isArray(first)) return first.length === second.length && first.every((value, index) => jsonEqual(value, second[index]));
    const keys = Object.keys(first);
    return keys.length === Object.keys(second).length && keys.every((key) => Object.hasOwn(second, key) && jsonEqual(first[key], second[key]));
}

export function resolveEnvelope(method, answer, request = {}) {
    const choices = Array.isArray(answer) ? answer : [answer];
    const entry = choices.find((item) => {
        if (!item || typeof item !== "object") return false;
        if (!Object.hasOwn(item, "when")) return true;
        return item.when !== null && typeof item.when === "object" && !Array.isArray(item.when) && Object.entries(item.when).every(([key, value]) => Object.hasOwn(request, key) && jsonEqual(value, request[key]));
    });
    if (!entry) return failure(method);
    const response = structuredClone(entry);
    delete response.when;
    return response;
}
export async function install() {
    const query = new URLSearchParams(window.location.search);
    const fixture = query.get("mock");
    if (!fixture || window.pywebview) return false;
    if (!/^[a-z][a-z0-9_-]*\/[a-z][a-z0-9_-]*$/.test(fixture)) throw new Error("Invalid mock fixture path");
    const root = new URL("../mock/", import.meta.url);
    const load = async (path) => {
        const response = await fetch(new URL(path, root));
        if (!response.ok) throw new Error(`Mock fixture ${path}: HTTP ${response.status}`);
        return response.json();
    };
    const [defaults, chosen] = await Promise.all([load("shell/defaults.json"), load(`${fixture}.json`)]);
    const methods = { ...defaults.methods, ...chosen.methods };
    const timers = new Set();
    let pushesStarted = false;
    const api = Object.fromEntries(methodNames.map((method) => [method, async (request = {}) => {
        const response = resolveEnvelope(method, methods[method], request);
        if (method === "app_info" && response.ok) {
            const theme = query.get("theme");
            if (["dark", "light"].includes(theme)) response.data.theme = theme;
            if (query.has("lang")) {
                response.data.lang = query.get("lang");
                const direction = query.get("lang") === "x-rtl" ? "rtl" : response.data.locales?.find((item) => item.code === query.get("lang"))?.dir;
                if (direction) response.data.dir = direction;
                else delete response.data.dir;
            }
            if (query.has("format")) {
                const format = query.get("format");
                try {
                    if (Intl.getCanonicalLocales(format).length) response.data.format_locale = format;
                } catch (error) {
                    if (!(error instanceof RangeError)) throw error;
                }
            }
            if (query.get("reduced_motion") === "1") response.data.reduced_motion = true;
        }
        if (method === "ui_ready" && !pushesStarted && response.ok) {
            pushesStarted = true;
            for (const push of chosen.pushes || []) {
                const timer = setTimeout(() => {
                    timers.delete(timer);
                    window.rf?.receive([{ topic: push.topic, data: structuredClone(push.data) }]);
                }, Math.max(0, push.after_ms));
                timers.add(timer);
            }
        }
        if (method === "save_settings" && response.ok && request.changes) {
            const current = resolveEnvelope("get_settings", methods.get_settings, {});
            if (current.ok) methods.get_settings = { ok: true, data: { ...current.data, values: structuredClone(response.data.values), schedule: structuredClone(response.data.schedule), secrets_set: structuredClone(response.data.secrets_set || {}) } };
        }
        return response;
    }]));
    window.pywebview = { api };
    window.addEventListener("pagehide", () => { for (const timer of timers) clearTimeout(timer); }, { once: true });
    window.dispatchEvent(new Event("pywebviewready"));
    return true;
}
