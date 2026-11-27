// The Crypto page: the crypto inventory and its posture, in five views
// (Overview, Inventory, Findings, TLS probe, History). The view is in the hash of
// the address (#findings), so a link can open one. Every value is put in
// the page as text, never as HTML, and the charts are inline SVG, so the
// page needs no inline style.
import {api, el, showError, when} from "/static/js/api.js";

const errorBox = document.querySelector("[data-error]");
const updated = document.querySelector("[data-updated]");
const VIEWS = ["overview", "inventory", "findings", "probe", "history"];
const HYBRID = ["X25519MLKEM768", "SecP256r1MLKEM768", "SecP384r1MLKEM1024"];
const SVG = "http://www.w3.org/2000/svg";
const SEVERITY_BADGES = {
    critical: "text-bg-danger", high: "text-bg-warning", medium: "text-bg-primary",
    low: "text-bg-secondary", info: "text-bg-light",
};
const SEVERITIES = ["critical", "high", "medium", "low", "info"];
const loaded = {};
let rules = {};
let findingsAnswer = null;

function q(selector, root) { return (root || document).querySelector(selector); }

function badge(severity) {
    return el("span", {className: "badge " + (SEVERITY_BADGES[severity] || "text-bg-secondary")}, severity);
}

function day(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    return isNaN(date) ? iso : date.toLocaleDateString();
}

function daysFrom(iso, now) {
    return Math.floor((new Date(iso) - new Date(now)) / 86400000);
}

function inDays(iso, now) {
    const days = daysFrom(iso, now);
    if (days < 0) return "expired " + (-days) + " days ago";
    if (days === 0) return "today";
    return "in " + days + " days";
}

function plural(count, word) { return count + " " + word + (count === 1 ? "" : "s"); }

// The views
// =========

function show(view, push) {
    if (!VIEWS.includes(view)) view = "overview";
    for (const tab of document.querySelectorAll("[data-tab]")) {
        const active = tab.dataset.tab === view;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
    }
    for (const panel of document.querySelectorAll("[data-view]")) {
        panel.classList.toggle("d-none", panel.dataset.view !== view);
    }
    if (push && window.location.hash !== "#" + view) {
        history.replaceState(null, "", "#" + view);
    }
    if (!loaded[view]) {
        loaded[view] = true;
        LOADERS[view]();
    }
}

for (const tab of document.querySelectorAll("[data-tab]")) {
    tab.addEventListener("click", () => show(tab.dataset.tab, true));
}
window.addEventListener("hashchange", () => show(window.location.hash.slice(1), false));

function failed(error) {
    showError(errorBox, error);
}

// Overview
// ========

function counts(found) {
    if (!found) return "—";
    const parts = SEVERITIES.filter((s) => found[s]).map((s) =>
        el("span", {className: "badge me-1 " + SEVERITY_BADGES[s]}, found[s] + " " + s));
    if (found.excepted) {
        parts.push(el("span", {className: "small text-body-secondary"}, found.excepted + " accepted"));
    }
    return parts.length ? el("span", {}, ...parts) : el("span", {className: "small text-success"}, "none");
}

function scoreCell(posture) {
    if (!posture) return el("span", {className: "text-body-secondary"}, "—");
    const tone = posture.score >= 90 ? "text-success" : posture.score >= 60 ? "text-warning-emphasis" : "text-danger";
    return el("span", {className: "mast-score fw-semibold " + tone, title: "100 minus the weight of each finding that no exception covers"},
        String(posture.score));
}

function expiryCell(posture, now) {
    const expiry = posture && posture.nearest_expiry;
    if (!expiry) return el("span", {className: "text-body-secondary"}, "—");
    const days = daysFrom(expiry.not_after, now);
    const link = el("button", {type: "button", className: "btn btn-link btn-sm p-0 text-start" +
        (days < 30 ? " text-danger" : "")}, expiry.name);
    link.addEventListener("click", () => openObject(expiry.object_id));
    return el("span", {}, link, el("span", {className: "small text-body-secondary"},
        " " + inDays(expiry.not_after, now)));
}

function lastCell(appliance) {
    const last = appliance.last;
    if (!last) return el("span", {className: "small text-body-secondary"}, "Never read");
    const text = when(last.collected_at) + (last.firmware ? " · " + last.firmware : "");
    if (!last.ok) {
        return el("span", {className: "small text-danger", title: last.error || ""}, "Failed " + when(last.collected_at));
    }
    return el("span", {className: "small"}, text,
        last.problems ? el("span", {className: "text-warning-emphasis"}, " · " + plural(last.problems, "problem")) : "");
}

function timeline(expiring, now) {
    // One bar for what has expired, and one for each month from this one
    // on: 13 of them, so that a certificate that ends in 365 days has one
    const start = new Date(now);
    const buckets = [{label: "expired", count: 0, late: true}];
    for (let i = 0; i < 13; i += 1) {
        const month = new Date(start.getFullYear(), start.getMonth() + i, 1);
        buckets.push({label: month.toLocaleDateString(undefined, {month: "short"}), count: 0,
                      year: month.getFullYear(), month: month.getMonth()});
    }
    for (const item of expiring) {
        const at = new Date(item.not_after);
        if (at < start) { buckets[0].count += 1; continue; }
        const found = buckets.find((b) => b.year === at.getFullYear() && b.month === at.getMonth());
        if (found) found.count += 1;
    }
    const width = 700;
    const height = 140;
    const top = Math.max(1, ...buckets.map((b) => b.count));
    const step = width / buckets.length;
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("class", "mast-timeline");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Certificates by month of expiry: " +
        buckets.map((b) => b.label + " " + b.count).join(", "));
    buckets.forEach((bucket, index) => {
        const barHeight = (height - 34) * bucket.count / top;
        const x = index * step + 4;
        if (bucket.count) {
            const bar = document.createElementNS(SVG, "rect");
            for (const [name, value] of [["x", x], ["y", height - 18 - barHeight], ["width", step - 8],
                                         ["height", barHeight], ["rx", 2]]) {
                bar.setAttribute(name, String(value));
            }
            bar.setAttribute("class", bucket.late ? "mast-bar-late" : "mast-bar");
            svg.append(bar);
            const value = document.createElementNS(SVG, "text");
            value.setAttribute("x", String(x + (step - 8) / 2));
            value.setAttribute("y", String(height - 22 - barHeight));
            value.setAttribute("text-anchor", "middle");
            value.textContent = String(bucket.count);
            svg.append(value);
        }
        const label = document.createElementNS(SVG, "text");
        label.setAttribute("x", String(x + (step - 8) / 2));
        label.setAttribute("y", String(height - 4));
        label.setAttribute("text-anchor", "middle");
        label.textContent = bucket.label;
        svg.append(label);
    });
    return svg;
}

async function showRunForm(answer) {
    const box = q("[data-run]");
    const text = q("[data-run-text]");
    const form = q("[data-run-form]");
    const daily = q("[data-run-daily]");
    const enabled = answer.schedules.filter((s) => s.enabled);
    const parts = [];
    if (!answer.appliances.length) {
        parts.push("There are no appliances yet. An admin adds them in Admin → Targets.");
    } else if (enabled.length) {
        parts.push("The schedule " + enabled.map((s) => s.name + " (" + s.cron + ")").join(", ") +
                   " runs the inventory.");
    } else {
        parts.push("No schedule runs the inventory yet.");
    }
    if (!answer.may.run) {
        // A viewer only needs to know when nothing runs it
        if (!enabled.length && answer.appliances.length) parts.push("An operator can run it on this page.");
        text.textContent = parts.join(" ");
        box.classList.toggle("d-none", Boolean(enabled.length));
        return;
    }
    text.textContent = parts.join(" ");
    box.classList.remove("d-none");
    if (!answer.appliances.length) return;
    form.classList.remove("d-none");
    daily.classList.toggle("d-none", !(answer.may.schedule && !enabled.length));
    const select = form.elements.credential;
    if (select.options.length) return;
    try {
        const found = (await api.get("/credentials")).credentials.filter((c) => c.purpose === "collect");
        if (!found.length) {
            text.textContent = parts.join(" ") + " There is no stored credential for collecting: an admin adds one in Admin → Credentials.";
            form.classList.add("d-none");
            return;
        }
        const preferred = (enabled[0] || answer.schedules[0] || {}).credential;
        for (const credential of found) {
            select.append(el("option", {value: credential.name, selected: credential.name === preferred}, credential.name));
        }
    } catch (error) {
        text.textContent = error.message;
    }
}

q("[data-run-form]").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
        const answer = await api.post("/crypto/run", {credential: event.target.elements.credential.value});
        window.location.assign("/jobs/" + encodeURIComponent(answer.job_id));
    } catch (error) {
        failed(error);
    }
});

q("[data-run-daily]").addEventListener("click", async () => {
    try {
        await api.post("/crypto/schedule", {credential: q("[data-run-form]").elements.credential.value, hour: 2});
        showError(errorBox, null);
        loadOverview();
    } catch (error) {
        failed(error);
    }
});

// Export is offered once there is a snapshot that worked. The page starts
// with the state of the server; each overview brings it up to date.
function exportable(on) {
    q("[data-export-button]").disabled = !on;
    q("[data-export-why]").classList.toggle("d-none", on);
}

async function loadOverview() {
    let answer;
    try {
        answer = await api.get("/crypto/overview");
    } catch (error) {
        failed(error);
        return;
    }
    showError(errorBox, null);
    exportable(answer.exportable);
    updated.textContent = "Updated " + new Date(answer.now).toLocaleTimeString();
    const body = q("[data-posture]");
    body.replaceChildren();
    const sorted = [...answer.appliances].sort((a, b) =>
        ((a.posture ? a.posture.score : 101) - (b.posture ? b.posture.score : 101)) || a.name.localeCompare(b.name));
    if (!sorted.length) {
        body.append(el("tr", {}, el("td", {colSpan: 5, className: "text-body-secondary"}, "No appliances.")));
    }
    for (const appliance of sorted) {
        const name = el("button", {type: "button", className: "btn btn-link btn-sm p-0"}, appliance.name);
        name.addEventListener("click", () => {
            q("[data-f-appliance]").value = appliance.name;
            show("findings", true);
            if (findingsAnswer) drawFindings();
        });
        body.append(el("tr", {},
            el("td", {}, name, ...appliance.environments.map((e) => el("span", {className: "badge text-bg-light ms-1"}, e))),
            el("td", {}, scoreCell(appliance.posture)),
            el("td", {}, counts(appliance.posture && appliance.posture.counts)),
            el("td", {}, expiryCell(appliance.posture, answer.now)),
            el("td", {}, lastCell(appliance))));
    }
    q("[data-timeline]").replaceChildren(timeline(answer.expiring, answer.now));
    const soonest = q("[data-soonest]");
    soonest.replaceChildren();
    for (const item of answer.expiring.slice(0, 8)) {
        const link = el("button", {type: "button", className: "btn btn-link btn-sm p-0 text-start"}, item.name);
        link.addEventListener("click", () => openObject(item.object_id));
        soonest.append(el("li", {}, el("span", {className: daysFrom(item.not_after, answer.now) < 0 ? "text-danger" : ""},
            day(item.not_after)), " · ", item.appliance, " / ", item.domain, " / ", link,
            item.state === "disabled" ? el("span", {className: "text-body-secondary"}, " (disabled)") : ""));
    }
    if (!answer.expiring.length) {
        soonest.append(el("li", {className: "text-body-secondary"}, "None in the newest snapshots."));
    }
    const checks = answer.self_checks;
    const open = SEVERITIES.reduce((total, s) => total + (checks[s] || 0), 0);
    const line = q("[data-selfcheck]");
    if (!open) {
        line.replaceChildren(el("span", {className: "text-success"}, "Nothing to fix in how MAST is set up."),
            checks.excepted ? " (" + checks.excepted + " accepted)" : "");
    } else {
        const link = el("button", {type: "button", className: "btn btn-link btn-sm p-0"}, plural(open, "finding"));
        link.addEventListener("click", () => {
            q("[data-f-pack]").value = "mast";
            show("findings", true);
            if (findingsAnswer) drawFindings();
        });
        line.replaceChildren(link, " about how MAST is set up: the TLS of the appliances and the stored credentials.");
    }
    drawReadiness(answer.pqc || []);
    showRunForm(answer);
}

function readinessTone(ok, total) {
    if (!total) return "text-body-secondary";
    return ok === total ? "text-success" : ok ? "text-warning-emphasis" : "text-danger";
}

function drawReadiness(items) {
    const box = q("[data-pqc]");
    box.replaceChildren();
    if (!items.length) {
        box.append(el("p", {className: "small text-body-secondary mb-0"}, "No snapshot yet: run the inventory."));
        return;
    }
    const list = el("ul", {className: "list-unstyled small mb-0"});
    for (const item of items) {
        const name = el("button", {type: "button", className: "btn btn-link btn-sm p-0 fw-semibold"}, item.appliance);
        name.addEventListener("click", () => {
            q("[data-f-appliance]").value = item.appliance;
            q("[data-f-pack]").value = "pqc";
            show("findings", true);
            if (findingsAnswer) drawFindings();
        });
        const firmwareTone = item.support === "control" ? "text-success"
            : item.support === "none" ? "text-danger" : "text-warning-emphasis";
        const profiles = item.profiles;
        const peers = item.peers;
        let peerText = "not probed";
        let peerTone = "text-body-secondary";
        if (peers) {
            const tested = peers.probed - peers.unknown;
            peerText = tested ? peers.hybrid + " of " + plural(tested, "port") + " accept a hybrid group"
                : plural(peers.probed, "port") + " probed, groups not checked (needs Python 3.15)";
            peerTone = tested ? readinessTone(peers.hybrid, tested) : "text-body-secondary";
        }
        list.append(el("li", {className: "mb-2"}, name,
            el("div", {}, el("span", {className: "text-body-secondary"}, "Firmware: "),
               el("span", {className: firmwareTone}, (item.firmware || "") + " — " + item.support_text)),
            el("div", {}, el("span", {className: "text-body-secondary"}, "Profiles: "),
               profiles.total ? el("span", {className: readinessTone(profiles.hybrid, profiles.total)},
                   profiles.hybrid + " of " + plural(profiles.total, "profile") + " can negotiate a hybrid group" +
                   (profiles.down ? ", " + profiles.down + " down" : ""))
                   : el("span", {className: "text-body-secondary"}, "no server or client profile")),
            el("div", {}, el("span", {className: "text-body-secondary"}, "Peers: "),
               el("span", {className: peerTone}, peerText),
               peers && peers.at ? el("span", {className: "text-body-secondary"}, " (" + when(peers.at) + ")") : "")));
    }
    box.append(list);
}

// Inventory
// =========

let inventoryRows = [];

function drawInventory() {
    const needle = q("[data-inv-filter]").value.trim().toLowerCase();
    const kind = q("[data-inv-kind]").value;
    const appliance = q("[data-inv-appliance]").value;
    const body = q("[data-inv-rows]");
    body.replaceChildren();
    let shown = 0;
    const now = new Date().toISOString();
    for (const row of inventoryRows) {
        if (kind && row.kind !== kind) continue;
        if (appliance && row.appliance !== appliance) continue;
        if (needle && ![row.appliance, row.domain, row.class, row.name, row.detail, row.state]
            .some((v) => String(v || "").toLowerCase().includes(needle))) continue;
        shown += 1;
        if (shown > 2000) continue;
        let name = row.name;
        if (row.id) {
            name = el("button", {type: "button", className: "btn btn-link btn-sm p-0 text-start"}, row.name);
            name.addEventListener("click", () => openObject(row.id));
        }
        const used = row.used_by === null || row.used_by === undefined ? el("span", {}, "—")
            : row.used_by === 0 ? el("span", {className: "text-body-secondary"}, "nothing") : el("span", {}, String(row.used_by));
        if (row.users && row.users.length) used.title = row.users.join("\n");
        const expires = row.not_after ? el("span", {className: daysFrom(row.not_after, now) < 30 ? "text-danger" : ""},
            day(row.not_after)) : "";
        body.append(el("tr", {},
            el("td", {}, row.appliance || ""), el("td", {}, row.domain), el("td", {}, row.class),
            el("td", {className: "mast-cell"}, name),
            el("td", {}, row.state === "disabled" ? el("span", {className: "badge text-bg-secondary"}, "disabled") : (row.state || "")),
            el("td", {className: "mast-cell"}, row.detail || ""), el("td", {}, expires), el("td", {}, used),
            el("td", {}, row.findings ? el("span", {className: "badge text-bg-warning"}, String(row.findings)) : "")));
    }
    if (!inventoryRows.length) {
        body.append(el("tr", {}, el("td", {colSpan: 9, className: "text-body-secondary"},
            "No snapshot yet: run the inventory (Overview).")));
    }
    q("[data-inv-count]").textContent = (shown === inventoryRows.length ? plural(shown, "row")
        : shown + " of " + inventoryRows.length + " rows") + (shown > 2000 ? ", the first 2000 shown: filter to see the others" : "");
}

async function loadInventory() {
    try {
        inventoryRows = (await api.get("/crypto/inventory")).rows;
    } catch (error) {
        failed(error);
        return;
    }
    const select = q("[data-inv-appliance]");
    for (const name of [...new Set(inventoryRows.map((r) => r.appliance).filter(Boolean))].sort()) {
        select.append(el("option", {value: name}, name));
    }
    drawInventory();
}

for (const selector of ["[data-inv-filter]", "[data-inv-kind]", "[data-inv-appliance]"]) {
    q(selector).addEventListener("input", drawInventory);
}

// One object
// ==========

const objectDialog = q("[data-object-dialog]");

function objectLink(item) {
    const text = item.class + " " + item.name + (item.via ? " (" + item.via + ")" : "") +
        (item.state === "disabled" ? " — disabled" : "");
    if (!item.id) return el("span", {}, text);
    const link = el("button", {type: "button", className: "btn btn-link btn-sm p-0 text-start"}, text);
    link.addEventListener("click", () => openObject(item.id));
    return link;
}

function tree(nodes) {
    return el("ul", {className: "mast-tree small"}, ...nodes.map((node) =>
        el("li", {}, objectLink(node), node.children && node.children.length ? tree(node.children) : "")));
}

function facts(pairs) {
    return el("dl", {className: "row small mb-2"}, ...pairs.filter(([, v]) => v !== null && v !== undefined && v !== "")
        .flatMap(([name, value]) => [el("dt", {className: "col-sm-3 fw-normal text-body-secondary"}, name),
                                     el("dd", {className: "col-sm-9 mb-1 mast-cell"}, String(value))]));
}

function findingItem(finding) {
    return el("li", {className: "mb-1"}, badge(finding.severity), " ", el("strong", {}, finding.title), ": ",
        finding.message, finding.exception ? el("span", {className: "text-body-secondary"},
            " — accepted until " + day(finding.exception.expires_at) + ": " + finding.exception.reason) : "");
}

async function openObject(id) {
    q("[data-o-title]").textContent = "Loading…";
    const body = q("[data-o-body]");
    body.replaceChildren();
    if (!objectDialog.open) objectDialog.showModal();
    let item;
    try {
        item = await api.get("/crypto/objects/" + encodeURIComponent(id));
    } catch (error) {
        body.replaceChildren(el("p", {className: "text-danger"}, error.message));
        return;
    }
    q("[data-o-title]").textContent = item.class + " " + item.name;
    const parts = [facts([["Appliance", item.appliance], ["Domain", item.domain],
                          ["State", item.state + (item.running ? "" : ", not running") +
                                    (item.saved ? "" : ", not saved as it runs")],
                          ["Status", item.op_state ? item.op_state + (item.status_error ? ": " + item.status_error : "") : null],
                          ["Listens on", item.listen],
                          ["Snapshot", item.snapshot.id + " of " + when(item.snapshot.collected_at)],
                          ["Plaintext password", item.has_plaintext_password ? "yes (not kept by MAST)" : null]])];
    if (item.cert) {
        const cert = item.cert;
        parts.push(el("h3", {className: "h6"}, "Certificate"), facts([
            ["Subject", cert.subject], ["Issuer", cert.issuer + (cert.self_signed ? " (self-signed)" : "")],
            ["Valid", day(cert.not_before) + " to " + day(cert.not_after) + " (" + inDays(cert.not_after, new Date().toISOString()) + ")"],
            ["Key", [cert.key_alg, cert.key_bits ? cert.key_bits + " bits" : "", cert.curve].filter(Boolean).join(" ")],
            ["Signature", cert.sig_alg], ["Alternative names", (cert.sans || []).join(", ")],
            ["Serial", cert.serial], ["SHA-256", cert.sha256]]));
    }
    if (item.groups) {
        const groups = item.groups;
        parts.push(el("h3", {className: "h6"}, "Key exchange"), facts([
            ["TLS 1.3 groups", groups.down ? "none: the firmware takes this profile down"
                : groups.tls13.length ? groups.tls13.join(", ") : "none (TLS 1.3 is off)"],
            ["Hybrid PQC", groups.hybrid.length ? groups.hybrid.join(", ") : "no"],
            ["TLS 1.2", groups.tls12 ? "yes, classical groups" : "no"],
            ["Why", groups.reason]]));
    }
    if (item.findings.length) {
        parts.push(el("h3", {className: "h6"}, "Findings"), el("ul", {className: "list-unstyled small"}, ...item.findings.map(findingItem)));
    }
    parts.push(el("h3", {className: "h6"}, "Uses"), item.uses.length
        ? el("ul", {className: "list-unstyled small"}, ...item.uses.map((u) => el("li", {}, objectLink(u))))
        : el("p", {className: "small text-body-secondary"}, "Nothing."));
    parts.push(el("h3", {className: "h6"}, "Used by"), item.used_by.length
        ? el("ul", {className: "list-unstyled small"}, ...item.used_by.map((u) => el("li", {}, objectLink(u))))
        : el("p", {className: "small text-body-secondary"}, "Nothing in the configuration refers to it."));
    if (item.breaks) {
        parts.push(el("h3", {className: "h6"}, "What breaks if it expires"), item.breaks.length ? tree(item.breaks)
            : el("p", {className: "small text-body-secondary"}, "Nothing that the inventory sees."));
    }
    if (item.attrs && Object.keys(item.attrs).length) {
        parts.push(el("details", {className: "mt-2"}, el("summary", {className: "small"}, "Configuration (without secrets)"),
            el("pre", {className: "mast-pre mt-2"}, JSON.stringify(item.attrs, null, 2))));
    }
    body.replaceChildren(...parts);
}

for (const dialog of document.querySelectorAll("dialog")) {
    q("[data-close]", dialog).addEventListener("click", () => dialog.close());
}

// Findings
// ========

function drawFindings() {
    const answer = findingsAnswer;
    if (!answer) return;
    const severity = q("[data-f-severity]").value;
    const appliance = q("[data-f-appliance]").value;
    const pack = q("[data-f-pack]").value;
    const excepted = q("[data-f-excepted]").checked;
    const groups = new Map();
    for (const finding of answer.findings) {
        if (severity && finding.severity !== severity) continue;
        if (appliance && finding.appliance !== appliance) continue;
        if (pack && finding.pack !== pack) continue;
        if (!excepted && finding.exception) continue;
        if (!groups.has(finding.rule_id)) groups.set(finding.rule_id, []);
        groups.get(finding.rule_id).push(finding);
    }
    const order = (rule) => SEVERITIES.indexOf((rules[rule] || {}).severity);
    const box = q("[data-f-groups]");
    box.replaceChildren();
    if (!groups.size) {
        box.append(el("p", {className: "text-success"}, answer.findings.length ? "No finding with these filters."
            : "No findings."));
    }
    for (const rule of [...groups.keys()].sort((a, b) => order(a) - order(b) || a.localeCompare(b))) {
        const items = groups.get(rule);
        const info = rules[rule] || {title: rule, severity: items[0].severity, doc: ""};
        const body = el("tbody", {});
        for (const finding of items) {
            let subject = finding.subject;
            if (finding.object_id) {
                subject = el("button", {type: "button", className: "btn btn-link btn-sm p-0 text-start"}, finding.subject);
                subject.addEventListener("click", () => openObject(finding.object_id));
            }
            const actions = [];
            if (answer.may_manage && !finding.exception) {
                const add = el("button", {type: "button", className: "btn btn-sm btn-outline-secondary"}, "Add exception");
                add.addEventListener("click", () => openException(finding));
                actions.push(add);
            }
            body.append(el("tr", {className: finding.exception ? "text-body-secondary" : ""},
                el("td", {}, badge(finding.severity)), el("td", {}, finding.appliance || "—"),
                el("td", {className: "mast-cell mast-subject"}, subject),
                el("td", {className: "mast-cell"}, finding.message,
                   finding.exception ? el("div", {className: "small"}, "Accepted until " + day(finding.exception.expires_at) +
                       " by " + finding.exception.created_by + ": " + finding.exception.reason) : ""),
                el("td", {}, ...actions)));
        }
        const remediation = items.find((f) => !f.exception) || items[0];
        box.append(el("details", {className: "mast-group mb-3", open: true},
            el("summary", {}, badge(info.severity), " ", el("strong", {}, info.title), " ",
               el("span", {className: "text-body-secondary small"}, "(" + rule + ", " + plural(items.length, "finding") + ")")),
            el("p", {className: "small text-body-secondary mb-1 mt-1"}, info.doc),
            el("p", {className: "small mb-1"}, el("span", {className: "fw-semibold"}, "Fix: "), remediation.remediation),
            el("div", {className: "table-responsive"}, el("table", {className: "table table-sm small align-top mb-0"},
                el("thead", {}, el("tr", {}, el("th", {}, "Severity"), el("th", {}, "Appliance"), el("th", {}, "Object"),
                                   el("th", {}, "Finding"), el("th", {}, ""))), body))));
    }
}

async function loadExceptions() {
    const body = q("[data-exceptions]");
    let answer;
    try {
        answer = await api.get("/crypto/exceptions");
    } catch (error) {
        failed(error);
        return;
    }
    body.replaceChildren();
    if (!answer.exceptions.length) {
        body.append(el("tr", {}, el("td", {colSpan: 8, className: "text-body-secondary"}, "None.")));
    }
    for (const item of answer.exceptions) {
        const cells = [el("td", {}, item.rule_id), el("td", {}, item.appliance || "every"), el("td", {}, item.domain || "every"),
            el("td", {}, el("code", {}, item.object_pattern)),
            el("td", {}, item.expired ? el("span", {className: "badge text-bg-secondary"}, "expired " + day(item.expires_at)) : day(item.expires_at)),
            el("td", {className: "mast-cell"}, item.reason), el("td", {}, item.created_by)];
        const actions = el("td", {});
        if (answer.may_manage) {
            const remove = el("button", {type: "button", className: "btn btn-sm btn-outline-danger"}, "Remove");
            remove.addEventListener("click", async () => {
                if (!window.confirm("Remove this exception? Its findings will count again.")) return;
                try {
                    await api.del("/crypto/exceptions/" + item.id);
                    await refreshFindings();
                } catch (error) {
                    failed(error);
                }
            });
            actions.append(remove);
        }
        body.append(el("tr", {}, ...cells, actions));
    }
}

async function refreshFindings() {
    try {
        findingsAnswer = await api.get("/crypto/findings");
    } catch (error) {
        failed(error);
        return;
    }
    rules = Object.fromEntries(findingsAnswer.rules.map((r) => [r.id, r]));
    drawFindings();
    loadExceptions();
}

async function loadFindings() {
    const severity = q("[data-f-severity]");
    for (const name of SEVERITIES) severity.append(el("option", {value: name}, name));
    await refreshFindings();
    if (!findingsAnswer) return;
    const appliance = q("[data-f-appliance]");
    const chosen = appliance.value;
    for (const name of findingsAnswer.appliances) {
        appliance.append(el("option", {value: name, selected: name === chosen}, name));
    }
    const ruleSelect = q("#e-rule");
    for (const item of findingsAnswer.rules) ruleSelect.append(el("option", {value: item.id}, item.id + " — " + item.title));
    const exceptionAppliance = q("#e-appliance");
    for (const name of findingsAnswer.appliances) exceptionAppliance.append(el("option", {value: name}, name));
}

for (const selector of ["[data-f-severity]", "[data-f-appliance]", "[data-f-pack]", "[data-f-excepted]"]) {
    q(selector).addEventListener("input", drawFindings);
}

const exceptionDialog = q("[data-exception-dialog]");
const exceptionForm = q("[data-exception-form]");

function openException(finding) {
    showError(q("[data-e-error]"), null);
    exceptionForm.reset();
    exceptionForm.elements.rule_id.value = finding.rule_id;
    exceptionForm.elements.appliance.value = finding.appliance || "";
    exceptionForm.elements.domain.value = finding.domain || "";
    exceptionForm.elements.object_pattern.value = finding.name || finding.subject;
    const soon = new Date(Date.now() + 30 * 86400000);
    exceptionForm.elements.expires.value = soon.toISOString().slice(0, 10);
    exceptionForm.elements.expires.min = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
    exceptionDialog.showModal();
}

exceptionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(exceptionForm).entries());
    try {
        await api.post("/crypto/exceptions", values);
        exceptionDialog.close();
        await refreshFindings();
        loaded.overview = false;
    } catch (error) {
        showError(q("[data-e-error]"), error);
    }
});

// TLS probe
// =========

function offer(row, name, label, bad) {
    // bad: accepting it is the problem (TLS 1.0, 1.1)
    const value = row.offers[name];
    const note = row.notes[name] || "";
    if (value === true) {
        return el("span", {className: "badge me-1 " + (bad ? "text-bg-danger" : "text-bg-success"),
                           title: bad ? "accepted" : note}, label || name);
    }
    if (value === false) return el("span", {className: "badge text-bg-light me-1 text-decoration-line-through", title: note}, label || name);
    return el("span", {className: "badge text-bg-secondary me-1", title: note || "not tried"}, (label || name) + " ?");
}

function targetText(target) {
    if (target.kind === "listener") return target.class + " " + target.name + " (" + target.domain + ")";
    return target.name || target.kind || "";
}

async function loadProbe() {
    let answer;
    try {
        answer = await api.get("/crypto/probes");
    } catch (error) {
        failed(error);
        return;
    }
    const enabled = answer.schedules.filter((s) => s.enabled);
    const box = q("[data-p-run]");
    const parts = [enabled.length ? "The schedule " + enabled.map((s) => s.name + " (" + s.cron + ")").join(", ") + " runs the probe."
        : "No schedule runs the probe yet."];
    if (!answer.may.run && !enabled.length) parts.push("An operator can run it on this page.");
    q("[data-p-run-text]").textContent = parts.join(" ");
    box.classList.toggle("d-none", !answer.may.run && Boolean(enabled.length));
    q("[data-p-actions]").classList.toggle("d-none", !answer.may.run);
    q("[data-p-weekly]").classList.toggle("d-none", !(answer.may.schedule && !enabled.length));
    const body = q("[data-p-rows]");
    body.replaceChildren();
    if (!answer.results.length) {
        body.append(el("tr", {}, el("td", {colSpan: 9, className: "text-body-secondary"}, "No probe yet.")));
    }
    for (const row of answer.results) {
        const negotiated = row.ok ? el("span", {}, [row.protocol, row.group].filter(Boolean).join(" · "),
                                       el("div", {className: "text-body-secondary"}, row.cipher || ""))
            : el("span", {className: "text-danger"}, row.error || "no TLS");
        const leaf = row.chain[0];
        const cert = leaf ? (leaf.subject || leaf.sha256.slice(0, 16)) + " · " +
            [leaf.key_alg, leaf.key_bits].filter(Boolean).join(" ") : "";
        const job = row.job_id ? el("a", {href: "/jobs/" + encodeURIComponent(row.job_id)}, when(row.probed_at)) : when(row.probed_at);
        const reached = row.ok || Object.values(row.offers).some((v) => v === true);
        body.append(el("tr", {},
            el("td", {}, row.appliance || "—"), el("td", {}, row.host + ":" + row.port),
            el("td", {className: "mast-cell"}, el("div", {className: "mast-probe-wide"}, targetText(row.target))),
            el("td", {className: "mast-cell"}, el("div", {className: "mast-probe-wide"}, negotiated)),
            el("td", {}, reached ? el("span", {}, ...HYBRID.map((g) => offer(row, g)), offer(row, "MLKEM768")) : ""),
            el("td", {}, reached ? offer(row, "classical", "classical") : ""),
            el("td", {}, reached ? el("span", {}, offer(row, "TLSv1.0", "1.0", true), offer(row, "TLSv1.1", "1.1", true)) : ""),
            el("td", {className: "mast-cell"}, cert), el("td", {}, job)));
    }
}

q("[data-p-now]").addEventListener("click", async () => {
    try {
        const answer = await api.post("/crypto/probe", {});
        window.location.assign("/jobs/" + encodeURIComponent(answer.job_id));
    } catch (error) {
        failed(error);
    }
});

q("[data-p-weekly]").addEventListener("click", async () => {
    try {
        await api.post("/crypto/probe-schedule", {});
        showError(errorBox, null);
        loadProbe();
    } catch (error) {
        failed(error);
    }
});

// History
// =======

const choice = {from: null, to: null};

async function loadSnapshots() {
    const name = q("[data-h-appliance]").value;
    const body = q("[data-h-rows]");
    choice.from = choice.to = null;
    q("[data-h-compare]").disabled = true;
    q("[data-h-diff]").replaceChildren();
    body.replaceChildren();
    if (!name) return;
    let answer;
    try {
        answer = await api.get("/crypto/snapshots?appliance=" + encodeURIComponent(name));
    } catch (error) {
        failed(error);
        return;
    }
    if (!answer.snapshots.length) {
        body.append(el("tr", {}, el("td", {colSpan: 9, className: "text-body-secondary"}, "No snapshot yet.")));
    }
    answer.snapshots.forEach((snapshot, index) => {
        const radio = (side) => {
            const input = el("input", {type: "radio", name: "h-" + side, className: "form-check-input",
                                       disabled: !snapshot.ok, "aria-label": side + " snapshot " + snapshot.id});
            if (snapshot.ok && ((side === "to" && index === 0) || (side === "from" && index === 1))) {
                input.checked = true;
                choice[side] = snapshot.id;
            }
            input.addEventListener("change", () => {
                choice[side] = snapshot.id;
                q("[data-h-compare]").disabled = !(choice.from && choice.to && choice.from !== choice.to);
            });
            return input;
        };
        const findings = SEVERITIES.filter((s) => snapshot.findings[s]).map((s) =>
            el("span", {className: "badge me-1 " + SEVERITY_BADGES[s]}, snapshot.findings[s] + " " + s));
        const job = snapshot.job_id ? el("a", {href: "/jobs/" + encodeURIComponent(snapshot.job_id)}, String(snapshot.id)) : String(snapshot.id);
        body.append(el("tr", {},
            el("td", {}, radio("from")), el("td", {}, radio("to")), el("td", {}, job),
            el("td", {}, when(snapshot.collected_at)),
            el("td", {}, snapshot.ok ? "yes" : el("span", {className: "text-danger", title: snapshot.error || ""}, "no")),
            el("td", {}, snapshot.firmware || ""), el("td", {}, String(snapshot.objects)),
            el("td", {}, String(snapshot.certificates)), el("td", {}, ...findings)));
    });
    q("[data-h-compare]").disabled = !(choice.from && choice.to && choice.from !== choice.to);
}

function list(title, items, text) {
    return el("div", {className: "mb-2"}, el("h3", {className: "h6 mb-1"}, title + " (" + items.length + ")"),
        items.length ? el("ul", {className: "small mb-0"}, ...items.map((item) => el("li", {}, text(item))))
            : el("p", {className: "small text-body-secondary mb-0"}, "None."));
}

q("[data-h-compare]").addEventListener("click", async () => {
    // From the older to the newer, whatever was chosen in which column
    const [first, second] = [choice.from, choice.to].sort((a, b) => a - b);
    let answer;
    try {
        answer = await api.get("/crypto/diff?from=" + first + "&to=" + second);
    } catch (error) {
        failed(error);
        return;
    }
    const key = (item) => item.domain + " / " + item.class + " " + item.name;
    q("[data-h-diff]").replaceChildren(
        el("h2", {className: "h6 mt-3"}, "From snapshot " + answer.from.id + " (" + when(answer.from.collected_at) +
           ") to " + answer.to.id + " (" + when(answer.to.collected_at) + ")"),
        list("Objects added", answer.added, key), list("Objects removed", answer.removed, key),
        list("Objects changed", answer.changed, (item) => key(item) + ": " + item.changes.join(", ")),
        list("Findings opened", answer.opened, (item) => el("span", {}, badge(item.severity), " ", item.rule_id, " · ",
            item.subject)),
        list("Findings closed", answer.closed, (item) => el("span", {}, badge(item.severity), " ", item.rule_id, " · ",
            item.subject)));
});

async function loadHistory() {
    let answer;
    try {
        answer = await api.get("/crypto/overview");
    } catch (error) {
        failed(error);
        return;
    }
    const select = q("[data-h-appliance]");
    for (const appliance of answer.appliances) select.append(el("option", {value: appliance.name}, appliance.name));
    select.addEventListener("input", loadSnapshots);
    loadSnapshots();
}

const LOADERS = {overview: loadOverview, inventory: loadInventory, findings: loadFindings, probe: loadProbe,
                 history: loadHistory};

show(window.location.hash.slice(1) || "overview", false);
