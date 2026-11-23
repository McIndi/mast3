// Admin -> Schedules: the scheduled jobs (/api/v1/schedules). The parameters
// of the command come from the same form as on the Commands page; the
// appliances and the stored credential come from the target bar.
import {api, el, showError, when} from "./api.js";
import {buildForm} from "./forms.js";
import * as targets from "./targets.js";

const rows = document.querySelector("[data-schedule-rows]");
const form = document.querySelector("[data-schedule-form]");
const paramsBox = form.querySelector("[data-params]");
const preview = form.querySelector("[data-preview]");
const errorBox = document.querySelector("[data-error]");
const notice = document.querySelector("[data-notice]");
const title = document.querySelector("[data-editor-title]");
const saveButton = form.querySelector("[data-save]");
const cancelEdit = form.querySelector("[data-cancel-edit]");
const commandSelect = form.elements.command;
let commands = [];
let schedules = [];
let built = null;

function say(message) {
    notice.textContent = message;
    notice.classList.toggle("d-none", !message);
}

async function act(promise, message) {
    showError(errorBox, null);
    say("");
    try {
        const answer = await promise;
        say(message);
        return answer || true;
    } catch (error) {
        const fields = error.fields ? Object.entries(error.fields).map(([k, v]) => (k ? k + ": " : "") + v) : [];
        showError(errorBox, fields.length ? new Error(error.message + " " + fields.join("; ")) : error);
        return false;
    } finally {
        await refresh();
    }
}

function utc(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    return isNaN(date) ? iso : date.toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

// The form of the chosen command, with `values` to start with
function showParams(values) {
    paramsBox.replaceChildren();
    built = null;
    const command = commands.find((c) => c.id === commandSelect.value);
    if (!command) {
        paramsBox.append(el("span", {className: "small text-body-secondary"}, "Choose a command."));
        return;
    }
    paramsBox.append(el("p", {className: "small mast-help mb-2"}, command.doc.split("\n\nParameters:")[0]));
    if (command.exclusive) {
        paramsBox.append(el("p", {className: "small text-body-secondary mb-2"},
            "This command can change the appliances: it needs a stored credential for changing."));
    }
    built = buildForm(command, paramsBox, values || {});
}

let previewCount = 0;
async function showPreview() {
    const text = form.elements.cron.value.trim();
    const count = ++previewCount;
    if (!text) {
        preview.textContent = "";
        return;
    }
    try {
        const runs = (await api.get("/schedules/preview?cron=" + encodeURIComponent(text))).runs;
        if (count !== previewCount) return;
        preview.className = "small";
        preview.replaceChildren(el("span", {className: "text-body-secondary"}, "Next runs: "),
            runs.length ? runs.map((r) => utc(r) + " (" + when(r) + " here)").join(" · ") : "none in the next two years");
    } catch (error) {
        if (count !== previewCount) return;
        preview.className = "small text-danger";
        preview.textContent = error.message;
    }
}

function edit(schedule) {
    form.reset();
    showError(errorBox, null);
    form.elements.original.value = schedule ? schedule.name : "";
    form.elements.name.readOnly = !!schedule;
    commandSelect.disabled = !!schedule;
    if (schedule) {
        form.elements.name.value = schedule.name;
        commandSelect.value = schedule.command;
        form.elements.cron.value = schedule.cron;
        form.elements.description.value = schedule.description;
        form.elements.enabled.checked = schedule.enabled;
        targets.setAppliances(schedule.params.appliances || []);
        targets.setStoredCredential(schedule.params.credential || null);
    }
    showParams(schedule ? schedule.params : null);
    showPreview();
    title.textContent = schedule ? "Change " + schedule.name : "Add a schedule";
    saveButton.textContent = schedule ? "Save" : "Add";
    cancelEdit.classList.toggle("d-none", !schedule);
    if (schedule) form.scrollIntoView({behavior: "smooth"});
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!built) {
        showError(errorBox, new Error("Choose a command."));
        return;
    }
    const params = await built.collect();
    const data = {cron: form.elements.cron.value.trim(), description: form.elements.description.value,
                  enabled: form.elements.enabled.checked, params: params};
    const original = form.elements.original.value;
    let done;
    if (original) {
        done = await act(api.patch("/schedules/" + encodeURIComponent(original), data), "Saved " + original + ".");
    } else {
        data.name = form.elements.name.value.trim();
        data.command = commandSelect.value;
        done = await act(api.post("/schedules", data), "Added " + data.name + ".");
    }
    if (done) edit(null);
});
cancelEdit.addEventListener("click", () => edit(null));
commandSelect.addEventListener("change", () => showParams(null));
form.elements.cron.addEventListener("input", showPreview);

function scheduleRow(schedule) {
    const button = (text, style, handler) => {
        const node = el("button", {type: "button", className: "btn btn-sm " + style}, text);
        node.addEventListener("click", handler);
        return node;
    };
    const name = encodeURIComponent(schedule.name);
    const buttons = [
        button("Edit", "btn-outline-secondary", () => edit(schedule)),
        button("Run now", "btn-outline-primary", async () => {
            const answer = await act(api.post("/schedules/" + name + "/run"), "");
            if (answer && answer.job_id) window.location.assign("/jobs/" + answer.job_id);
        }),
        button(schedule.enabled ? "Pause" : "Enable", "btn-outline-secondary", () =>
            act(api.patch("/schedules/" + name, {enabled: !schedule.enabled}),
                (schedule.enabled ? "Paused " : "Enabled ") + schedule.name + ".")),
        el("a", {className: "btn btn-sm btn-outline-secondary", href: "/jobs?schedule=" + name}, "Jobs"),
        button("Remove", "btn-outline-danger", () => {
            if (window.confirm("Remove the schedule " + schedule.name + "? Its jobs stay.")) {
                act(api.del("/schedules/" + name), "Removed " + schedule.name + ".");
            }
        }),
    ];
    const last = schedule.last_job_id
        ? el("a", {href: "/jobs/" + schedule.last_job_id}, when(schedule.last_fired_at))
        : when(schedule.last_fired_at);
    return el("tr", {},
        el("td", {}, schedule.name, schedule.description ? el("div", {className: "form-text"}, schedule.description) : null),
        el("td", {}, el("code", {className: "small"}, schedule.command)),
        el("td", {className: "text-nowrap"}, el("code", {className: "small"}, schedule.cron)),
        el("td", {className: "small"}, schedule.enabled ? utc(schedule.next_run) : ""),
        el("td", {className: "small"}, last),
        el("td", {className: "small"},
           el("span", {className: "badge " + (schedule.enabled ? "text-bg-success" : "text-bg-secondary")},
              schedule.enabled ? "enabled" : "paused"),
           schedule.last_skip_reason ? el("div", {className: "text-warning-emphasis"}, schedule.last_skip_reason) : null),
        el("td", {}, el("div", {className: "d-flex flex-wrap gap-1"}, ...buttons)));
}

async function refresh() {
    try {
        schedules = (await api.get("/schedules")).schedules;
        rows.replaceChildren(...schedules.map(scheduleRow));
        if (!schedules.length) {
            rows.append(el("tr", {}, el("td", {colSpan: 7, className: "text-body-secondary"},
                "No schedules yet. Add one below, or with mast-jobs schedule-add.")));
        }
    } catch (error) {
        showError(errorBox, error);
    }
}

async function start() {
    try {
        commands = (await api.get("/commands")).commands.filter((c) => c.queued);
    } catch (error) {
        showError(errorBox, error);
        return;
    }
    commandSelect.append(el("option", {value: ""}, "Choose a command"));
    const namespaces = [...new Set(commands.map((c) => c.namespace))];
    for (const namespace of namespaces) {
        commandSelect.append(el("optgroup", {label: namespace},
            ...commands.filter((c) => c.namespace === namespace)
                .map((c) => el("option", {value: c.id}, c.id))));
    }
    edit(null);
    await refresh();
}

start();
