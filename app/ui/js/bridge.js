const listeners = new Map();
let readyResolve;
const ready = new Promise((resolve) => { readyResolve = resolve; });
window.addEventListener("pywebviewready", () => readyResolve(), { once: true });

export class BridgeError extends Error {
    constructor(error = {}) {
        super(error.message_key || "errors.app.internal");
        this.name = "BridgeError";
        this.code = error.code || "internal";
        this.messageKey = error.message_key || "errors.app.internal";
        this.params = error.params || {};
        this.detail = error.detail ?? null;
        this.fields = error.fields ?? null;
    }
}

export async function call(method, request = {}) {
    if (!window.pywebview?.api) await ready;
    const api = window.pywebview.api;
    if (typeof api[method] !== "function") throw new BridgeError();
    let response;
    try { response = await api[method](request); }
    catch (error) {
        if (error instanceof BridgeError) throw error;
        throw new BridgeError({ code: "internal", message_key: "errors.app.internal", detail: String(error) });
    }
    if (!response || typeof response.ok !== "boolean") throw new BridgeError();
    if (!response.ok) throw new BridgeError(response.error);
    if (!Object.hasOwn(response, "data")) throw new BridgeError();
    return response.data;
}

export function on(topic, handler) {
    if (!listeners.has(topic)) listeners.set(topic, new Set());
    listeners.get(topic).add(handler);
    return () => listeners.get(topic)?.delete(handler);
}

window.rf = {
    receive(batch) {
        if (!Array.isArray(batch)) return;
        for (const item of batch) {
            if (!item || typeof item.topic !== "string") continue;
            for (const handler of [...(listeners.get(item.topic) || [])]) {
                try { handler(item.data); } catch (error) { console.error("Push handler failed", item.topic, error); }
            }
        }
    },
};
