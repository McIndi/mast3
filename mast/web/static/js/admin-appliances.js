// Admin -> Appliances: the appliance registry (/api/v1/appliances and
// /api/v1/environments).
import {api, el, showError, when} from "./api.js";

const rows = document.querySelector("[data-appliance-rows]");
const envRows = document.querySelector("[data-environment-rows]");
const form = document.querySelector("[data-appliance-form]");
const envForm = document.querySelector("[data-environment-form]");
const errorBox = document.querySelector("[data-error]");
const notice = document.querySelector("[data-notice]");
const title = document.querySelector("[data-editor-title]");
const saveButton = form.querySelector("[data-save]");
const cancelEdit = form.querySelector("[data-cancel-edit]");
const pinPanel = document.querySelector("[data-pin-panel]");
const pinDetails = pinPanel.querySelector("[data-pin-details]");
const pinConfirm = pinPanel.querySelector("[data-pin-confirm]");
const unpinButton = pinPanel.querySelector("[data-unpin]");
let appliances = [];
let pinning = null;

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

function changedBadge(mismatch, at) {
    if (!mismatch) return null;
    return el("span", {className: "badge text-bg-danger ms-1",
                       title: "At " + when(at) + " the appliance showed " + mismatch +
                              ", not the pinned one. Check it, then pin again."}, "changed");
}

// The details of a certificate or a host key, with a checkbox to pin it
function pinCard(kind, found, pinned) {
    const title = kind === "tls" ? "TLS certificate" : "SSH host key";
    const card = el("div", {className: "col-12 col-lg-6"});
    if (!found) return null;
    if (found.error) {
        card.append(el("div", {className: "border rounded p-2 small"},
            el("strong", {}, title), el("div", {className: "text-danger"}, found.error)));
        return card;
    }
    const rows = kind === "tls"
        ? [["Port", found.port], ["Subject", found.subject], ["Issuer", found.issuer + (found.self_signed ? " (self-signed)" : "")],
           ["Valid", when(found.not_before) + " – " + when(found.not_after)], ["SHA-256", found.sha256]]
        : [["Port", found.port], ["Type", found.type], ["Fingerprint", found.fingerprint]];
    const same = kind === "tls" ? pinned.pin_sha256 === found.sha256 : !!pinned.ssh_host_key &&
        (pinned.ssh_host_key === found.key || pinned.ssh_host_key === found.fingerprint);
    const id = "pin-" + kind;
    card.append(el("div", {className: "border rounded p-2 small"},
        el("strong", {}, title),
        el("dl", {className: "row mb-1"}, ...rows.flatMap(([label, value]) => [
            el("dt", {className: "col-4 col-sm-3 fw-normal text-body-secondary"}, label),
            el("dd", {className: "col-8 col-sm-9 mb-0 text-break font-monospace"}, String(value))])),
        same ? el("div", {className: "text-success"}, "This is pinned.") : null,
        el("div", {className: "form-check"},
            el("input", {className: "form-check-input", type: "checkbox", id: id, checked: !same,
                         dataset: {kind: kind}}),
            el("label", {className: "form-check-label", for: id}, "Pin this " + (kind === "tls" ? "certificate" : "host key")))));
    return card;
}

async function openPin(appliance) {
    pinning = {appliance: appliance, found: null};
    pinPanel.querySelector("[data-pin-title]").textContent = "Pin " + appliance.name;
    pinDetails.replaceChildren(el("div", {className: "col-12 small text-body-secondary"}, "Connecting…"));
    unpinButton.classList.toggle("d-none", !(appliance.pin_sha256 || appliance.ssh_host_key));
    pinConfirm.disabled = true;
    pinPanel.classList.remove("d-none");
    pinPanel.scrollIntoView({behavior: "smooth"});
    showError(errorBox, null);
    try {
        const found = await api.post("/appliances/" + encodeURIComponent(appliance.name) + "/pin/preview",
                                     {tls: true, ssh: true});
        if (!pinning || pinning.appliance !== appliance) return;
        pinning.found = found;
        pinDetails.replaceChildren(...["tls", "ssh"].map((kind) => pinCard(kind, found[kind], appliance)).filter(Boolean));
        pinConfirm.disabled = false;
    } catch (error) {
        pinDetails.replaceChildren();
        showError(errorBox, error);
    }
}

pinConfirm.addEventListener("click", async () => {
    if (!pinning || !pinning.found) return;
    const name = pinning.appliance.name;
    const data = {};
    for (const box of pinDetails.querySelectorAll("input[data-kind]:checked")) {
        if (box.dataset.kind === "tls") data.sha256 = pinning.found.tls.sha256;
        if (box.dataset.kind === "ssh") data.ssh_host_key = pinning.found.ssh.key;
    }
    if (!Object.keys(data).length) {
        showError(errorBox, new Error("Check what to pin."));
        return;
    }
    if (await act(api.post("/appliances/" + encodeURIComponent(name) + "/pin", data),
                  "Pinned " + [data.sha256 ? "the certificate" : null, data.ssh_host_key ? "the SSH host key" : null]
                      .filter(Boolean).join(" and ") + " of " + name + ".")) {
        pinPanel.classList.add("d-none");
        pinning = null;
    }
});

unpinButton.addEventListener("click", async () => {
    if (!pinning) return;
    const name = pinning.appliance.name;
    if (!window.confirm("Remove the pinned certificate and SSH host key of " + name +
                        "? Its TLS check becomes ca, and stored credentials stop working with it until it is pinned again.")) return;
    if (await act(api.post("/appliances/" + encodeURIComponent(name) + "/unpin", {tls: true, ssh: true}),
                  "Removed the pins of " + name + ".")) {
        pinPanel.classList.add("d-none");
        pinning = null;
    }
});
pinPanel.querySelector("[data-pin-close]").addEventListener("click", () => {
    pinPanel.classList.add("d-none");
    pinning = null;
});

function applianceRow(appliance) {
    const change = el("button", {type: "button", className: "btn btn-sm btn-outline-secondary"}, "Edit");
    change.addEventListener("click", () => edit(appliance));
    const remove = el("button", {type: "button", className: "btn btn-sm btn-outline-danger"}, "Remove");
    remove.addEventListener("click", () => {
        if (window.confirm("Remove " + appliance.name + "? It leaves its environments too.")) {
            act(api.del("/appliances/" + encodeURIComponent(appliance.name)), "Removed " + appliance.name + ".");
        }
    });
    const pin = el("button", {type: "button", className: "btn btn-sm btn-outline-secondary"}, "Pin");
    pin.addEventListener("click", () => openPin(appliance));
    return el("tr", {},
        el("td", {}, appliance.name, appliance.notes ? el("div", {className: "form-text"}, appliance.notes) : null),
        el("td", {}, appliance.address),
        el("td", {className: "small"}, ports(appliance)),
        el("td", {}, el("span", {className: "badge " + (appliance.tls_mode === "none" ? "text-bg-warning" : "text-bg-light"),
                                 title: appliance.pin_sha256 ? "SHA-256 " + appliance.pin_sha256 : ""}, appliance.tls_mode),
           changedBadge(appliance.tls_mismatch, appliance.tls_mismatch_at)),
        el("td", {}, appliance.ssh_host_key
            ? el("span", {className: "badge text-bg-light", title: appliance.ssh_host_key}, "pinned")
            : el("span", {className: "small text-body-secondary"}, "–"),
           changedBadge(appliance.ssh_mismatch, appliance.ssh_mismatch_at)),
        el("td", {}, appliance.poll ? "yes" : ""),
        el("td", {className: "small"}, appliance.environments.join(" ")),
        el("td", {}, el("div", {className: "d-flex flex-wrap gap-1"}, change, pin, remove)));
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
            rows.append(el("tr", {}, el("td", {colSpan: 8, className: "text-body-secondary"},
                "No appliances yet. Add one below, or import hosts.conf and environments.conf with mast targets import.")));
        }
        envRows.replaceChildren(...environments.map(environmentRow));
    } catch (error) {
        showError(errorBox, error);
    }
}

refresh();
