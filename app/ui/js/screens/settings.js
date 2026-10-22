import { el } from "../dom.js";
import { scheduleTime } from "../schedule-time.js";

const sections = ["schedule", "run", "notifications", "app", "advanced"];
const secretIds = new Set(["run.openrouter_key", "notify.webhook_url"]);
const browserMode = value => value === "hidden" ? "hidden" : "visible";
const clone = value => JSON.parse(JSON.stringify(value));
const fieldId = id => `setting-${id.replace(/[._]/g, "-")}`;

export function mount(view, ctx) {
    const { bridge, i18n, store, components: c } = ctx;
    const { t, has, formatTime, formatNumber } = i18n;
    const root = el("section", { class: "screen screen-stack settings-screen", "data-screen": "settings", "aria-busy": "true" });
    view.replaceChildren(root);
    let alive = true;
    let section = ctx.params.section || "schedule";
    let baseline = null;
    let values = null;
    let secretsSet = {};
    const secretEdits = new Map();
    let schedule = null;
    let saving = false;
    let generation = 0;
    let legacyDismissed = false;
    let models = [];
    let fieldErrors = {};
    let noticeProps = [];
    let footer;
    let panel;
    let notices;
    let form;
    const fields = new Map();
    const textFields = new Map();

    const action = (labelKey, onClick, variant = "secondary") => c.button.create({ labelKey, onClick, variant });
    const option = (value, labelKey) => ({ value, labelKey });
    const choices = (prefix, list) => list.map(value => option(value, `${prefix}.${value}`));
    const changed = () => baseline ? { ...Object.fromEntries(Object.entries(values).filter(([id, value]) => JSON.stringify(value) !== JSON.stringify(baseline[id]))), ...Object.fromEntries(secretEdits) } : {};
    const dirty = () => Object.keys(changed()).length > 0;

    function paintNotices() {
        c.dispose(notices);
        notices.replaceChildren(...noticeProps.map(props => c.banner.create(props)));
    }

    function showError(error) {
        noticeProps = [{ tone: "error", messageKey: error.messageKey, params: error.params }];
        paintNotices();
    }

    function updateFooter() {
        c.dispose(footer);
        const save = action("common.action.save", saveSettings, "primary");
        save.id = "settings-save";
        save.disabled = !dirty() || saving;
        const cancel = action("common.action.cancel", () => { values = clone(baseline); secretEdits.clear(); fieldErrors = {}; noticeProps = []; render(); });
        cancel.id = "settings-cancel";
        cancel.disabled = !dirty() || saving;
        footer.replaceChildren(el("p", { class: "subtle", role: "status", text: dirty() ? t("settings.unsaved") : "" }), el("div", { class: "control-actions" }, [cancel, save]));
    }

    function setValue(id, value, repaint = false) {
        if (secretIds.has(id)) {
            if (value) secretEdits.set(id, value); else secretEdits.delete(id);
        } else values[id] = value;
        delete fieldErrors[id];
        if (repaint) {
            renderPanel();
            const control = root.querySelector(`#${fieldId(id)}`);
            (control?.matches("select,input") ? control : control?.querySelector('[role="radio"][aria-checked="true"]'))?.focus();
        } else paintFieldError(id);
        updateFooter();
    }

    function errorParams(id) {
        const bounds = id === "schedule.checks" ? [1, 12] : id === "schedule.random_delay" ? [0, 60] : [1, 500];
        return { checks: values["schedule.checks"], name: id === "advanced.driver_path" ? "msedgedriver.exe" : "msedge.exe", min: formatNumber(bounds[0]), max: formatNumber(bounds[1]) };
    }

    function paintFieldError(id) {
        const row = fields.get(id);
        if (!row) return;
        const key = `errors.settings.${fieldErrors[id]}`;
        if (textFields.has(id)) {
            const field = textFields.get(id);
            field.props = { ...field.props, value: secretIds.has(id) ? secretEdits.get(id) || "" : values[id] || "", placeholderKey: secretIds.has(id) ? secretPlaceholder(id) : null, error: fieldErrors[id] ? { messageKey: has(key) ? key : "settings.field.invalid", params: errorParams(id) } : null };
            field.node = c.textField.update(field.node, field.props);
            const remove = row.querySelector("[data-remove-secret]");
            if (remove) remove.hidden = !secretsSet[id] && !secretEdits.get(id);
            row.classList.toggle("has-field-error", Boolean(fieldErrors[id]));
            return;
        }
        const message = row.querySelector(`[data-field-error="${id}"]`);
        message.hidden = !fieldErrors[id];
        message.textContent = fieldErrors[id] ? t(has(key) ? key : "settings.field.invalid", errorParams(id)) : "";
        row.classList.toggle("has-field-error", Boolean(fieldErrors[id]));
        const controls = id === "schedule.window_start" || id === "schedule.window_end" ? [row.querySelector(id === "schedule.window_start" ? '[id$="-start"]' : '[id$="-end"]')] : row.querySelectorAll("input,select,button[role=switch],button[role=radio],button[aria-pressed]");
        for (const control of controls) {
            if (fieldErrors[id]) {
                control.setAttribute("aria-invalid", "true");
                control.setAttribute("aria-errormessage", message.id);
                const ids = new Set((control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
                ids.add(message.id);
                control.setAttribute("aria-describedby", [...ids].join(" "));
            } else {
                control.removeAttribute("aria-invalid");
                control.removeAttribute("aria-errormessage");
                const ids = (control.getAttribute("aria-describedby") || "").split(/\s+/).filter(value => value && value !== message.id);
                if (ids.length) control.setAttribute("aria-describedby", ids.join(" ")); else control.removeAttribute("aria-describedby");
            }
        }
    }

    function row(id, control, helpKey = null) {
        const wrapper = el("div", { class: "settings-row", "data-setting-id": id }, [control]);
        if (helpKey) {
            const help = el("p", { class: "field-description", id: `${fieldId(id)}-help`, text: t(helpKey) });
            wrapper.append(help);
            for (const input of control.querySelectorAll("input,select")) input.setAttribute("aria-describedby", help.id);
        }
        wrapper.append(el("p", { class: "settings-field-error", id: `${fieldId(id)}-error`, "data-field-error": id, role: "alert", hidden: true }));
        fields.set(id, wrapper);
        paintFieldError(id);
        return wrapper;
    }

    function switchRow(id, labelKey, descriptionKey) {
        return row(id, c.switchControl.create({ id: fieldId(id), labelKey, descriptionKey, checked: values[id], onChange: value => setValue(id, value) }));
    }

    function selectRow(id, labelKey, options, helpKey = null) {
        return row(id, c.select.create({ id: fieldId(id), labelKey, options, value: values[id], onChange: value => setValue(id, value) }), helpKey);
    }

    function secretPlaceholder(id) {
        return secretsSet[id] && secretEdits.get(id) !== "" ? "settings.secret.saved" : "settings.secret.empty";
    }

    function textRow(id, labelKey, helpKey = null, pickKind = null) {
        const secret = secretIds.has(id);
        const key = `errors.settings.${fieldErrors[id]}`;
        const props = { id: fieldId(id), labelKey, descriptionKey: helpKey, type: secret ? "password" : id === "run.ollama_url" ? "url" : "text", value: secret ? secretEdits.get(id) || "" : values[id] || "", autocomplete: "off", placeholderKey: secret ? secretPlaceholder(id) : null, error: fieldErrors[id] ? { messageKey: has(key) ? key : "settings.field.invalid", params: errorParams(id) } : null, onInput: value => setValue(id, value) };
        const field = { node: c.textField.create(props), props };
        const controls = el("div", { class: "settings-input-actions" }, [field.node]);
        textFields.set(id, field);
        if (pickKind) field.node.querySelector("input").dir = "ltr";
        if (secret) {
            const remove = action("settings.secret.remove", () => {
                secretEdits.set(id, "");
                delete fieldErrors[id];
                paintFieldError(id);
                updateFooter();
                field.node.querySelector("input").focus();
            }, "ghost");
            remove.dataset.removeSecret = id;
            remove.hidden = !secretsSet[id] && !secretEdits.get(id);
            controls.append(remove);
        }
        if (pickKind) {
            const pick = action("settings.advanced.pick", async () => {
                try {
                    const data = await bridge.call("pick_path", { kind: pickKind });
                    if (alive && data.path != null) setValue(id, data.path);
                } catch (error) { if (alive) showError(error); }
                finally { if (alive && pick.isConnected) pick.focus({ preventScroll: true }); }
            });
            pick.id = `${fieldId(id)}-browse`;
            pick.setAttribute("aria-label", `${t(labelKey)}, ${t("settings.advanced.pick")}`);
            field.node.classList.add("settings-path-field");
            field.node.append(pick);
        }
        const wrapper = el("div", { class: "settings-row", "data-setting-id": id }, [controls]);
        fields.set(id, wrapper);
        wrapper.classList.toggle("has-field-error", Boolean(fieldErrors[id]));
        return wrapper;
    }

    function scheduleSection() {
        const content = el("div", { class: "settings-rows" });
        content.append(row("schedule.mode", c.segmented.create({ id: fieldId("schedule.mode"), labelKey: "settings.schedule.mode.label", value: values["schedule.mode"], options: choices("settings.schedule.mode", ["automatic", "fixed", "manual"]), onChange: value => setValue("schedule.mode", value, true) })));
        const mode = values["schedule.mode"];
        content.append(el("p", { class: "settings-mode-help field-description", text: t(`settings.schedule.${mode}_help`) }));
        if (mode === "automatic") {
            const range = c.timeRange.create({ id: fieldId("schedule.window"), labelKey: "settings.schedule.window.label", start: values["schedule.window_start"], end: values["schedule.window_end"], onChange: value => { setValue("schedule.window_start", value.start); setValue("schedule.window_end", value.end); } });
            const rangeRow = row("schedule.window_start", range);
            const endError = el("p", { class: "settings-field-error", id: `${fieldId("schedule.window_end")}-error`, "data-field-error": "schedule.window_end", role: "alert", hidden: true });
            rangeRow.append(endError);
            fields.set("schedule.window_end", rangeRow);
            paintFieldError("schedule.window_end");
            content.append(rangeRow, el("p", { class: "subtle", text: t("settings.schedule.check_count", { count: values["schedule.checks"] }) }));
        } else if (mode === "fixed") {
            content.append(row("schedule.time", c.timeInput.create({ id: fieldId("schedule.time"), labelKey: "settings.schedule.time.label", value: values["schedule.time"], onChange: value => setValue("schedule.time", value) })));
        }
        if (mode !== "manual") {
            content.append(row("schedule.days", c.weekdayPicker.create({ id: fieldId("schedule.days"), labelKey: "settings.schedule.days.label", days: values["schedule.days"], onChange: value => setValue("schedule.days", value) })), row("schedule.random_delay", c.select.create({ id: fieldId("schedule.random_delay"), labelKey: "settings.schedule.delay.label", options: [option("0", "settings.schedule.delay.none"), ...[5, 15, 30, 60].map(value => ({ value: String(value), label: t("settings.schedule.delay.minutes", { count: value }) }))], value: String(values["schedule.random_delay"]), onChange: value => setValue("schedule.random_delay", Number(value)) })), switchRow("schedule.catch_up", "settings.schedule.catch_up.label", "settings.schedule.catch_up.help"), switchRow("schedule.on_battery", "settings.schedule.battery.label", "settings.schedule.battery.help"));
        }
        if (schedule.next_at && schedule.mode === mode && !Object.keys(changed()).some(id => id.startsWith("schedule."))) content.append(el("dl", { class: "settings-schedule-next" }, [el("dt", { class: "subtle", text: t(schedule.next_is_check ? "overview.next_check" : "overview.next_run") }), el("dd", { class: "settings-schedule-status", text: scheduleTime(schedule, store.get("overview")?.generated_at || new Date().toISOString(), i18n, store.get("appInfo")?.format_locale) })]));
        if (schedule.mode !== "manual" && (!schedule.task_exists || !schedule.in_sync)) content.append(c.banner.create({ tone: "warning", messageKey: "settings.schedule.not_ready" }));
        if (schedule.error_code) {
            const key = `errors.app.${schedule.error_code.replaceAll(".", "_")}`;
            content.append(c.banner.create({ tone: "error", messageKey: has(key) ? key : "errors.app.schedule_failed" }));
        }
        if (mode !== "manual") content.append(el("p", { class: "field-description", text: t("settings.schedule.description") }));
        if (schedule.legacy_task && !legacyDismissed) content.append(c.banner.create({ tone: "warning", messageKey: schedule.legacy_task.time ? "settings.schedule.legacy" : "settings.schedule.legacy_unknown", params: schedule.legacy_task.time ? { time: formatTime(schedule.legacy_task.time) } : undefined, actions: [action("settings.schedule.replace", replaceLegacy), action("settings.schedule.keep", () => { legacyDismissed = true; renderPanel(); }, "ghost")] }));
        return content;
    }

    function runSection() {
        const browserHelp = el("p", { class: "field-description", id: `${fieldId("run.browser_mode")}-help`, "aria-live": "polite", text: t(`settings.run.browser.${browserMode(values["run.browser_mode"])}_help`) });
        const browserControl = c.segmented.create({ id: fieldId("run.browser_mode"), labelKey: "settings.run.browser.label", value: browserMode(values["run.browser_mode"]), options: choices("settings.run.browser", ["visible", "hidden"]), onChange: value => { setValue("run.browser_mode", value); browserHelp.textContent = t(`settings.run.browser.${browserMode(value)}_help`); } });
        browserControl.querySelector('[role="radiogroup"]').setAttribute("aria-describedby", browserHelp.id);
        const browserRow = row("run.browser_mode", browserControl);
        browserRow.append(browserHelp);
        const content = el("div", { class: "settings-rows" }, [browserRow]);
        const taskSwitches = el("fieldset", { class: "settings-task-options" }, [el("legend", { class: "field-label", text: t("settings.run.tasks.label") })]);
        for (const task of store.get("appInfo").task_ids) taskSwitches.append(c.switchControl.create({ id: `setting-task-${task}`, labelKey: `common.task.${task}`, checked: !values["run.tasks_off"].includes(task), onChange: enabled => setValue("run.tasks_off", enabled ? values["run.tasks_off"].filter(value => value !== task) : [...values["run.tasks_off"], task]) }));
        content.append(row("run.tasks_off", taskSwitches), row("run.search_source", c.select.create({ id: fieldId("run.search_source"), labelKey: "settings.run.source.label", value: values["run.search_source"], options: choices("settings.run.source", ["trends", "ollama", "openrouter"]), onChange: value => setValue("run.search_source", value, true) })));
        if (values["run.search_source"] === "ollama") {
            const names = [...new Set([values["run.ollama_model"], ...models].filter(Boolean))];
            content.append(textRow("run.ollama_url", "settings.run.ollama_url.label"), selectRow("run.ollama_model", "settings.run.ollama_model.label", names.map(value => ({ value, label: value }))), el("div", { class: "control-actions" }, [action("settings.run.models.refresh", refreshModels)]));
        } else if (values["run.search_source"] === "openrouter") {
            content.append(el("p", { class: "muted", text: t("settings.run.openrouter_help") }), textRow("run.openrouter_key", "settings.run.openrouter_key.label"), textRow("run.openrouter_model", "settings.run.openrouter_model.label"));
        }
        return content;
    }

    function notificationsSection() {
        return el("div", { class: "settings-rows" }, [selectRow("notify.policy", "settings.notifications.policy.label", choices("settings.notifications.policy", ["always", "errors", "never"])), textRow("notify.webhook_url", "settings.notifications.webhook.label", "settings.notifications.webhook.help")]);
    }

    function appSection() {
        const languages = [option("system", "settings.app.language.system"), ...store.get("appInfo").locales.map(locale => ({ value: locale.code, label: locale.name }))];
        return el("div", { class: "settings-rows" }, [switchRow("app.autostart", "settings.app.autostart.label", "settings.app.autostart.help"), switchRow("app.close_to_tray", "settings.app.tray.label", "settings.app.tray.help"), selectRow("app.language", "settings.app.language.label", languages, "settings.app.language.help"), switchRow("app.update_check", "settings.app.updates.label", "settings.app.updates.help"), el("div", { class: "control-actions" }, [action("settings.app.updates.check", checkUpdates)])]);
    }

    function advancedSection() {
        const exportButton = action("settings.advanced.export.label", exportDiagnostics);
        exportButton.id = "settings-export";
        exportButton.setAttribute("aria-describedby", "settings-export-description");
        return el("div", { class: "settings-rows settings-advanced", id: "settings-advanced" }, [textRow("advanced.edge_path", "settings.advanced.edge.label", "settings.advanced.edge.help", "edge"), textRow("advanced.driver_path", "settings.advanced.driver.label", "settings.advanced.driver.help", "driver"), textRow("advanced.data_folder", "settings.advanced.folder.label", "settings.advanced.folder.help", "data_folder"), selectRow("advanced.log_level", "settings.advanced.log.label", [option("DEBUG", "settings.advanced.log.debug"), option("INFO", "settings.advanced.log.info"), option("WARNING", "settings.advanced.log.warning"), option("ERROR", "settings.advanced.log.error")]), el("div", { class: "settings-export" }, [exportButton, el("p", { class: "field-description", id: "settings-export-description", text: t("settings.advanced.export.help") })])]);
    }

    function renderPanel() {
        c.dispose(panel);
        fields.clear();
        textFields.clear();
        const builders = { schedule: scheduleSection, run: runSection, notifications: notificationsSection, app: appSection, advanced: advancedSection };
        form = el("fieldset", { class: "settings-form", disabled: saving }, [el("legend", { class: "sr-only", text: t(`settings.section.${section}`) }), builders[section]()]);
        panel.setAttribute("aria-labelledby", `settings-section-${section}`);
        panel.replaceChildren(c.card.create({ children: [form] }));
    }

    function render() {
        c.dispose(root);
        fields.clear();
        textFields.clear();
        const navigation = el("nav", { class: "settings-sections", "aria-label": t("settings.sections.label") });
        for (const name of sections) navigation.append(el("a", { id: `settings-section-${name}`, href: `#/settings/${name}`, "aria-current": name === section ? "page" : null, text: t(`settings.section.${name}`) }));
        notices = el("div", { class: "screen-stack", "aria-live": "polite" });
        panel = el("div", { class: "settings-panel", "data-section": section, role: "region", "aria-labelledby": `settings-section-${section}` });
        footer = el("footer", { class: "settings-footer" });
        root.replaceChildren(el("header", { class: "screen-header" }, el("h1", { text: t("settings.title") })), navigation, notices, panel, footer);
        renderPanel();
        paintNotices();
        updateFooter();
        root.setAttribute("aria-busy", "false");
    }

    async function load() {
        const stamp = ++generation;
        root.setAttribute("aria-busy", "true");
        try {
            const data = await bridge.call("get_settings");
            if (!alive || stamp !== generation) return;
            baseline = clone(data.values);
            values = clone(data.values);
            secretsSet = { ...data.secrets_set };
            secretEdits.clear();
            schedule = data.schedule;
            fieldErrors = {};
            noticeProps = [];
            store.set("settings", data);
            render();
        } catch (error) {
            if (alive && stamp === generation) {
                c.dispose(root);
                root.replaceChildren(c.banner.create({ tone: "error", messageKey: error.messageKey, params: error.params, actions: [action("common.action.retry", load)] }));
                root.setAttribute("aria-busy", "false");
            }
        }
    }

    async function saveSettings() {
        if (saving || !dirty()) return;
        saving = true;
        form.disabled = true;
        updateFooter();
        try {
            const data = await bridge.call("save_settings", { changes: changed() });
            if (!alive) return;
            baseline = clone(data.values);
            values = clone(data.values);
            secretsSet = { ...data.secrets_set };
            secretEdits.clear();
            schedule = data.schedule;
            fieldErrors = {};
            noticeProps = [{ tone: "success", messageKey: "settings.saved" }, ...data.warnings.map(warning => ({ tone: "warning", messageKey: warning.message_key, params: warning.params }))];
            // The shell owns language reload after a successful save.
            store.set("settings", data);
        } catch (error) {
            if (!alive) return;
            fieldErrors = error.fields || {};
            const first = Object.keys(fieldErrors)[0];
            const errorSection = first?.startsWith("notify.") ? "notifications" : first?.split(".")[0];
            if (sections.includes(errorSection)) section = errorSection;
            noticeProps = [{ tone: "error", messageKey: error.messageKey, params: error.params }];
        } finally {
            if (alive) {
                saving = false;
                render();
                const invalid = root.querySelector('[aria-invalid="true"]');
                (invalid || root.querySelector("#settings-save:not(:disabled)") || view)?.focus();
            }
        }
    }

    async function replaceLegacy() {
        const choice = await c.dialog.open({ titleKey: "settings.schedule.replace_title", bodyKey: "settings.schedule.replace_body", actions: [{ id: "cancel", labelKey: "common.action.cancel" }, { id: "replace", labelKey: "settings.schedule.replace", variant: "primary" }] });
        if (!alive || choice !== "replace") return;
        try {
            schedule = await bridge.call("replace_legacy_task");
            if (alive) { noticeProps = [{ tone: "success", messageKey: "settings.schedule.replaced" }]; render(); }
        } catch (error) { if (alive) showError(error); }
    }

    async function refreshModels() {
        const baseUrl = values["run.ollama_url"];
        try {
            const data = await bridge.call("list_ollama_models", { base_url: baseUrl });
            if (!alive || values["run.ollama_url"] !== baseUrl) return;
            models = data.models;
            if (!models.length) { noticeProps = [{ tone: "info", messageKey: "settings.run.models.empty" }]; paintNotices(); }
            if (section === "run") renderPanel();
        } catch (error) { if (alive) showError(error); }
    }

    async function checkUpdates() {
        try {
            const data = await bridge.call("check_updates", { force: true });
            if (alive) { store.set("update", data); noticeProps = [{ tone: "info", messageKey: data.available ? "settings.app.updates.available" : "settings.app.updates.current", params: { version: data.latest } }]; paintNotices(); }
        } catch (error) { if (alive) showError(error); }
    }

    async function exportDiagnostics() {
        try {
            const data = await bridge.call("export_diagnostics");
            if (alive && data.path) { noticeProps = [{ tone: "success", messageKey: "settings.advanced.export.saved", params: { path: data.path } }]; paintNotices(); }
        } catch (error) { if (alive) showError(error); }
    }

    const offSettings = bridge.on("settings.changed", () => {
        if (!alive || !baseline) return;
        if (saving) return;
        if (dirty()) { noticeProps = [{ tone: "warning", messageKey: "settings.external_change", actions: [action("settings.reload", load)] }]; paintNotices(); }
        else load();
    });
    root.append(c.spinner.create({ labelKey: "settings.loading" }));
    load();
    return {
        update(params) { section = params.section || "schedule"; if (values) render(); },
        unmount() { alive = false; generation += 1; offSettings(); secretEdits.clear(); textFields.clear(); c.dispose(root); }
    };
}
