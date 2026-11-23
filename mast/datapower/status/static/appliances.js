// The Appliances page: the last status of every appliance, refreshed every
// 30 seconds, with the appliances that have a problem first. A row opens to
// show its domains, the objects that are down and charts of the last 24
// hours. The bars are <meter> elements and the charts inline SVG, so the
// page needs no inline style.
import {api, el, showError} from "/static/js/api.js";

const rows = document.querySelector("[data-rows]");
const errorBox = document.querySelector("[data-error]");
const updated = document.querySelector("[data-updated]");
const pollingBox = document.querySelector("[data-polling]");
const pollingText = document.querySelector("[data-polling-text]");
const pollingForm = document.querySelector("[data-polling-form]");
const COLUMNS = 11;
const SVG = "http://www.w3.org/2000/svg";
// A status older than this is marked stale
const STALE_MINUTES = 20;
const opened = new Set();
let me = null;
let last = null;

function ago(iso, now) {
    if (!iso) return "never";
    const seconds = Math.max(0, (new Date(now) - new Date(iso)) / 1000);
    if (seconds < 90) return "just now";
    if (seconds < 3600) return Math.round(seconds / 60) + " min ago";
    if (seconds < 172800) return Math.round(seconds / 3600) + " h ago";
    return Math.round(seconds / 86400) + " days ago";
}

function minutesOld(iso, now) {
    return iso ? (new Date(now) - new Date(iso)) / 60000 : Infinity;
}

function bar(value, label) {
    if (value === null || value === undefined) return "—";
    const rounded = Math.round(value);
    return el("span", {className: "d-inline-flex align-items-center gap-1 text-nowrap"},
        el("meter", {min: 0, max: 100, low: 70, high: 90, optimum: 0, value: rounded,
                     className: "mast-meter", "aria-label": label + " " + rounded + " %"}),
        el("span", {className: "small"}, rounded + " %"));
}

function temporaryUsed(status) {
    if (!status || !status.fs_total_temporary_mb || status.fs_free_temporary_mb === null) return null;
    return 100 - 100 * status.fs_free_temporary_mb / status.fs_total_temporary_mb;
}

// Lower is shown first: unreachable, changed pin, problems, stale, fine,
// not polled
function rank(appliance, now) {
    const status = appliance.status;
    if (!appliance.poll && !status) return 6;
    if (!status) return 4;
    if (!status.ok || appliance.pin_changed) return 0;
    if (status.domains_down || status.objects_down) return 1;
    if (status.credential_can_write) return 2;
    if (minutesOld(status.collected_at, now) > STALE_MINUTES) return 3;
    return 5;
}

function warnings(appliance, now) {
    const found = [];
    const status = appliance.status;
    if (appliance.pin_changed) {
        found.push(el("span", {className: "badge text-bg-danger"}, "Certificate or host key changed"));
    }
    if (status && status.credential_can_write) {
        found.push(el("span", {className: "badge text-bg-warning",
            title: "The stored credential of the poll could change the appliance at its last test. Use a user that may only read."},
            "Credential can change it"));
    }
    if (status && status.statistics_off) {
        found.push(el("span", {className: "badge text-bg-secondary",
            title: "DataPower measures the CPU only while Statistics is enabled in the default domain."},
            "Statistics off: no CPU"));
    }
    if (status && minutesOld(status.collected_at, now) > STALE_MINUTES) {
        found.push(el("span", {className: "badge text-bg-secondary"}, "Not polled lately"));
    }
    if (!appliance.poll) {
        found.push(el("span", {className: "badge text-bg-light"}, "Not polled"));
    }
    return el("span", {className: "d-inline-flex flex-wrap gap-1"}, ...found);
}

function reachable(appliance, now) {
    const status = appliance.status;
    if (!status) {
        return el("span", {className: "small text-body-secondary"}, appliance.poll ? "Not polled yet" : "—");
    }
    const dot = el("span", {className: status.ok ? "text-success" : "text-danger",
                            "aria-hidden": "true"}, "●");
    const text = status.ok ? "Seen " + ago(status.collected_at, now)
        : "Not reachable, last seen " + ago(status.last_ok_at, now);
    const cell = el("span", {className: "small text-nowrap"}, dot, " ", text);
    if (!status.ok && status.error) cell.title = status.error;
    return cell;
}

function row(appliance, now) {
    const status = appliance.status || {};
    const toggle = el("button", {type: "button", className: "btn btn-sm btn-link p-0 text-start",
                                 "aria-expanded": String(opened.has(appliance.name))}, appliance.name);
    toggle.addEventListener("click", () => {
        if (opened.has(appliance.name)) opened.delete(appliance.name); else opened.add(appliance.name);
        draw();
    });
    const environments = appliance.environments.map((name) =>
        el("span", {className: "badge text-bg-light ms-1"}, name));
    const domains = status.domains_total === null || status.domains_total === undefined ? "—"
        : (status.domains_total - (status.domains_down || 0)) + "/" + status.domains_total;
    const objects = status.objects_down === null || status.objects_down === undefined ? "—"
        : el("span", {className: status.objects_down ? "fw-semibold text-danger" : ""}, String(status.objects_down));
    return el("tr", {},
        el("td", {}, toggle, ...environments),
        el("td", {}, reachable(appliance, now)),
        el("td", {className: "small"}, status.firmware || "—"),
        el("td", {}, bar(status.cpu_1m, "CPU")),
        el("td", {}, bar(status.mem_used_pct, "Memory")),
        el("td", {}, bar(temporaryUsed(appliance.status), "Temporary space used")),
        el("td", {}, domains),
        el("td", {}, objects),
        el("td", {}, warnings(appliance, now)),
        el("td", {className: "text-body-secondary"}, "—"),
        el("td", {className: "text-body-secondary"}, "—"));
}

function sparkline(points, key, label, max, now, hours) {
    const width = 160;
    const height = 36;
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("class", "mast-spark text-primary");
    svg.setAttribute("role", "img");
    const values = points.filter((p) => p[key] !== null && p[key] !== undefined);
    const top = max || Math.max(1, ...values.map((p) => p[key]));
    const start = new Date(now) - hours * 3600000;
    const x = (at) => Math.max(0, Math.min(width, (new Date(at) - start) / (hours * 3600000) * width));
    const y = (v) => height - 2 - (height - 4) * Math.min(v, top) / top;
    const line = document.createElementNS(SVG, "polyline");
    line.setAttribute("points", values.map((p) => x(p.at).toFixed(1) + "," + y(p[key]).toFixed(1)).join(" "));
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "currentColor");
    line.setAttribute("stroke-width", "1.5");
    // A faint baseline, and a dot at the latest value: a chart of a
    // single poll still shows something
    const base = document.createElementNS(SVG, "line");
    for (const [name, value] of [["x1", 0], ["x2", width], ["y1", height - 1], ["y2", height - 1],
                                 ["stroke", "currentColor"], ["stroke-opacity", "0.25"]]) {
        base.setAttribute(name, String(value));
    }
    svg.append(base, line);
    if (values.length) {
        const end = values[values.length - 1];
        const dot = document.createElementNS(SVG, "circle");
        dot.setAttribute("cx", x(end.at).toFixed(1));
        dot.setAttribute("cy", y(end[key]).toFixed(1));
        dot.setAttribute("r", "2.5");
        dot.setAttribute("fill", "currentColor");
        svg.append(dot);
    }
    const latest = values.length ? values[values.length - 1][key] : null;
    const text = latest === null ? "no data" : Math.round(latest) + (max ? " %" : "");
    svg.setAttribute("aria-label", label + ", last " + hours + " hours: " + text);
    return el("div", {className: "me-4 mb-2"},
        el("div", {className: "small text-body-secondary"}, label + ": " + text), svg);
}

async function detail(appliance, cell) {
    let answer;
    try {
        answer = await api.get("/appliances-status/" + encodeURIComponent(appliance.name) + "?hours=24");
    } catch (error) {
        cell.replaceChildren(el("span", {className: "text-danger small"}, error.message));
        return;
    }
    const status = answer.status;
    const parts = [];
    parts.push(el("div", {className: "d-flex flex-wrap"},
        sparkline(answer.points, "cpu", "CPU", 100, answer.now, answer.hours),
        sparkline(answer.points, "mem", "Memory", 100, answer.now, answer.hours),
        sparkline(answer.points, "objects_down", "Objects down", null, answer.now, answer.hours)));
    if (status) {
        const facts = [["Model", status.model], ["Serial", status.serial],
                       ["Release", status.delivery], ["Time on the appliance", status.appliance_time],
                       ["Up for", status.uptime_seconds === null ? null
                           : Math.floor(status.uptime_seconds / 86400) + " days " +
                             Math.floor(status.uptime_seconds % 86400 / 3600) + " h"]]
            .filter(([, value]) => value);
        if (status.credential) facts.push(["Polled with", status.credential]);
        parts.push(el("p", {className: "small mb-2"}, ...facts.flatMap(([name, value], i) =>
            [i ? " · " : "", el("span", {className: "text-body-secondary"}, name + ": "), String(value)])));
        if (!status.ok && status.error) {
            parts.push(el("p", {className: "small text-danger mb-2"}, "Last poll: " + status.error));
        }
        const domains = (status.detail.domains || []).map((d) =>
            el("span", {className: "badge me-1 " + (d.op_state === "up" ? "text-bg-success"
                : d.admin_state === "enabled" ? "text-bg-danger" : "text-bg-secondary")},
                d.name + (d.op_state === "up" ? "" : " (" + (d.admin_state === "enabled" ? d.op_state : "disabled") + ")")));
        parts.push(el("div", {className: "small mb-2"}, el("span", {className: "text-body-secondary"}, "Domains: "),
            ...(domains.length ? domains : ["—"])));
        const down = status.detail.objects_down || [];
        if (down.length) {
            const body = el("tbody", {});
            for (const object of down) {
                body.append(el("tr", {}, el("td", {}, object.domain || ""), el("td", {}, object.class || ""),
                    el("td", {}, object.name || ""),
                    el("td", {className: "d-none d-sm-table-cell"}, el("code", {}, object.event_code || ""))));
            }
            parts.push(el("table", {className: "table table-sm small mb-2"},
                el("thead", {}, el("tr", {}, el("th", {}, "Domain"), el("th", {}, "Class"),
                    el("th", {}, "Object"), el("th", {className: "d-none d-sm-table-cell"}, "Event code"))), body));
        } else {
            parts.push(el("p", {className: "small mb-2"}, "No object that is enabled is down."));
        }
    }
    const links = [el("a", {href: "/jobs?appliance=" + encodeURIComponent(appliance.name)}, "Jobs for this appliance")];
    if (me && me.role !== "viewer") {
        links.push(" · ", el("a", {href: "/commands?appliance=" + encodeURIComponent(appliance.name)}, "Run a command"));
    }
    parts.push(el("div", {className: "small"}, ...links));
    cell.replaceChildren(...parts);
}

function polling(answer) {
    const enabled = answer.polling.schedules.filter((s) => s.enabled);
    const polled = answer.appliances.filter((a) => a.poll).length;
    let text = "";
    pollingForm.classList.add("d-none");
    if (!answer.appliances.length) {
        text = "There are no appliances yet. An admin adds them in Admin → Appliances.";
    } else if (!polled) {
        text = "No appliance is polled. Turn on \"Collect status\" for them in Admin → Appliances.";
    } else if (!enabled.length) {
        if (answer.polling.may_manage) {
            text = "No schedule polls the appliances yet. Choose a stored credential for collecting " +
                   "(a DataPower user that may only read) to start polling:";
            showPollingForm();
        } else {
            text = "No schedule polls the appliances yet. An admin must start the polling, on this page.";
        }
    }
    pollingText.textContent = text;
    pollingBox.classList.toggle("d-none", !text);
}

async function showPollingForm() {
    pollingForm.classList.remove("d-none");
    const select = pollingForm.elements.credential;
    if (select.options.length) return;
    try {
        const found = (await api.get("/credentials")).credentials.filter((c) => c.purpose === "collect");
        if (!found.length) {
            pollingText.textContent = "No schedule polls the appliances yet, and there is no stored credential " +
                "for collecting. Add one in Admin → Credentials first.";
            pollingForm.classList.add("d-none");
            return;
        }
        for (const credential of found) {
            select.append(el("option", {value: credential.name}, credential.name));
        }
    } catch (error) {
        pollingText.textContent = error.message;
    }
}

pollingForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
        await api.post("/appliances-status/polling", {
            credential: pollingForm.elements.credential.value,
            minutes: parseInt(pollingForm.elements.minutes.value, 10)});
        showError(errorBox, null);
        refresh();
    } catch (error) {
        showError(errorBox, error);
    }
});

function draw() {
    if (!last) return;
    const now = last.now;
    const sorted = [...last.appliances].sort((a, b) => rank(a, now) - rank(b, now) || a.name.localeCompare(b.name));
    rows.replaceChildren();
    if (!sorted.length) {
        rows.append(el("tr", {}, el("td", {colSpan: COLUMNS, className: "text-body-secondary"}, "No appliances.")));
    }
    for (const appliance of sorted) {
        rows.append(row(appliance, now));
        if (opened.has(appliance.name)) {
            const cell = el("td", {colSpan: COLUMNS, className: "bg-body-tertiary mast-status-detail"},
                el("span", {className: "small text-body-secondary"}, "Loading…"));
            rows.append(el("tr", {}, cell));
            detail(appliance, cell);
        }
    }
}

async function refresh() {
    try {
        last = await api.get("/appliances-status");
        polling(last);
        draw();
        updated.textContent = "Updated " + new Date(last.now).toLocaleTimeString();
        showError(errorBox, null);
    } catch (error) {
        showError(errorBox, error);
    }
}

api.get("/me").then((answer) => { me = answer; }).catch(() => {}).finally(refresh);
setInterval(() => { if (!document.hidden) refresh(); }, 30000);
