import { el } from "../dom.js";
import { activeStates, statusKeys, signinFlow } from "./accounts/signin.js";

export async function mount(view, ctx) {
    const { bridge, i18n, store, router, components: c } = ctx;
    const t = i18n.t;
    let alive = true;
    let accounts = store.get("accounts") || [];
    let loaded = false;
    let error = null;
    let warnings = [];
    let busyId = null;
    let confirming = false;
    let editId = null;
    let label = "";
    const openActions = new Set();
    const root = el("section", { class: "screen accounts-screen", "data-screen": "accounts" });
    view.append(root);
    const flow = signinFlow(ctx, { accepts: (id) => accounts.some((a) => a.id === id), changed: render, connected: refresh });
    const button = (id, props) => { const node = c.button.create(props); node.id = id; return node; };
    const keyId = (account) => encodeURIComponent(account.id);
    function disclosure(labelKey, children, id, open = false) {
        const summary = el("summary", { class: "log-toggle" }, [el("span", { text: t(labelKey) }), c.icon.create({ name: open ? "chevron-up" : "chevron-down" })]);
        const node = el("details", { class: "accounts-disclosure", open }, [summary, el("div", { class: "accounts-disclosure-body" }, children)]);
        node.addEventListener("toggle", () => { summary.replaceChild(c.icon.create({ name: node.open ? "chevron-up" : "chevron-down" }), summary.lastElementChild); if (id && node.isConnected) { if (node.open) openActions.add(id); else openActions.delete(id); } });
        return node;
    }
    function sync(next) {
        accounts = next;
        store.set("accounts", next);
        store.set("appInfo", { ...store.get("appInfo"), has_accounts: next.length > 0 });
    }
    async function refresh() {
        try {
            const data = await bridge.call("list_accounts");
            if (alive) { sync(data.accounts); loaded = true; }
        } catch (err) { if (alive) error = err; }
        render();
    }
    async function action(account, method, request, successKey) {
        if (busyId || confirming) return;
        busyId = account.id;
        error = null;
        warnings = [];
        render();
        try {
            const data = await bridge.call(method, request);
            if (!alive) return;
            if (data.account) sync(accounts.map((old) => old.id === data.account.id ? data.account : old));
            if (method === "rename_account") editId = null;
            if (successKey) c.toast.show({ tone: "success", messageKey: successKey });
        } catch (err) { if (alive) error = err; }
        finally { busyId = null; render(); }
    }
    async function signin(account) {
        error = null;
        editId = null;
        try { await flow.start("sign_in_again", { account_id: account.id }); } catch { /* The sign-in panel shows the envelope. */ }
    }
    async function remove(account) {
        if (busyId || confirming) return;
        confirming = true;
        const choices = [{ id: "cancel", labelKey: "common.action.cancel", variant: "ghost" }];
        if (!account.is_root) choices.push({ id: "keep", labelKey: "accounts.remove.keep", variant: "danger" });
        choices.push({ id: "delete", labelKey: "accounts.remove.delete", variant: "danger" });
        let choice;
        try { choice = await c.dialog.open({ titleKey: "accounts.remove.title", bodyKey: account.is_root ? "accounts.remove.root_body" : "accounts.remove.body", params: { account: account.label }, actions: choices }); }
        finally { confirming = false; }
        if (!alive || !["keep", "delete"].includes(choice)) return;
        busyId = account.id;
        error = null;
        warnings = [];
        render();
        try {
            const data = await bridge.call("remove_account", { account_id: account.id, delete_data: choice === "delete" });
            if (!alive) return;
            sync(data.accounts);
            warnings = data.warnings;
            editId = null;
            if (!accounts.length) {
                // Only a redirect needs a toast to preserve the warning.
                for (const warning of warnings) c.toast.show({ tone: "warning", messageKey: warning.message_key, params: warning.params, timeoutMs: 0 });
                warnings = [];
                router.navigate("#/setup/1");
            }
            else if (!warnings.length) c.toast.show({ tone: "success", messageKey: "accounts.remove.success" });
        } catch (err) { if (alive) error = err; }
        finally { busyId = null; render(); }
    }
    function signinPanel() {
        const status = flow.status;
        if (!status) return flow.error ? c.banner.create({ tone: "error", messageKey: flow.error.messageKey, params: flow.error.params }) : null;
        const active = activeStates.has(status.state);
        const children = [];
        if (active) children.push(c.spinner.create({ labelKey: statusKeys[status.state] }));
        children.push(c.banner.create({ tone: status.error_code === "terms_of_use" ? "warning" : status.state === "signed_in" ? "success" : ["busy", "failed", "timed_out"].includes(status.state) ? "warning" : "info", messageKey: flow.cancelling ? "accounts.signin.cancelling" : status.message_key || statusKeys[status.state], params: { account: accounts.find((a) => a.id === status.account_id)?.label } }));
        if (flow.error) children.push(c.banner.create({ tone: "error", messageKey: flow.error.messageKey, params: flow.error.params }));
        if (active) children.push(button("accounts-cancel-signin", { labelKey: "common.action.cancel", onClick: flow.cancel, disabled: flow.cancelling }));
        else if (status.state !== "signed_in" || status.error_code === "terms_of_use") children.push(button("accounts-retry-signin", { labelKey: "accounts.signin.retry", onClick: () => signin(accounts.find((a) => a.id === status.account_id)), disabled: flow.pending || !accounts.some((a) => a.id === status.account_id) }));
        return el("section", { class: "accounts-signin", "aria-label": t("accounts.signin.title"), "aria-live": "polite", "data-signin-state": status.state }, children);
    }
    function accountEntry(account) {
        const id = keyId(account);
        const locked = Boolean(busyId) || flow.pending || activeStates.has(flow.status?.state);
        const actions = [
            button(`accounts-signin-${id}`, { labelKey: "accounts.action.signin_again", disabled: locked, onClick: () => signin(account) }),
            button(`accounts-rename-${id}`, { labelKey: "accounts.action.rename", variant: "ghost", disabled: locked, onClick: () => { editId = account.id; label = account.label; error = null; render("accounts-name"); } }),
        ];
        if (!account.is_root) actions.push(button(`accounts-pause-${id}`, { labelKey: account.paused ? "accounts.action.resume" : "accounts.action.pause", variant: "ghost", disabled: locked, onClick: () => action(account, "set_account_paused", { account_id: account.id, paused: !account.paused }, account.paused ? "accounts.resumed" : "accounts.paused") }));
        const removeAction = button(`accounts-remove-${id}`, { labelKey: "common.action.remove", variant: "ghost", disabled: locked, onClick: () => remove(account) });
        removeAction.classList.add("button-danger");
        actions.push(removeAction);
        const more = disclosure("accounts.action.more", actions.slice(1), account.id, openActions.has(account.id) || editId === account.id);
        more.id = `accounts-more-${id}`;
        more.querySelector("summary").id = `accounts-more-toggle-${id}`;
        const row = c.accountRow.create({ account, actions: [actions[0], more] });
        if (!Number.isFinite(account.balance)) row.querySelector(".account-row-balance")?.remove();
        const entry = el("div", { class: "accounts-entry" }, row);
        if (account.is_root) entry.append(el("p", { class: "accounts-help", text: t("accounts.root_pause") }));
        if (editId === account.id) {
            const save = () => action(account, "rename_account", { account_id: account.id, label: label.trim() }, "accounts.rename.saved");
            entry.append(el("div", { class: "accounts-editor" }, [c.textField.create({ id: "accounts-name", labelKey: "accounts.rename.label", descriptionKey: "accounts.rename.description", value: label, maxLength: 40, autocomplete: "off", disabled: Boolean(busyId), error: error ? { messageKey: error.messageKey, params: error.params } : null, onInput: (value) => { label = value; }, onCommit: (value) => { label = value; } }),
                el("div", { class: "control-actions" }, [button("accounts-save-name", { labelKey: "common.action.save", variant: "primary", busy: busyId === account.id, onClick: save }), button("accounts-cancel-name", { labelKey: "common.action.cancel", variant: "ghost", disabled: Boolean(busyId), onClick: () => { editId = null; error = null; render(`accounts-rename-${id}`); } })]),
            ]));
        }
        return entry;
    }
    function render(focusId) {
        if (!alive) return;
        const focused = root.contains(document.activeElement) ? document.activeElement.id : null;
        c.dispose(root);
        root.replaceChildren(el("header", { class: "screen-header" }, el("h1", { text: t("accounts.title") })));
        if (error) root.append(c.banner.create({ tone: "error", messageKey: error.messageKey, params: error.params }));
        for (const warning of warnings) root.append(c.banner.create({ tone: "warning", messageKey: warning.message_key, params: warning.params }));
        const panel = signinPanel();
        if (panel) root.append(panel);
        if (!loaded && error) root.append(c.emptyState.create({ titleKey: "accounts.load.title", action: { labelKey: "common.action.retry", onClick: () => { error = null; refresh(); } } }));
        else if (!accounts.length) root.append(c.emptyState.create({ icon: "accounts", titleKey: "accounts.empty.title", bodyKey: "accounts.empty.body", action: { labelKey: "accounts.empty.action", variant: "primary", onClick: () => router.navigate("#/setup/1") } }));
        else root.append(el("div", { class: "accounts-list" }, [...accounts.map(accountEntry), disclosure("accounts.action.add_another", [el("p", { id: "accounts-add-reason", text: t("errors.app.accounts_add_unsupported") }), button("accounts-add-another", { labelKey: "accounts.action.add_another", disabled: true })])]));
        root.querySelector("#accounts-add-another")?.setAttribute("aria-describedby", "accounts-add-reason");
        const nextFocus = document.getElementById(focusId || focused);
        if (nextFocus && !nextFocus.disabled) nextFocus.focus({ preventScroll: true });
        else if (focused) (root.querySelector("#accounts-cancel-signin:not(:disabled), #accounts-retry-signin:not(:disabled), .account-row button:not(:disabled)") || view).focus({ preventScroll: true });
    }
    await refresh();
    flow.seed();
    const activeId = store.get("overview")?.signin_active?.account_id;
    if (activeId && !flow.status) await flow.restore(activeId);
    return { update() { render(); }, unmount() { alive = false; flow.dispose(); c.dispose(root); } };
}
