// The target bar: the appliances that the next command talks to, and how to
// log in to them.
//
// Only the names of the appliances are kept, in sessionStorage. The user
// name and the password stay in the memory of this page: they are sent in
// the body of the POST that submits a job, and they are never stored in the
// browser.
import {el} from "./api.js";

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
    }
}

function changed() {
    save(names);
    render();
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
    if (!bar) return [];
    const user = bar.querySelector("[data-target-username]").value.trim();
    const password = bar.querySelector("[data-target-password]").value;
    return user ? [user + ":" + password] : [];
}

export function noCheckHostname() {
    return !!(bar && bar.querySelector("[data-target-no-check]").checked);
}

export function onChange(listener) {
    listeners.push(listener);
}

if (bar) {
    const form = bar.querySelector("[data-target-form]");
    const input = bar.querySelector("[data-target-input]");
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const added = input.value.split(/[\s,]+/).filter(Boolean);
        input.value = "";
        setAppliances(names.concat(added));
    });
    for (const field of bar.querySelectorAll("[data-target-username], [data-target-password], [data-target-no-check]")) {
        field.addEventListener("change", () => { for (const listener of listeners) listener(); });
    }
    render();
}
