// Admin -> Credentials: the credential store (/api/v1/credentials). The
// password field is write-only: no answer of the API has a password, so
// there is nothing to show.
import {api, el, showError, when} from "./api.js";

const rows = document.querySelector("[data-credential-rows]");
const form = document.querySelector("[data-credential-form]");
const bindingsBox = form.querySelector("[data-bindings]");
const errorBox = document.querySelector("[data-error]");
const notice = document.querySelector("[data-notice]");
const title = document.querySelector("[data-editor-title]");
const saveButton = form.querySelector("[data-save]");
const cancelEdit = form.querySelector("[data-cancel-edit]");
const passwordHelp = form.querySelector("[data-password-help]");
const testPanel = document.querySelector("[data-test-panel]");
const testForm = document.querySelector("[data-test-form]");
const testStatus = document.querySelector("[data-test-status]");
const usesPanel = document.querySelector("[data-uses-panel]");
let credentials = [];
let appliances = [];
let environments = [];
let testing = null;

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
        return answer === undefined ? true : (answer || true);
    } catch (error) {
        showError(errorBox, error);
        return false;
    } finally {
        await refresh();
    }
}

function bindingLabel(binding) {
    return (binding.kind === "environment" ? "env " : "") + binding.name;
}

function renderBindings(selected) {
    const chosen = new Set(selected.map((b) => b.name));
    bindingsBox.replaceChildren();
    const items = environments.map((e) => ({name: e.name, label: "env " + e.name}))
        .concat(appliances.map((a) => ({name: a.name, label: a.name})));
    if (!items.length) {
        bindingsBox.append(el("span", {className: "small text-body-secondary"},
            "There are no appliances yet. Add them in Admin → Appliances."));
    }
    items.forEach((item, index) => {
        const id = "b-" + index;
        bindingsBox.append(el("div", {className: "form-check form-check-inline me-0"},
            el("input", {className: "form-check-input", type: "checkbox", id: id, value: item.name,
                         checked: chosen.has(item.name)}),
            el("label", {className: "form-check-label small", for: id}, item.label)));
    });
}

function edit(credential) {
    form.reset();
    form.elements.original.value = credential ? credential.name : "";
    if (credential) {
        form.elements.name.value = credential.name;
        form.elements.username.value = credential.username;
        form.elements.purpose.value = credential.purpose;
        form.elements.description.value = credential.description;
    }
    form.elements.name.readOnly = !!credential;
    form.elements.password.required = !credential;
    passwordHelp.textContent = credential
        ? "Leave it empty to keep the password. It is never shown."
        : "Never shown again.";
    renderBindings(credential ? credential.bindings : []);
    title.textContent = credential ? "Change " + credential.name : "Add a credential";
    saveButton.textContent = credential ? "Save" : "Add";
    cancelEdit.classList.toggle("d-none", !credential);
    if (credential) form.scrollIntoView({behavior: "smooth"});
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const original = form.elements.original.value;
    const data = {
        username: form.elements.username.value.trim(),
        purpose: form.elements.purpose.value,
        description: form.elements.description.value,
        bindings: [...bindingsBox.querySelectorAll("input:checked")].map((i) => i.value),
    };
    const password = form.elements.password.value;
    let done;
    if (original) {
        const before = credentials.find((c) => c.name === original);
        if (password) {
            data.password = password;
        } else if (before && data.username !== before.username) {
            showError(errorBox, new Error("A new user name needs its password too."));
            return;
        }
        if (!password) delete data.username;
        done = await act(api.patch("/credentials/" + encodeURIComponent(original), data), "Saved " + original + ".");
    } else {
        data.name = form.elements.name.value.trim();
        data.password = password;
        done = await act(api.post("/credentials", data), "Stored " + data.name + ".");
    }
    form.elements.password.value = "";
    if (done) edit(null);
});
cancelEdit.addEventListener("click", () => edit(null));

// The appliances that a credential can be tested on: those it is bound to,
// directly or through an environment
function testTargets(credential) {
    const names = [];
    for (const binding of credential.bindings) {
        const members = binding.kind === "environment"
            ? ((environments.find((e) => e.name === binding.name) || {}).members || [])
            : [binding.name];
        for (const name of members) if (!names.includes(name)) names.push(name);
    }
    return names;
}

function openTest(credential) {
    testing = credential.name;
    usesPanel.classList.add("d-none");
    testPanel.querySelector("[data-test-title]").textContent = "Test " + credential.name;
    const select = testForm.elements.appliance;
    select.replaceChildren(...testTargets(credential).map((name) => el("option", {value: name}, name)));
    testStatus.textContent = select.options.length ? "" : "Bind it to an appliance first.";
    testPanel.classList.remove("d-none");
    testPanel.scrollIntoView({behavior: "smooth"});
}

async function waitFor(jobId) {
    for (;;) {
        const job = await api.get("/jobs/" + encodeURIComponent(jobId));
        if (job.finished) return job;
        await new Promise((resolve) => setTimeout(resolve, 1000));
    }
}

testForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = testing;
    const appliance = testForm.elements.appliance.value;
    showError(errorBox, null);
    try {
        const started = await api.post("/credentials/" + encodeURIComponent(name) + "/tests", {appliance: appliance});
        const link = el("a", {href: "/jobs/" + started.job_id}, "the job");
        testStatus.replaceChildren("Testing " + name + " on " + appliance + " (", link, ")…");
        await waitFor(started.job_id);
        const answer = await api.post("/credentials/" + encodeURIComponent(name) + "/tests/" +
                                      encodeURIComponent(started.job_id));
        const outcome = answer.outcome;
        const parts = [name + (outcome.ok ? " works on " : " does not work on ") + appliance + "."];
        if (outcome.firmware) parts.push("Firmware " + outcome.firmware + ".");
        if (outcome.problem) parts.push(outcome.problem);
        if (outcome.can_write && answer.credential.purpose === "collect") {
            parts.push("Warning: its user can change the appliance. Give a credential for collecting a user that may only read.");
        }
        testStatus.replaceChildren(parts.join(" "), " See ", el("a", {href: "/jobs/" + started.job_id}, "the job"), ".");
    } catch (error) {
        showError(errorBox, error);
        testStatus.textContent = "";
    }
    await refresh();
});
testPanel.querySelector("[data-test-close]").addEventListener("click", () => testPanel.classList.add("d-none"));

async function openUses(credential) {
    testPanel.classList.add("d-none");
    usesPanel.querySelector("[data-uses-title]").textContent = "Uses of " + credential.name;
    const body = usesPanel.querySelector("[data-use-rows]");
    body.replaceChildren();
    usesPanel.classList.remove("d-none");
    try {
        const uses = (await api.get("/credentials/" + encodeURIComponent(credential.name) + "/uses")).uses;
        body.replaceChildren(...uses.map((use) => el("tr", {},
            el("td", {}, when(use.used_at)),
            el("td", {}, el("a", {href: "/jobs/" + use.job_id}, use.job_id.slice(0, 8))),
            el("td", {}, use.appliance),
            el("td", {}, use.principal))));
        if (!uses.length) body.append(el("tr", {}, el("td", {colSpan: 4, className: "text-body-secondary"}, "Not used yet.")));
    } catch (error) {
        showError(errorBox, error);
    }
}
usesPanel.querySelector("[data-uses-close]").addEventListener("click", () => usesPanel.classList.add("d-none"));

function lastTest(credential) {
    if (!credential.last_tested_at) return "";
    return el("span", {className: "badge " + (credential.last_test_ok ? "text-bg-success" : "text-bg-danger"),
                       title: when(credential.last_tested_at)},
              credential.last_test_ok ? "works" : "failed");
}

function canChange(credential) {
    if (credential.can_write === null) return "";
    const warn = credential.can_write && credential.purpose === "collect";
    return el("span", {className: "badge " + (warn ? "text-bg-warning" : "text-bg-light"),
                       title: warn ? "A credential for collecting should not be able to change the appliance." : ""},
              credential.can_write ? "yes" : "no");
}

function credentialRow(credential) {
    const buttons = [["Edit", "btn-outline-secondary", () => edit(credential)],
                     ["Test", "btn-outline-secondary", () => openTest(credential)],
                     ["Uses", "btn-outline-secondary", () => openUses(credential)],
                     ["Delete", "btn-outline-danger", () => {
                         if (window.confirm("Delete the credential " + credential.name +
                                            "? Queued jobs that use it will fail.")) {
                             act(api.del("/credentials/" + encodeURIComponent(credential.name)),
                                 "Deleted " + credential.name + ".");
                         }
                     }]].map(([text, style, handler]) => {
        const button = el("button", {type: "button", className: "btn btn-sm " + style}, text);
        button.addEventListener("click", handler);
        return button;
    });
    return el("tr", {},
        el("td", {}, credential.name, credential.description ? el("div", {className: "form-text"}, credential.description) : null),
        el("td", {}, credential.username),
        el("td", {}, el("span", {className: "badge " + (credential.purpose === "change" ? "text-bg-warning" : "text-bg-light")},
                        credential.purpose)),
        el("td", {className: "small"}, credential.bindings.map(bindingLabel).join(", ")),
        el("td", {className: "small"}, when(credential.last_used_at)),
        el("td", {}, lastTest(credential)),
        el("td", {}, canChange(credential)),
        el("td", {}, el("div", {className: "d-flex flex-wrap gap-1"}, ...buttons)));
}

async function refresh() {
    try {
        credentials = (await api.get("/credentials")).credentials;
        appliances = (await api.get("/appliances")).appliances;
        environments = (await api.get("/environments")).environments;
        rows.replaceChildren(...credentials.map(credentialRow));
        if (!credentials.length) {
            rows.append(el("tr", {}, el("td", {colSpan: 8, className: "text-body-secondary"},
                "No credentials yet. Add one below, or with mast credentials add.")));
        }
        if (!form.elements.original.value) {
            const checked = [...bindingsBox.querySelectorAll("input:checked")].map((i) => ({name: i.value}));
            renderBindings(checked);
        }
    } catch (error) {
        showError(errorBox, error);
    }
}

edit(null);
refresh();
