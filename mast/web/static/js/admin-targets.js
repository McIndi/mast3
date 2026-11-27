// Admin -> Targets: the targets and the fleets of the registry
// (/api/v1/targets, /api/v1/fleets), made from the kinds of the plugins
// (/api/v1/kinds).
import {api, el, showError, when} from "./api.js";

const rows = document.querySelector("[data-target-rows]");
const fleetRows = document.querySelector("[data-fleet-rows]");
const form = document.querySelector("[data-target-form]");
const fleetForm = document.querySelector("[data-fleet-form]");
const errorBox = document.querySelector("[data-error]");
const notice = document.querySelector("[data-notice]");
const title = document.querySelector("[data-editor-title]");
const saveButton = form.querySelector("[data-save]");
const cancelEdit = form.querySelector("[data-cancel-edit]");
const attributesBox = form.querySelector("[data-attributes]");
const extensionsBox = document.querySelector("[data-extensions]");
const extensionForm = document.querySelector("[data-extension-form]");
const trustPanel = document.querySelector("[data-trust-panel]");
const trustCards = trustPanel.querySelector("[data-trust-cards]");
const pinConfirm = trustPanel.querySelector("[data-pin-confirm]");
let targets = [];
let kinds = {};
let editing = null;
let trusting = null;

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
        showError(errorBox, error);
        return false;
    } finally {
        await refresh();
    }
}

// The kind and every kind that it extends
function lineage(name) {
    const chain = [];
    while (name && kinds[name] && !chain.includes(name)) {
        chain.push(name);
        name = kinds[name].extends;
    }
    return chain;
}

function isA(target, kind) {
    return target.kinds.some((own) => lineage(own).includes(kind));
}

function label(trust) {
    return trust.scope ? trust.scope + "/" + trust.endpoint : trust.endpoint;
}

// One input for each attribute of a kind, filled from `values`
function attributeFields(box, kind, values, prefix) {
    box.replaceChildren();
    for (const attribute of (kinds[kind] || {attributes: []}).attributes) {
        const id = prefix + "-" + attribute.name;
        const value = values[attribute.name];
        const shown = value === undefined || value === null ? "" : String(value);
        const placeholder = attribute.default === null ? "default" : "default: " + attribute.default;
        let input;
        if (attribute.type === "bool" || attribute.choices.length) {
            const choices = attribute.type === "bool" ? ["true", "false"] : attribute.choices.map(String);
            input = el("select", {className: "form-select form-select-sm", id: id},
                el("option", {value: ""}, attribute.required ? "choose" : placeholder),
                ...choices.map((c) => el("option", {value: c, selected: c === shown},
                    attribute.type === "bool" ? (c === "true" ? "yes" : "no") : c)));
        } else if (attribute.type === "text") {
            input = el("textarea", {className: "form-control form-control-sm", id: id, rows: 2, placeholder: placeholder});
            input.value = shown;
        } else {
            input = el("input", {className: "form-control form-control-sm", id: id, value: shown, placeholder: placeholder,
                                 type: attribute.type === "port" || attribute.type === "int" ? "number" : "text"});
            if (attribute.type === "port") Object.assign(input, {min: 1, max: 65535});
        }
        input.dataset.attribute = attribute.name;
        input.dataset.type = attribute.type;
        input.required = attribute.required;
        box.append(el("div", {className: "col-6 col-lg-3"},
            el("label", {className: "form-label small", for: id}, attribute.name),
            input, attribute.help ? el("div", {className: "form-text"}, attribute.help) : null));
    }
}

// The values of the fields: null for an empty one (its default)
function attributeValues(box) {
    const values = {};
    for (const input of box.querySelectorAll("[data-attribute]")) {
        const raw = input.value.trim();
        let value = raw === "" ? null : raw;
        if (value !== null && (input.dataset.type === "int" || input.dataset.type === "port")) value = parseInt(value, 10);
        if (value !== null && input.dataset.type === "bool") value = value === "true";
        values[input.dataset.attribute] = value;
    }
    return values;
}

function fillKinds() {
    const bases = Object.values(kinds).filter((k) => !k.extends);
    form.elements.kind.replaceChildren(...bases.map((k) => el("option", {value: k.name}, k.name + " · " + k.title)));
    fleetForm.elements.kind.replaceChildren(...Object.values(kinds).map((k) =>
        el("option", {value: k.name}, k.name + (k.extends ? " (extends " + k.extends + ")" : ""))));
}

form.elements.kind.addEventListener("change", () => attributeFields(attributesBox, form.elements.kind.value, {}, "a"));

function edit(target) {
    editing = target;
    form.reset();
    form.elements.original.value = target ? target.name : "";
    form.elements.kind.disabled = !!target;
    if (target) {
        form.elements.name.value = target.name;
        form.elements.kind.value = target.kind;
        form.elements.address.value = target.address;
        form.elements.notes.value = target.notes;
    }
    attributeFields(attributesBox, form.elements.kind.value, target ? target.attributes : {}, "a");
    title.textContent = target ? "Change " + target.name : "Add a target";
    saveButton.textContent = target ? "Save" : "Add";
    cancelEdit.classList.toggle("d-none", !target);
    showExtensions();
    if (target) form.scrollIntoView({behavior: "smooth"});
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const original = form.elements.original.value;
    const name = form.elements.name.value.trim();
    const data = {address: form.elements.address.value.trim() || null, notes: form.elements.notes.value,
                  attributes: attributeValues(attributesBox)};
    let done;
    if (original) {
        if (name !== original) data.rename = name;
        done = await act(api.patch("/targets/" + encodeURIComponent(original), data), "Saved " + name + ".");
    } else {
        data.name = name;
        data.kind = form.elements.kind.value;
        // An attribute that is not given takes its default
        for (const key of Object.keys(data.attributes)) {
            if (data.attributes[key] === null) delete data.attributes[key];
        }
        done = await act(api.post("/targets", data), "Added " + name + ".");
    }
    if (done) edit(null);
});
cancelEdit.addEventListener("click", () => edit(null));

// The extensions of the target that is edited
function showExtensions() {
    extensionsBox.classList.toggle("d-none", !editing);
    if (!editing) return;
    const target = targets.find((t) => t.name === editing.name) || editing;
    const list = extensionsBox.querySelector("[data-extension-list]");
    list.replaceChildren(...target.extensions.map((extension) => {
        const remove = el("button", {type: "button", className: "btn btn-sm btn-link text-danger p-0 ms-2"}, "Remove");
        remove.addEventListener("click", () => {
            if (window.confirm("Remove the extension " + extension.kind + " of " + target.name + "?")) {
                act(api.del("/targets/" + encodeURIComponent(target.name) + "/extensions/" +
                            encodeURIComponent(extension.kind) + "?entry=" + encodeURIComponent(extension.entry)),
                    "Removed the extension " + extension.kind + ".");
            }
        });
        const values = Object.entries(extension.attributes).map(([k, v]) => k + "=" + v).join(", ");
        return el("li", {}, el("code", {}, extension.kind + (extension.entry ? " " + extension.entry : "")),
                  values ? " " + values : null, remove);
    }));
    if (!target.extensions.length) list.append(el("li", {className: "text-body-secondary"}, "None."));
    const choices = Object.values(kinds).filter((k) => k.extends && k.base === target.kind);
    const select = extensionForm.elements.kind;
    select.replaceChildren(...choices.map((k) => el("option", {value: k.name}, k.name + " · " + k.title)));
    extensionForm.classList.toggle("d-none", !choices.length);
    attributeFields(extensionForm.querySelector("[data-extension-attributes]"), select.value, {}, "x");
}

extensionForm.elements.kind.addEventListener("change", () =>
    attributeFields(extensionForm.querySelector("[data-extension-attributes]"), extensionForm.elements.kind.value, {}, "x"));
extensionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!editing) return;
    const attributes = attributeValues(extensionForm.querySelector("[data-extension-attributes]"));
    for (const key of Object.keys(attributes)) {
        if (attributes[key] === null) delete attributes[key];
    }
    const data = {kind: extensionForm.elements.kind.value, entry: extensionForm.elements.entry.value.trim(),
                  attributes: attributes};
    if (await act(api.post("/targets/" + encodeURIComponent(editing.name) + "/extensions", data),
                  "Added the extension " + data.kind + " to " + editing.name + ".")) {
        extensionForm.elements.entry.value = "";
        showExtensions();
    }
});

function trustBadges(target) {
    return target.trust.map((trust) => {
        const parts = [el("span", {className: "badge " + (trust.mode === "none" && trust.protocol === "tls"
                                                          ? "text-bg-warning" : "text-bg-light"),
                                   title: trust.pin ? "Pin " + trust.pin : ""}, label(trust) + ": " + trust.mode)];
        if (trust.mismatch) {
            parts.push(el("span", {className: "badge text-bg-danger ms-1",
                                   title: "At " + when(trust.mismatch_at) + " it showed " + trust.mismatch +
                                          ", not the pinned one. Check it, then pin again."}, "changed"));
        }
        if (trust.review) {
            parts.push(el("span", {className: "badge text-bg-warning ms-1", title: trust.review}, "review"));
        }
        return el("span", {className: "me-2 d-inline-block"}, ...parts);
    });
}

// What the preview found for one endpoint, with what can be done
function trustCard(target, trust, found) {
    const card = el("div", {className: "col-12 col-lg-6"});
    const box = el("div", {className: "border rounded p-2 small"},
        el("strong", {}, label(trust) + " · " + (trust.title || trust.protocol.toUpperCase())),
        el("div", {className: "text-body-secondary"}, "Checked by: " + trust.mode +
            (trust.review ? " · must be reviewed: " + trust.review : "")));
    card.append(box);
    if (!found || found.error) {
        box.append(el("div", {className: "text-danger"}, found ? found.error : "Not read."));
    } else {
        const details = trust.protocol === "ssh"
            ? [["Port", found.port], ["Type", found.type], ["Fingerprint", found.fingerprint]]
            : [["Port", found.port], ["Subject", found.subject],
               ["Issuer", found.issuer + (found.self_signed ? " (self-signed)" : "")],
               ["Valid", when(found.not_before) + " – " + when(found.not_after)], ["SHA-256", found.sha256]];
        box.append(el("dl", {className: "row mb-1"}, ...details.flatMap(([name, value]) => [
            el("dt", {className: "col-4 col-sm-3 fw-normal text-body-secondary"}, name),
            el("dd", {className: "col-8 col-sm-9 mb-0 text-break font-monospace"}, String(value))])));
        const value = trust.protocol === "ssh" ? found.key : found.sha256;
        const same = !!trust.pin && (trust.pin === value || trust.pin === found.fingerprint);
        if (same) {
            box.append(el("div", {className: "text-success"}, "This is pinned."));
            if (trust.review) {
                const keep = el("button", {type: "button", className: "btn btn-sm btn-outline-primary mt-1"},
                                "It is still right: keep the pin");
                keep.addEventListener("click", () => act(
                    api.post("/targets/" + encodeURIComponent(target.name) + "/review",
                             {endpoint: trust.endpoint, scope: trust.scope}),
                    "Kept the pin of " + label(trust) + " of " + target.name + ".").then(() => closeTrust()));
                box.append(keep);
            }
        }
        const id = "pin-" + label(trust).replace(/[^A-Za-z0-9]/g, "-");
        box.append(el("div", {className: "form-check"},
            el("input", {className: "form-check-input", type: "checkbox", id: id, checked: !same,
                         dataset: {endpoint: trust.endpoint, scope: trust.scope, value: value}}),
            el("label", {className: "form-check-label", for: id}, "Pin this " + (trust.protocol === "ssh" ? "host key" : "certificate"))));
    }
    const actions = el("div", {className: "d-flex flex-wrap gap-1 mt-1"});
    if (trust.protocol === "tls") {
        const mode = el("select", {className: "form-select form-select-sm w-auto", "aria-label": "Mode of " + label(trust)},
            ...["ca", "pin", "none"].map((m) => el("option", {value: m, selected: m === trust.mode}, m)));
        const save = el("button", {type: "button", className: "btn btn-sm btn-outline-secondary"}, "Set the mode");
        save.addEventListener("click", () => act(
            api.patch("/targets/" + encodeURIComponent(target.name) + "/trust",
                      {endpoint: trust.endpoint, scope: trust.scope, mode: mode.value}),
            "Set the mode of " + label(trust) + " of " + target.name + " to " + mode.value + ".").then(() => closeTrust()));
        actions.append(mode, save);
    }
    if (trust.pin) {
        const unpin = el("button", {type: "button", className: "btn btn-sm btn-outline-danger"}, "Remove the pin");
        unpin.addEventListener("click", () => {
            if (window.confirm("Remove the pin of " + label(trust) + " of " + target.name +
                               "? Stored credentials stop working with it until it is pinned again.")) {
                act(api.post("/targets/" + encodeURIComponent(target.name) + "/unpin",
                             {endpoint: trust.endpoint, scope: trust.scope}),
                    "Removed the pin of " + label(trust) + " of " + target.name + ".").then(() => closeTrust());
            }
        });
        actions.append(unpin);
    }
    box.append(actions);
    return card;
}

function closeTrust() {
    trustPanel.classList.add("d-none");
    trusting = null;
}

async function openTrust(target) {
    trusting = target;
    trustPanel.querySelector("[data-trust-title]").textContent = "Trust of " + target.name;
    trustCards.replaceChildren(el("div", {className: "col-12 small text-body-secondary"}, "Connecting…"));
    pinConfirm.disabled = true;
    trustPanel.classList.remove("d-none");
    trustPanel.scrollIntoView({behavior: "smooth"});
    showError(errorBox, null);
    try {
        const found = await api.post("/targets/" + encodeURIComponent(target.name) + "/pin/preview", {});
        if (trusting !== target) return;
        trustCards.replaceChildren(...target.trust.map((trust) => trustCard(target, trust, found[label(trust)])));
        pinConfirm.disabled = false;
    } catch (error) {
        trustCards.replaceChildren();
        showError(errorBox, error);
    }
}

pinConfirm.addEventListener("click", async () => {
    if (!trusting) return;
    const pins = [...trustCards.querySelectorAll("input[data-value]:checked")].map((box) =>
        ({endpoint: box.dataset.endpoint, scope: box.dataset.scope, value: box.dataset.value}));
    if (!pins.length) {
        showError(errorBox, new Error("Check what to pin."));
        return;
    }
    if (await act(api.post("/targets/" + encodeURIComponent(trusting.name) + "/pin", {pins: pins}),
                  "Pinned " + pins.map((p) => p.scope ? p.scope + "/" + p.endpoint : p.endpoint).join(", ") +
                  " of " + trusting.name + ".")) {
        closeTrust();
    }
});
trustPanel.querySelector("[data-trust-close]").addEventListener("click", closeTrust);

function targetRow(target) {
    const change = el("button", {type: "button", className: "btn btn-sm btn-outline-secondary"}, "Edit");
    change.addEventListener("click", () => edit(target));
    const trust = el("button", {type: "button", className: "btn btn-sm btn-outline-secondary"}, "Trust");
    trust.addEventListener("click", () => openTrust(target));
    trust.disabled = !target.trust.length;
    const remove = el("button", {type: "button", className: "btn btn-sm btn-outline-danger"}, "Remove");
    remove.addEventListener("click", () => {
        if (window.confirm("Remove " + target.name + "? It leaves its fleets too.")) {
            act(api.del("/targets/" + encodeURIComponent(target.name)), "Removed " + target.name + ".");
        }
    });
    return el("tr", {},
        el("td", {}, target.name, target.notes ? el("div", {className: "form-text"}, target.notes) : null),
        el("td", {}, target.kinds.join(" + ")),
        el("td", {}, target.address),
        el("td", {className: "small"}, ...trustBadges(target)),
        el("td", {className: "small"}, target.fleets.join(" ")),
        el("td", {}, el("div", {className: "d-flex flex-wrap gap-1"}, change, trust, remove)));
}

function fleetRow(fleet) {
    // The members: a checkbox for each target of the kind of the fleet
    const boxes = el("div", {className: "d-flex flex-wrap gap-2"});
    for (const target of targets.filter((t) => isA(t, fleet.kind))) {
        const id = "m-" + fleet.name + "-" + target.name;
        boxes.append(el("div", {className: "form-check form-check-inline me-0"},
            el("input", {className: "form-check-input", type: "checkbox", id: id, value: target.name,
                         checked: fleet.members.includes(target.name)}),
            el("label", {className: "form-check-label small", for: id}, target.name)));
    }
    const production = el("input", {className: "form-check-input", type: "checkbox", checked: fleet.production,
                                     "aria-label": "Production"});
    const description = el("input", {className: "form-control form-control-sm", value: fleet.description,
                                      "aria-label": "Description of " + fleet.name});
    const save = el("button", {type: "button", className: "btn btn-sm btn-outline-primary"}, "Save");
    save.addEventListener("click", () => {
        // Keep the order of the members that stay, then the new ones
        const checked = [...boxes.querySelectorAll("input:checked")].map((i) => i.value);
        const members = fleet.members.filter((m) => checked.includes(m))
            .concat(checked.filter((m) => !fleet.members.includes(m)));
        act(api.patch("/fleets/" + encodeURIComponent(fleet.name),
                      {members: members, production: production.checked, description: description.value}),
            "Saved the fleet " + fleet.name + ".");
    });
    const remove = el("button", {type: "button", className: "btn btn-sm btn-outline-danger"}, "Remove");
    remove.addEventListener("click", () => {
        if (window.confirm("Remove the fleet " + fleet.name + "? Its targets stay.")) {
            act(api.del("/fleets/" + encodeURIComponent(fleet.name)), "Removed the fleet " + fleet.name + ".");
        }
    });
    return el("tr", {},
        el("td", {}, fleet.name),
        el("td", {}, fleet.kind),
        el("td", {}, el("div", {className: "form-check"}, production)),
        el("td", {}, boxes),
        el("td", {}, description),
        el("td", {}, el("div", {className: "d-flex gap-1"}, save, remove)));
}

fleetForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = {name: fleetForm.elements.name.value.trim(), kind: fleetForm.elements.kind.value,
                  description: fleetForm.elements.description.value,
                  production: fleetForm.elements.production.checked, members: []};
    if (await act(api.post("/fleets", data), "Added the fleet " + data.name + ".")) fleetForm.reset();
});

async function refresh() {
    try {
        targets = (await api.get("/targets")).targets;
        const fleets = (await api.get("/fleets")).fleets;
        rows.replaceChildren(...targets.map(targetRow));
        if (!targets.length) {
            rows.append(el("tr", {}, el("td", {colSpan: 6, className: "text-body-secondary"},
                "No targets yet. Add one below, or import hosts.conf and environments.conf with mast targets import.")));
        }
        fleetRows.replaceChildren(...fleets.map(fleetRow));
        if (editing) showExtensions();
    } catch (error) {
        showError(errorBox, error);
    }
}

async function start() {
    try {
        kinds = Object.fromEntries((await api.get("/kinds")).kinds.map((k) => [k.name, k]));
    } catch (error) {
        showError(errorBox, error);
    }
    fillKinds();
    edit(null);
    await refresh();
}

start();
