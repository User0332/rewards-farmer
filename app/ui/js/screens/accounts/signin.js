export const activeStates = new Set(["starting", "waiting", "verifying"]);
export const statusKeys = {
    starting: "accounts.signin.starting", waiting: "accounts.signin.waiting",
    verifying: "accounts.signin.verifying", signed_in: "accounts.signin.signed_in",
    failed: "accounts.signin.failed", timed_out: "accounts.signin.timed_out",
    cancelled: "accounts.signin.cancelled", busy: "accounts.signin.busy",
};

// Both owned screens follow the same backend state, without polling or opening a URL.
export function signinFlow(ctx, { accepts = () => true, changed, connected }) {
    let alive = true;
    let target = null;
    let revision = 0;
    const flow = { status: null, error: null, pending: false, cancelling: false };
    const notify = () => { if (alive) changed(); };
    function receive(status) {
        if (!alive || !status || !Object.hasOwn(statusKeys, status.state) || !accepts(status.account_id)) return;
        if (target && target !== status.account_id) return;
        const previous = flow.status;
        revision++;
        flow.status = status;
        target = status.account_id;
        flow.error = null;
        if (!activeStates.has(status.state)) flow.cancelling = false;
        notify();
        if (status.state === "signed_in" && (previous?.state !== "signed_in" || previous?.account_id !== status.account_id)) connected?.(status);
    }
    const off = ctx.store.on("signin", receive);
    flow.restore = async (accountId) => {
        target = accountId;
        const token = revision;
        try {
            const status = await ctx.bridge.call("get_signin_status", { account_id: accountId });
            if (token === revision) receive(status);
        } catch (error) { if (alive && token === revision) { flow.error = error; notify(); } }
    };
    flow.start = async (method, request = {}) => {
        if (flow.pending || activeStates.has(flow.status?.state)) return;
        target = request.account_id || null;
        flow.pending = true;
        flow.cancelling = false;
        flow.error = null;
        const token = revision;
        notify();
        try {
            const result = await ctx.bridge.call(method, request);
            if (alive && token === revision) receive(result.signin);
            return result;
        } catch (error) { if (alive) { flow.error = error; notify(); } throw error; }
        finally { flow.pending = false; notify(); }
    };
    flow.cancel = async () => {
        if (!activeStates.has(flow.status?.state) || flow.cancelling) return;
        flow.cancelling = true;
        flow.error = null;
        notify();
        try { await ctx.bridge.call("cancel_signin", { account_id: flow.status.account_id }); }
        catch (error) { if (alive) { flow.cancelling = false; flow.error = error; notify(); } }
    };
    flow.clearError = () => { flow.error = null; notify(); };
    flow.seed = () => receive(ctx.store.get("signin"));
    flow.dispose = () => { alive = false; off(); };
    return flow;
}
