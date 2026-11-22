// The Commands page: choose a namespace and a command, fill in the form
// that forms.js makes from its options, and submit it. A job opens on the
// Jobs page.
import {api, el, showError} from "./api.js";
import {buildForm} from "./forms.js";
import {renderData} from "./datatable.js";

const list = document.querySelector("[data-command-list]");
const namespaceSelect = document.querySelector("[data-namespace]");
const search = document.querySelector("[data-command-search]");
const formBox = document.querySelector("[data-command-form]");
const errorBox = document.querySelector("[data-error]");
let commands = [];

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
        el("p", {className: "mast-help small"}, summary),
        command.exclusive ? null : el("p", {className: "small text-body-secondary"}, "This command only reads."),
        form, output);
    const built = buildForm(command, form);
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
    const wanted = decodeURIComponent(window.location.hash.slice(1));
    const first = commands.find((c) => c.id === wanted);
    if (first) namespaceSelect.value = first.namespace;
    drawList();
    show(wanted);
}

namespaceSelect.addEventListener("change", drawList);
search.addEventListener("input", drawList);
window.addEventListener("hashchange", () => show(decodeURIComponent(window.location.hash.slice(1))));
start();
