// The target bar: the appliances that the next command talks to, and how to
// log in to them: a stored credential, or a user name and a password.
//
// Only the names of the appliances are kept, in sessionStorage. The user
// name and the password stay in the memory of this page: they are sent in
// the body of the POST that submits a job, and they are never stored in the
// browser. A stored credential is sent by name; the password never comes to
// the browser.
//
// The stored credentials offered are those that can be used on every
// appliance of the bar (GET /api/v1/credentials?appliances=...). A viewer
// may not list them, and sees no picker.
//
// Test asks whether MAST can log in to each appliance with these
// credentials, to SOMA and to SSH (POST /api/v1/connectivity, a passive
// job of system.test-connectivity), and shows the answer by each name.
import {api, el} from "./api.js";

const KEY = "mast.targets";

function load() {
    try {
        const stored = JSON.parse(sessionStorage.getItem(KEY) || "[]");
        return Array.isArray(stored) ? stored.filter((n) => typeof n === "string") : [];
    } catch (e) {
        return [];
    }
}

function save(names) {
    try { sessionStorage.setItem(KEY, JSON.stringify(names)); } catch (e) { /* no storage */ }
}

const listeners = [];
let names = load();
// The answer of the last Test, by appliance: {soma, ssh, firmware, problem}
let tested = {};

// ?appliance=NAME in the URL adds an appliance, so that other pages can
// link to "run a command on this appliance"
for (const name of new URLSearchParams(window.location.search).getAll("appliance")) {
    if (name && !names.includes(name)) names.push(name);
}
save(names);

const bar = document.getElementById("target-bar");

function render() {
    if (!bar) return;
    const list = bar.querySelector("[data-target-list]");
    list.replaceChildren();
    if (!names.length) {
        list.append(el("span", {className: "small text-body-secondary"}, "No appliances yet."));
    }
    for (const name of names) {
        const remove = el("button", {type: "button", className: "btn-close ms-1",
                                     "aria-label": "Remove " + name});
        remove.addEventListener("click", () => setAppliances(names.filter((n) => n !== name)));
        list.append(el("span", {className: "badge text-bg-primary mast-target-chip d-inline-flex align-items-center"},
                       name, remove));
        const result = tested[name];
        if (result) {
            const flag = (label, ok) => el("span", {className: "badge " + (ok ? "text-bg-success" : "text-bg-danger")},
                                           label + (ok ? " ok" : " failed"));
            list.append(el("span", {className: "d-inline-flex gap-1 align-items-center me-2",
                                    title: [result.firmware, result.problem].filter(Boolean).join(" — ")},
                           flag("SOMA", result.soma === "yes"), flag("SSH", result.ssh === "yes")));
        }
    }
}

function changed() {
    tested = {};
    save(names);
    render();
    if (bar) offerStored();
    for (const listener of listeners) listener();
}

export function setAppliances(next) {
    names = [...new Set(next.map((n) => n.trim()).filter(Boolean))];
    changed();
}

export function appliances() {
    return [...names];
}

// The credentials for the job: one "user:password" for all the appliances,
// or none if no user name was typed.
export function credentials() {
    if (!bar || storedCredential()) return [];
    const user = bar.querySelector("[data-target-username]").value.trim();
    const password = bar.querySelector("[data-target-password]").value;
    return user ? [user + ":" + password] : [];
}

export function noCheckHostname() {
    if (storedCredential()) return false;
    return !!(bar && bar.querySelector("[data-target-no-check]").checked);
}

// Choose a stored credential by name (or none with null). It is chosen
// as soon as the credentials that fit the appliances offer it, or the
// user chooses another.
let wanted = null;
export function setStoredCredential(name) {
    wanted = name || "";
    if (bar) offerStored();
}

// The name of the stored credential that was chosen, or null
export function storedCredential() {
    if (!bar) return null;
    return bar.querySelector("[data-target-stored]").value || null;
}

function showAdhoc() {
    const stored = !!storedCredential();
    for (const box of bar.querySelectorAll("[data-target-adhoc]")) {
        box.classList.toggle("d-none", stored);
    }
}

// Offer the stored credentials that fit the appliances of the bar. A choice
// that no longer fits goes back to typing a user name and a password.
let offerCount = 0;
async function offerStored() {
    const box = bar.querySelector("[data-target-stored-box]");
    const select = bar.querySelector("[data-target-stored]");
    const count = ++offerCount;
    let found = [];
    if (names.length) {
        try {
            const query = names.map((n) => "appliances=" + encodeURIComponent(n)).join("&");
            found = (await api.get("/credentials?" + query)).credentials;
        } catch (error) {
            if (error.status === 403) {
                box.classList.add("d-none");
                return;
            }
            found = [];
        }
    }
    // An answer to an older list of appliances
    if (count !== offerCount) return;
    const before = wanted !== null ? wanted : select.value;
    if (wanted !== null && (wanted === "" || found.some((c) => c.name === wanted))) wanted = null;
    select.replaceChildren(el("option", {value: ""}, "Type a user name and password"),
        el("optgroup", {label: "Stored credentials"},
           ...found.map((c) => el("option", {value: c.name, selected: c.name === before,
                                             title: c.username + ", " + c.purpose},
                                  c.name + " (" + c.purpose + ")"))));
    box.classList.toggle("d-none", !found.length);
    if (select.value !== before) {
        showAdhoc();
        for (const listener of listeners) listener();
    }
    showAdhoc();
}

export function onChange(listener) {
    listeners.push(listener);
}

async function testConnectivity() {
    const status = bar.querySelector("[data-target-test-status]");
    const button = bar.querySelector("[data-target-test]");
    status.classList.remove("d-none", "text-danger");
    if (!names.length) {
        status.textContent = "Add the appliances first.";
        return;
    }
    const body = {appliances: names};
    const stored = storedCredential();
    if (stored) body.credential = stored;
    else {
        body.credentials = credentials();
        body.no_check_hostname = noCheckHostname();
    }
    button.disabled = true;
    status.textContent = "Testing " + names.length + " appliance(s)…";
    try {
        const answer = await api.post("/connectivity", body);
        tested = Object.fromEntries(answer.rows.map((r) => [r.appliance, r]));
        const failed = answer.rows.filter((r) => r.soma !== "yes" || r.ssh !== "yes").length;
        render();
        status.replaceChildren(failed ? failed + " of " + answer.rows.length + " could not log in to everything (hover for why). "
                                      : "All could log in. ",
                               el("a", {href: "/jobs/" + encodeURIComponent(answer.job_id)}, "The job"));
    } catch (error) {
        status.classList.add("text-danger");
        status.textContent = error.message;
    } finally {
        button.disabled = false;
    }
}

if (bar) {
    const form = bar.querySelector("[data-target-form]");
    bar.querySelector("[data-target-test]").addEventListener("click", testConnectivity);
    const input = bar.querySelector("[data-target-input]");
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const added = input.value.split(/[\s,]+/).filter(Boolean);
        input.value = "";
        setAppliances(names.concat(added));
    });
    for (const field of bar.querySelectorAll("[data-target-username], [data-target-password], [data-target-no-check]")) {
        field.addEventListener("change", () => {
            // A test of other credentials says nothing about these
            tested = {};
            render();
            for (const listener of listeners) listener();
        });
    }
    bar.querySelector("[data-target-stored]").addEventListener("change", () => {
        tested = {};
        render();
        wanted = null;
        showAdhoc();
        for (const listener of listeners) listener();
    });
    render();
    offerStored();
    suggest();
}

// The names of the registry, as suggestions for the input: the
// environments first, then the appliances
async function suggest() {
    const list = bar.querySelector("[data-target-names]");
    try {
        const environments = (await api.get("/environments")).environments;
        const appliances = (await api.get("/appliances")).appliances;
        list.replaceChildren(
            ...environments.map((e) => el("option", {value: e.name, label: "environment: " + e.members.join(" ")})),
            ...appliances.map((a) => el("option", {value: a.name, label: a.address})));
    } catch (error) {
        // Suggestions are a help; the input works without them
    }
}
