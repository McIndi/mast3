// The Commands page: choose a namespace and a command, fill in the form
// that forms.js makes from its options, and submit it. A job opens on the
// Jobs page.
//
// ?from=JOB#COMMAND (the "Edit and run again" link of a job) fills in the
// form with the parameters of that job, and the target bar with its
// appliances and stored credential. Secrets come back from the server as
// ******** and uploads are gone after a day, so those are left empty and
// the page says so.
import {api, el, showError} from "./api.js";
import {buildForm, widgetControl} from "./forms.js";
import {renderData} from "./datatable.js";
import * as targets from "./targets.js";

const REDACTED = "********";

const list = document.querySelector("[data-command-list]");
const namespaceSelect = document.querySelector("[data-namespace]");
const search = document.querySelector("[data-command-search]");
const formBox = document.querySelector("[data-command-form]");
const errorBox = document.querySelector("[data-error]");
let commands = [];
// The job to fill in the form from, until its form is shown once
let fromJob = null;

// The values of `job` for the form of `command`, the target bar set from
// it, and what could not be filled in
function valuesFrom(command, job) {
    const values = {};
    const notes = [];
    for (const option of command.options) {
        if (!Object.prototype.hasOwnProperty.call(job.params, option.name)) continue;
        const value = job.params[option.name];
        const kind = widgetControl(option.widget);
        const name = option.name.replace(/_/g, " ");
        if (option.widget === "appliances") {
            if (Array.isArray(value) && value.length) targets.setAppliances(value);
        } else if (option.widget === "stored_credential") {
            if (value) targets.setStoredCredential(value);
        } else if (option.widget === "credentials") {
            if (Array.isArray(value) && value.length) {
                notes.push("Type the user name and the password in the target bar again.");
            }
        } else if (kind === "file") {
            if (value && (!Array.isArray(value) || value.length)) {
                notes.push("Choose the files of " + name + " again.");
            }
        } else if (kind === "hidden") {
            continue;
        } else if (option.secret || value === REDACTED) {
            if (value) notes.push("Type " + name + " again: secrets are not kept for the form.");
        } else {
            values[option.name] = value;
        }
    }
    return {values, notes};
}

function drawList() {
    const namespace = namespaceSelect.value;
    const needle = search.value.trim().toLowerCase();
    list.replaceChildren();
    let category = null;
    for (const command of commands) {
        if (needle) {
            if (!command.id.includes(needle) && !command.doc.toLowerCase().includes(needle)) continue;
        } else if (command.namespace !== namespace) {
            continue;
        }
        const heading = needle ? command.namespace : command.category;
        if (heading !== category) {
            category = heading;
            list.append(el("div", {className: "list-group-item small text-body-secondary fw-semibold border-0 pt-2"}, heading || "other"));
        }
        const item = el("a", {href: "#" + command.id, className: "list-group-item list-group-item-action py-1",
                              dataset: {command: command.id}}, command.name);
        list.append(item);
    }
    markActive();
}

function markActive() {
    const current = decodeURIComponent(window.location.hash.slice(1));
    for (const item of list.querySelectorAll("[data-command]")) {
        item.classList.toggle("active", item.dataset.command === current);
    }
}

function show(commandId) {
    const command = commands.find((c) => c.id === commandId);
    formBox.replaceChildren();
    showError(errorBox, null);
    if (!command) {
        formBox.textContent = "Choose a command.";
        return;
    }
    if (namespaceSelect.value !== command.namespace && !search.value) {
        namespaceSelect.value = command.namespace;
        drawList();
    }
    markActive();
    const summary = command.doc.split("\n\nParameters:")[0];
    const form = el("form", {noValidate: true});
    const output = el("div", {className: "mt-3"});
    const button = el("button", {type: "submit", className: "btn btn-primary"}, command.queued ? "Run" : "Run now");
    formBox.append(
        el("h1", {className: "h4"}, el("code", {}, command.id)),
        el("p", {className: "mast-help small"}, summary));
    if (!command.exclusive) {
        formBox.append(el("p", {className: "small text-body-secondary"}, "This command only reads."));
    }
    let values;
    if (fromJob && fromJob.command === command.id) {
        const filled = valuesFrom(command, fromJob);
        values = filled.values;
        formBox.append(el("div", {className: "alert alert-info small py-2"},
            "Filled in from job ", el("a", {href: "/jobs/" + fromJob.id}, el("code", {}, fromJob.id.slice(0, 8))),
            ". Check the values before you run it.",
            ...filled.notes.map((note) => el("div", {}, note))));
        fromJob = null;
        // A reload shows the form with its defaults again
        history.replaceState(null, "", window.location.pathname + window.location.hash);
    }
    formBox.append(form, output);
    const built = buildForm(command, form, values);
    form.append(el("div", {className: "mt-3"}, button));
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        button.disabled = true;
        showError(errorBox, null);
        output.replaceChildren();
        try {
            const params = await built.collect();
            const answer = await api.post("/jobs", {command: command.id, params: params});
            built.showProblems({});
            if (answer.job_id) {
                window.location.assign("/jobs/" + answer.job_id);
                return;
            }
            const result = answer.result || {};
            output.append(result.data ? renderData(result.data)
                : el("pre", {className: "mast-pre"}, result.text || "Done."));
        } catch (error) {
            const general = built.showProblems(error.fields);
            showError(errorBox, general.length ? new Error(error.message + " " + general.join("; ")) : error);
        } finally {
            button.disabled = false;
        }
    });
}

async function start() {
    try {
        commands = (await api.get("/commands")).commands;
    } catch (error) {
        showError(errorBox, error);
        return;
    }
    const namespaces = [...new Set(commands.map((c) => c.namespace))];
    for (const namespace of namespaces) {
        namespaceSelect.append(el("option", {value: namespace}, namespace));
    }
    const from = new URLSearchParams(window.location.search).get("from");
    let fromError = null;
    if (from) {
        try {
            fromJob = await api.get("/jobs/" + encodeURIComponent(from));
        } catch (error) {
            fromError = new Error("The form could not be filled in from job " + from + ". " + error.message);
        }
    }
    const wanted = decodeURIComponent(window.location.hash.slice(1));
    const first = commands.find((c) => c.id === wanted);
    if (first) namespaceSelect.value = first.namespace;
    drawList();
    show(wanted);
    if (fromError) showError(errorBox, fromError);
}

namespaceSelect.addEventListener("change", drawList);
search.addEventListener("input", drawList);
window.addEventListener("hashchange", () => show(decodeURIComponent(window.location.hash.slice(1))));
start();
