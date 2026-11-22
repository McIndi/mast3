// Admin -> Appliances: the appliance registry (/api/v1/appliances and
// /api/v1/environments).
import {api, el, showError} from "./api.js";

const rows = document.querySelector("[data-appliance-rows]");
const envRows = document.querySelector("[data-environment-rows]");
const form = document.querySelector("[data-appliance-form]");
const envForm = document.querySelector("[data-environment-form]");
const errorBox = document.querySelector("[data-error]");
const notice = document.querySelector("[data-notice]");
const title = document.querySelector("[data-editor-title]");
const saveButton = form.querySelector("[data-save]");
const cancelEdit = form.querySelector("[data-cancel-edit]");
let appliances = [];

const NUMBERS = ["soma_port", "web_port", "ssh_port"];
const TEXTS = ["address", "soma_spec_file", "notes"];

function say(message) {
    notice.textContent = message;
    notice.classList.toggle("d-none", !message);
}

async function act(promise, message) {
    showError(errorBox, null);
    say("");
    try {
        await promise;
        say(message);
        return true;
    } catch (error) {
        showError(errorBox, error);
        return false;
    } finally {
        await refresh();
    }
}

function edit(appliance) {
    form.reset();
    form.elements.original.value = appliance ? appliance.name : "";
    if (appliance) {
        form.elements.name.value = appliance.name;
        for (const name of NUMBERS.concat(TEXTS)) {
            form.elements[name].value = appliance[name] === null ? "" : appliance[name];
        }
        form.elements.tls_mode.value = appliance.tls_mode;
        form.elements.poll.checked = appliance.poll;
    }
    title.textContent = appliance ? "Change " + appliance.name : "Add an appliance";
    saveButton.textContent = appliance ? "Save" : "Add";
    cancelEdit.classList.toggle("d-none", !appliance);
    if (appliance) form.scrollIntoView({behavior: "smooth"});
}

function body() {
    const data = {tls_mode: form.elements.tls_mode.value, poll: form.elements.poll.checked};
    for (const name of NUMBERS) {
        const value = form.elements[name].value;
        data[name] = value === "" ? null : parseInt(value, 10);
    }
    for (const name of TEXTS) {
        data[name] = form.elements[name].value.trim() || null;
    }
    data.notes = data.notes || "";
    return data;
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const original = form.elements.original.value;
    const name = form.elements.name.value.trim();
    const data = body();
    let done;
    if (original) {
        if (!data.address) data.address = original === name ? name : appliances.find((a) => a.name === original).address;
        if (name !== original) data.rename = name;
        done = await act(api.patch("/appliances/" + encodeURIComponent(original), data), "Saved " + name + ".");
    } else {
        data.name = name;
        done = await act(api.post("/appliances", data), "Added " + name + ".");
    }
    if (done) edit(null);
});
cancelEdit.addEventListener("click", () => edit(null));

function ports(a) {
    return [a.soma_port, a.web_port, a.ssh_port].map((p) => p === null ? "–" : p).join(" / ");
}

function applianceRow(appliance) {
    const change = el("button", {type: "button", className: "btn btn-sm btn-outline-secondary"}, "Edit");
    change.addEventListener("click", () => edit(appliance));
    const remove = el("button", {type: "button", className: "btn btn-sm btn-outline-danger"}, "Remove");
    remove.addEventListener("click", () => {
        if (window.confirm("Remove " + appliance.name + "? It leaves its environments too.")) {
            act(api.del("/appliances/" + encodeURIComponent(appliance.name)), "Removed " + appliance.name + ".");
        }
    });
    return el("tr", {},
        el("td", {}, appliance.name, appliance.notes ? el("div", {className: "form-text"}, appliance.notes) : null),
        el("td", {}, appliance.address),
        el("td", {className: "small"}, ports(appliance)),
        el("td", {}, el("span", {className: "badge " + (appliance.tls_mode === "none" ? "text-bg-warning" : "text-bg-light")}, appliance.tls_mode)),
        el("td", {}, appliance.poll ? "yes" : ""),
        el("td", {className: "small"}, appliance.environments.join(" ")),
        el("td", {}, el("div", {className: "d-flex gap-1"}, change, remove)));
}

function environmentRow(environment) {
    // The members: a checkbox for each appliance
    const boxes = el("div", {className: "d-flex flex-wrap gap-2"});
    for (const appliance of appliances) {
        const id = "m-" + environment.name + "-" + appliance.name;
        boxes.append(el("div", {className: "form-check form-check-inline me-0"},
            el("input", {className: "form-check-input", type: "checkbox", id: id, value: appliance.name,
                         checked: environment.members.includes(appliance.name)}),
            el("label", {className: "form-check-label small", for: id}, appliance.name)));
    }
    const production = el("input", {className: "form-check-input", type: "checkbox", checked: environment.production,
                                     "aria-label": "Production"});
    const description = el("input", {className: "form-control form-control-sm", value: environment.description,
                                      "aria-label": "Description of " + environment.name});
    const save = el("button", {type: "button", className: "btn btn-sm btn-outline-primary"}, "Save");
    save.addEventListener("click", () => {
        // Keep the order of the members that stay, then the new ones
        const checked = [...boxes.querySelectorAll("input:checked")].map((i) => i.value);
        const members = environment.members.filter((m) => checked.includes(m))
            .concat(checked.filter((m) => !environment.members.includes(m)));
        act(api.patch("/environments/" + encodeURIComponent(environment.name),
                      {members: members, production: production.checked, description: description.value}),
            "Saved the environment " + environment.name + ".");
    });
    const remove = el("button", {type: "button", className: "btn btn-sm btn-outline-danger"}, "Remove");
    remove.addEventListener("click", () => {
        if (window.confirm("Remove the environment " + environment.name + "? Its appliances stay.")) {
            act(api.del("/environments/" + encodeURIComponent(environment.name)),
                "Removed the environment " + environment.name + ".");
        }
    });
    return el("tr", {},
        el("td", {}, environment.name),
        el("td", {}, el("div", {className: "form-check"}, production)),
        el("td", {}, boxes),
        el("td", {}, description),
        el("td", {}, el("div", {className: "d-flex gap-1"}, save, remove)));
}

envForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = {name: envForm.elements.name.value.trim(), description: envForm.elements.description.value,
                  production: envForm.elements.production.checked, members: []};
    if (await act(api.post("/environments", data), "Added the environment " + data.name + ".")) envForm.reset();
});

async function refresh() {
    try {
        appliances = (await api.get("/appliances")).appliances;
        const environments = (await api.get("/environments")).environments;
        rows.replaceChildren(...appliances.map(applianceRow));
        if (!appliances.length) {
            rows.append(el("tr", {}, el("td", {colSpan: 7, className: "text-body-secondary"},
                "No appliances yet. Add one below, or import hosts.conf and environments.conf with mast-appliances import.")));
        }
        envRows.replaceChildren(...environments.map(environmentRow));
    } catch (error) {
        showError(errorBox, error);
    }
}

refresh();
