// The Terminal page: the DataPower CLI of the appliances of the target bar,
// one pane each. A line goes to every ticked pane. The output is put in the
// page as text (textContent), never as HTML. Passwords never stay in the
// page: a line typed at a password prompt is sent and forgotten, and the
// command history keeps only the other lines.
import {api, el, showError} from "/static/js/api.js";
import * as targets from "/static/js/targets.js";

const errorBox = document.querySelector("[data-error]");
const q = (selector, root) => (root || document).querySelector(selector);
const POLL_MS = 700;

let session = null;
const panes = new Map();
const history = [];
let historyIndex = 0;
let running = 0;
let active = null;

function failed(error) { showError(errorBox, error); }

function setState(text) { q("[data-state]").textContent = text; }

// The panes
// =========

function append(pane, text) {
    if (!text) return;
    const pre = pane.output;
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
    pre.textContent += (pre.textContent && !pre.textContent.endsWith("\n") ? "\n" : "") + text;
    if (atBottom) pre.scrollTop = pre.scrollHeight;
}

function paneState(pane) {
    const info = pane.info;
    pane.badge.className = "badge " + (pane.busy ? "text-bg-primary" : info.connected ? "text-bg-success" : "text-bg-danger");
    pane.badge.textContent = pane.busy ? "running" : info.connected ? "connected" : "not connected";
    pane.card.classList.toggle("mast-term-off", !info.connected);
    pane.reconnect.classList.toggle("d-none", info.connected || pane.busy);
    pane.check.disabled = !info.connected;
    if (!info.connected) pane.check.checked = false;
}

function showPane(name) {
    // At phone width one pane at a time, chosen with the tabs
    active = name;
    for (const [other, pane] of panes) {
        pane.column.classList.toggle("d-none", other !== name);
        pane.column.classList.add("d-lg-block");
        pane.tab.classList.toggle("active", other === name);
        pane.tab.setAttribute("aria-selected", String(other === name));
    }
}

function addPane(info, transcript) {
    const check = el("input", {type: "checkbox", className: "form-check-input", checked: info.connected,
                               "aria-label": "Send the next line to " + info.name});
    const badge = el("span", {className: "badge"});
    const reconnect = el("button", {type: "button", className: "btn btn-sm btn-outline-secondary py-0 d-none"}, "Reconnect");
    const download = el("a", {className: "btn btn-sm btn-outline-secondary py-0",
                              href: "/api/v1/ssh/sessions/" + encodeURIComponent(session.id) +
                                    "/transcript?appliance=" + encodeURIComponent(info.name)}, "Download transcript");
    const output = el("pre", {className: "mast-term-output font-monospace", tabIndex: 0,
                              "aria-label": "Output of " + info.name, "aria-live": "polite"});
    const card = el("div", {className: "card mast-term-pane"},
        el("div", {className: "card-header d-flex flex-wrap align-items-center gap-2"},
           el("label", {className: "form-check-label d-flex align-items-center gap-2 fw-semibold me-auto"}, check, info.name),
           badge, reconnect, download),
        output);
    const column = el("div", {className: "col-12 col-lg-6"}, card);
    const tab = el("button", {type: "button", className: "nav-link", role: "tab"}, info.name);
    // shown: the numbers of the lines whose answer is in the pane
    const pane = {info, check, badge, reconnect, output, card, column, tab, busy: false, shown: new Set()};
    tab.addEventListener("click", () => showPane(info.name));
    reconnect.addEventListener("click", () => doReconnect(pane));
    panes.set(info.name, pane);
    q("[data-panes]").append(column);
    q("[data-tabs]").append(el("li", {className: "nav-item", role: "presentation"}, tab));
    if (transcript) append(pane, transcript);
    if (info.error) append(pane, "[" + info.error + "]");
    for (const warning of info.warnings || []) append(pane, "[" + warning + "]");
    paneState(pane);
}

function passwordMode() {
    // A ticked appliance that asks for a password: the next line is one
    const asking = [...panes.values()].some((p) => p.check.checked && p.info.password_prompt);
    const input = q("[data-line]");
    input.type = asking ? "password" : "text";
    q("[data-line-help]").textContent = asking
        ? "An appliance asks for a password: what you type is sent, not shown, and kept nowhere."
        : "Enter sends the line to the ticked appliances. Up and Down go through the lines sent. Ctrl-C stops a line that is still running.";
    return asking;
}

function showSession(answer, transcripts) {
    session = answer;
    panes.clear();
    q("[data-panes]").replaceChildren();
    q("[data-tabs]").replaceChildren();
    for (const info of answer.appliances) addPane(info, transcripts ? transcripts[info.name] : info.banner);
    q("[data-start]").classList.add("d-none");
    q("[data-session]").classList.remove("d-none");
    q("[data-session-text]").replaceChildren("Session of " + answer.appliances.length + " appliance(s), recorded as the job ",
        el("a", {href: "/jobs/" + encodeURIComponent(answer.job_id)}, answer.job_id.slice(0, 8)), ".");
    q("[data-download-all]").href = "/api/v1/ssh/sessions/" + encodeURIComponent(answer.id) + "/transcript";
    showPane(answer.appliances[0].name);
    passwordMode();
    setState("Connected to " + answer.appliances.filter((a) => a.connected).length + " of " + answer.appliances.length);
    q("[data-line]").focus();
}

function ended(text) {
    session = null;
    panes.clear();
    q("[data-session]").classList.add("d-none");
    q("[data-start]").classList.remove("d-none");
    setState(text);
    loadSessions();
}

// Opening, attaching, closing
// ===========================

async function loadSessions() {
    let answer;
    try {
        answer = await api.get("/ssh/sessions");
    } catch (error) {
        failed(error);
        return;
    }
    const select = q("[data-attach]");
    select.replaceChildren(el("option", {value: ""}, "Attach to an open session…"),
        ...answer.sessions.map((s) => el("option", {value: s.id},
            s.appliances.map((a) => a.name).join(", ") + " (" + new Date(s.opened_at).toLocaleTimeString() + ")")));
    select.classList.toggle("d-none", !answer.sessions.length);
    // A reload attaches to the session of this tab again, if it is open
    const mine = sessionStorage.getItem("mast.terminal");
    if (!session && mine && answer.sessions.some((s) => s.id === mine)) {
        attach(mine);
    }
}

async function attach(id) {
    try {
        const answer = await api.get("/ssh/sessions/" + encodeURIComponent(id));
        sessionStorage.setItem("mast.terminal", answer.id);
        showError(errorBox, null);
        showSession(answer, Object.fromEntries(answer.appliances.map((a) => [a.name, a.transcript])));
    } catch (error) {
        failed(error);
    }
}

q("[data-attach]").addEventListener("change", (event) => {
    if (event.target.value) attach(event.target.value);
});

q("[data-open-form]").addEventListener("submit", async (event) => {
    event.preventDefault();
    const names = targets.appliances();
    if (!names.length) {
        failed(new Error("Add the appliances to the target bar first."));
        return;
    }
    const body = {appliances: names, domain: event.target.elements.domain.value};
    const stored = targets.storedCredential();
    if (stored) body.credential = stored;
    else body.credentials = targets.credentials();
    const button = q("[data-open]");
    button.disabled = true;
    setState("Connecting to " + names.length + " appliance(s)…");
    try {
        const answer = await api.post("/ssh/sessions", body);
        sessionStorage.setItem("mast.terminal", answer.id);
        showError(errorBox, null);
        showSession(answer, null);
    } catch (error) {
        setState("");
        failed(error);
    } finally {
        button.disabled = false;
    }
});

q("[data-disconnect]").addEventListener("click", async () => {
    if (!session) return;
    const job = session.job_id;
    try {
        await api.del("/ssh/sessions/" + encodeURIComponent(session.id));
    } catch (error) {
        if (error.status !== 404) {
            failed(error);
            return;
        }
    }
    sessionStorage.removeItem("mast.terminal");
    ended("Closed. The job " + job.slice(0, 8) + " has the transcript.");
});

async function doReconnect(pane) {
    const body = {appliance: pane.info.name, credentials: targets.credentials(),
                  domain: q("#t-domain").value};
    pane.reconnect.disabled = true;
    try {
        const answer = await api.post("/ssh/sessions/" + encodeURIComponent(session.id) + "/reconnect", body);
        pane.info = answer;
        append(pane, answer.error ? "[" + answer.error + "]" : answer.banner);
        pane.check.checked = answer.connected;
        paneState(pane);
        passwordMode();
    } catch (error) {
        if (error.status === 404) ended("The session was closed.");
        else failed(error);
    } finally {
        pane.reconnect.disabled = false;
    }
}

// Lines
// =====

function showResults(line) {
    for (const [name, result] of Object.entries(line.results)) {
        const pane = panes.get(name);
        if (!pane || !result.done || pane.shown.has(line.number)) continue;
        pane.shown.add(line.number);
        append(pane, result.output);
        if (result.error) append(pane, "[" + result.error + "]");
        pane.busy = false;
        if (result.connected !== undefined) pane.info.connected = result.connected;
        pane.info.password_prompt = !!result.password_prompt;
        if (!pane.info.connected && !pane.info.error) pane.info.error = result.error || "not connected";
        paneState(pane);
    }
}

async function follow(line) {
    // A 202: ask again until every appliance has answered
    while (session && !line.finished) {
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        if (!session) return;
        try {
            line = await api.get("/ssh/sessions/" + encodeURIComponent(session.id) + "/lines/" + line.number);
        } catch (error) {
            if (error.status === 404) ended("The session was closed (idle, too old, or cancelled).");
            else failed(error);
            return;
        }
        showResults(line);
    }
}

q("[data-line-form]").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!session) return;
    const input = q("[data-line]");
    const text = input.value;
    const chosen = [...panes.values()].filter((p) => p.check.checked && p.info.connected);
    if (!chosen.length) {
        failed(new Error("Tick at least one connected appliance."));
        return;
    }
    const secret = passwordMode();
    if (!secret && text.trim() && history[history.length - 1] !== text) history.push(text);
    historyIndex = history.length;
    input.value = "";
    showError(errorBox, null);
    for (const pane of chosen) {
        pane.busy = true;
        paneState(pane);
    }
    running += 1;
    try {
        const line = await api.post("/ssh/sessions/" + encodeURIComponent(session.id) + "/lines",
                                    {line: text, appliances: chosen.map((p) => p.info.name)});
        showResults(line);
        await follow(line);
    } catch (error) {
        if (error.status === 404) ended("The session was closed (idle, too old, or cancelled).");
        else failed(error);
        for (const pane of chosen) { pane.busy = false; if (panes.size) paneState(pane); }
    } finally {
        running -= 1;
        if (session) passwordMode();
    }
});

async function interrupt() {
    if (!session) return;
    const names = [...panes.values()].filter((p) => p.busy).map((p) => p.info.name);
    if (!names.length) return;
    try {
        await api.post("/ssh/sessions/" + encodeURIComponent(session.id) + "/interrupt", {appliances: names});
    } catch (error) {
        failed(error);
    }
}

q("[data-interrupt]").addEventListener("click", interrupt);

q("[data-line]").addEventListener("keydown", (event) => {
    const input = event.target;
    if (event.ctrlKey && (event.key === "c" || event.key === "C") && input.selectionStart === input.selectionEnd
            && [...panes.values()].some((p) => p.busy)) {
        event.preventDefault();
        interrupt();
    } else if (event.key === "ArrowUp" && input.type === "text" && history.length) {
        event.preventDefault();
        historyIndex = Math.max(0, historyIndex - 1);
        input.value = history[historyIndex];
    } else if (event.key === "ArrowDown" && input.type === "text" && history.length) {
        event.preventDefault();
        historyIndex = Math.min(history.length, historyIndex + 1);
        input.value = historyIndex < history.length ? history[historyIndex] : "";
    }
});

document.addEventListener("change", (event) => {
    if (event.target.closest && event.target.closest(".mast-term-pane")) passwordMode();
});

loadSessions();
