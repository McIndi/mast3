// The form of a command, made from the options of its model
// (GET /api/v1/commands, see mast.web.catalog).
//
// WIDGETS says how each widget of an option is asked for. The same map is
// in mast/web/widgets.py, and a unit test checks that the two are the same
// and that every widget of every command is in it, so that a new widget
// cannot go without a control. Options without a widget get a control from
// their type (see control() below).
import {api, el} from "./api.js";
import * as targets from "./targets.js";

// WIDGETS-BEGIN
export const WIDGETS = {
    "appliances": "target-bar",
    "credentials": "target-bar",
    "upload": "file",
    "out_dir": "hidden",
    "out_file": "hidden",
    "status_provider": "choices",
    "object_class": "choices",
    "configured:": "choices",
};
// WIDGETS-END

// The options that the target bar fills, by name
const TARGET_OPTIONS = ["no_check_hostname"];

export function widgetControl(widget) {
    if (!widget) return null;
    if (WIDGETS[widget]) return WIDGETS[widget];
    const prefix = widget.split(":")[0] + ":";
    return widget.includes(":") ? (WIDGETS[prefix] || null) : null;
}

function label(option) {
    return option.name.replace(/_/g, " ");
}

function textValue(option) {
    const value = option.default;
    return value === null || value === undefined ? "" : String(value);
}

// A control and a function that reads its value
function control(option, id, context) {
    const kind = widgetControl(option.widget);
    if (kind === "file") {
        const input = el("input", {className: "form-control form-control-sm", type: "file", id: id,
                                   multiple: option.kind === "list"});
        return {node: input, read: async () => {
            const tokens = [];
            for (const file of input.files) {
                tokens.push((await api.upload(file)).token);
            }
            return option.kind === "list" ? tokens : (tokens[0] || null);
        }};
    }
    if (kind === "choices") {
        return choicesControl(option, id, context);
    }
    if (option.kind === "flag") {
        const input = el("input", {className: "form-check-input", type: "checkbox", id: id,
                                   checked: !!option.default});
        return {node: input, flag: true, read: () => input.checked};
    }
    if (option.choices) {
        const select = el("select", {className: "form-select form-select-sm", id: id,
                                     multiple: option.kind === "list"});
        if (!option.required && option.kind !== "list" && option.default === null) {
            select.append(el("option", {value: ""}, ""));
        }
        for (const choice of option.choices) {
            const selected = option.kind === "list"
                ? (option.default || []).map(String).includes(String(choice))
                : String(choice) === textValue(option);
            select.append(el("option", {value: String(choice), selected: selected}, String(choice)));
        }
        return {node: select, read: () => option.kind === "list"
            ? [...select.selectedOptions].map((o) => o.value) : select.value};
    }
    if (option.kind === "list") {
        const area = el("textarea", {className: "form-control form-control-sm", id: id, rows: 3,
                                     placeholder: "One value per line",
                                     value: (option.default || []).join("\n")});
        return {node: area, read: () => area.value.split("\n").map((v) => v.trim()).filter(Boolean)};
    }
    const input = el("input", {className: "form-control form-control-sm", id: id,
                               value: option.secret ? "" : textValue(option)});
    if (option.secret) {
        input.type = "password";
        input.autocomplete = "new-password";
    } else if (option.type === "int" || option.type === "float") {
        input.type = "number";
        input.step = option.type === "int" ? "1" : "any";
    }
    input.required = option.required;
    return {node: input, read: () => {
        if (input.value === "") return null;
        if (option.type === "int") return parseInt(input.value, 10);
        if (option.type === "float") return parseFloat(input.value);
        return input.value;
    }};
}

// A select whose values come from POST /api/v1/choices/<widget>. A choice
// from what is configured on the appliances needs the appliances and the
// credentials of the target bar; if they cannot be asked, the user types the
// value.
function choicesControl(option, id, context) {
    const many = option.kind === "list";
    const select = el("select", {className: "form-select form-select-sm", id: id, multiple: many});
    const text = el("input", {className: "form-control form-control-sm d-none", id: id + "-text",
                              placeholder: many ? "Values, separated by commas" : "Value",
                              "aria-label": label(option)});
    const note = el("div", {className: "form-text"});
    const refresh = el("button", {type: "button", className: "btn btn-sm btn-link p-0 ms-2"}, "Reload");
    const configured = option.widget.startsWith("configured:");
    let typed = false;

    async function fill() {
        note.textContent = "Loading…";
        if (configured && (!targets.appliances().length || !targets.credentials().length)) {
            note.textContent = "Add appliances and credentials to the target bar to choose from what is configured on them, or type a value.";
            useText(true);
            return;
        }
        try {
            const answer = await api.post("/choices/" + encodeURIComponent(option.widget), {
                appliances: targets.appliances(), credentials: targets.credentials(),
                no_check_hostname: targets.noCheckHostname()});
            select.replaceChildren();
            if (!many && !option.required) select.append(el("option", {value: ""}, ""));
            for (const value of answer.choices) {
                select.append(el("option", {value: value, selected: value === option.default}, value));
            }
            note.textContent = configured ? "What every selected appliance has." : "";
            useText(false);
        } catch (error) {
            note.textContent = error.message + " Type a value instead.";
            useText(true);
        }
    }

    function useText(on) {
        typed = on;
        select.classList.toggle("d-none", on);
        text.classList.toggle("d-none", !on);
    }

    refresh.addEventListener("click", fill);
    if (configured) context.onTargets(fill);
    fill();
    const node = el("div", {}, select, text, el("div", {className: "d-flex align-items-baseline"}, note, refresh));
    return {node: node, read: () => {
        if (typed) {
            const values = text.value.split(",").map((v) => v.trim()).filter(Boolean);
            return many ? values : (values[0] || null);
        }
        return many ? [...select.selectedOptions].map((o) => o.value) : (select.value || null);
    }};
}

// Build the form of `command` in `container`. Returns {collect, showProblems}:
// collect() gives the params for POST /api/v1/jobs.
export function buildForm(command, container) {
    const readers = {};
    const groups = {};
    const targetListeners = [];
    const context = {onTargets: (listener) => targetListeners.push(listener)};
    targets.onChange(() => { for (const listener of targetListeners) listener(); });

    const fields = el("div", {className: "row g-3"});
    for (const option of command.options) {
        const kind = widgetControl(option.widget);
        if (kind === "target-bar") {
            readers[option.name] = () => option.widget === "credentials"
                ? targets.credentials() : targets.appliances();
            continue;
        }
        if (kind === "hidden") continue;
        if (TARGET_OPTIONS.includes(option.name)) {
            readers[option.name] = () => targets.noCheckHostname();
            continue;
        }
        const id = "opt-" + option.name;
        const made = control(option, id, context);
        readers[option.name] = made.read;
        const feedback = el("div", {className: "invalid-feedback d-block"});
        const help = option.help ? el("div", {className: "form-text"}, option.help) : null;
        let group;
        if (made.flag) {
            group = el("div", {className: "col-12"},
                el("div", {className: "form-check"}, made.node,
                   el("label", {className: "form-check-label", for: id}, label(option))),
                help, feedback);
        } else {
            group = el("div", {className: "col-12 col-lg-6"},
                el("label", {className: "form-label small fw-semibold", for: id},
                   label(option), option.required ? " *" : ""),
                made.node, help, feedback);
        }
        groups[option.name] = {group: group, feedback: feedback};
        fields.append(group);
    }
    container.append(fields);

    return {
        async collect() {
            const params = {};
            for (const [name, read] of Object.entries(readers)) {
                const value = await read();
                if (value === null || value === undefined || value === "") continue;
                if (Array.isArray(value) && !value.length) continue;
                params[name] = value;
            }
            return params;
        },
        showProblems(fieldsWithProblems) {
            for (const {feedback} of Object.values(groups)) feedback.textContent = "";
            const general = [];
            for (const [name, message] of Object.entries(fieldsWithProblems || {})) {
                if (groups[name]) {
                    groups[name].feedback.textContent = message;
                } else {
                    general.push((name ? label({name: name}) + ": " : "") + message);
                }
            }
            return general;
        },
    };
}
