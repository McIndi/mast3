// The Jobs page: the newest jobs, with filters, refreshed every few
// seconds; and the workers.
import {api, el, showError, statusBadge, when} from "./api.js";

const rows = document.querySelector("[data-job-rows]");
const filters = document.querySelector("[data-job-filters]");
const errorBox = document.querySelector("[data-error]");
const more = document.querySelector("[data-more]");
const workersBox = document.querySelector("[data-workers]");
const hiddenBox = document.querySelector("[data-hidden-scheduled]");
const scheduleSelect = filters.querySelector("[data-schedule-filter]");
const PAGE = 50;
let limit = PAGE;

function query() {
    const form = new FormData(filters);
    const params = new URLSearchParams();
    for (const [name, value] of form.entries()) {
        if (value && name !== "schedule") params.append(name, value);
    }
    // The jobs of schedules (a status poll each minute) would fill the
    // page, so it shows by default only the jobs that people submitted.
    // Schedule names cannot start with "*".
    const schedule = form.get("schedule") || "";
    if (schedule === "") params.set("scheduled", "no");
    else if (schedule === "*scheduled") params.set("scheduled", "yes");
    else if (schedule !== "*any") params.set("schedule", schedule);
    params.set("limit", String(limit));
    return params;
}

async function refresh() {
    try {
        const answer = await api.get("/jobs?" + query().toString());
        rows.replaceChildren();
        if (!answer.jobs.length) {
            rows.append(el("tr", {}, el("td", {colSpan: 6, className: "text-body-secondary"}, "No jobs.")));
        }
        for (const job of answer.jobs) {
            const link = el("a", {href: "/jobs/" + job.id}, el("code", {}, job.id.slice(0, 8)));
            rows.append(el("tr", {},
                el("td", {}, link, job.parent_id ? el("span", {className: "badge text-bg-light ms-1"}, "child") : null),
                el("td", {}, statusBadge(job.status), job.cancel_requested && !job.finished
                    ? el("span", {className: "small ms-1"}, "cancelling") : null),
                el("td", {}, job.command),
                el("td", {className: "small"}, job.targets.join(" ")),
                el("td", {className: "small"}, job.submitted_by),
                el("td", {className: "small text-nowrap"}, when(job.created_at))));
        }
        more.classList.toggle("d-none", answer.jobs.length < limit);
        const hidden = answer.hidden_scheduled || 0;
        hiddenBox.classList.toggle("d-none", !hidden);
        hiddenBox.querySelector("[data-hidden-count]").textContent = hidden === 1
            ? "1 job of a schedule is not shown."
            : hidden.toLocaleString() + " jobs of schedules are not shown.";
        showError(errorBox, null);
    } catch (error) {
        showError(errorBox, error);
    }
}

async function refreshWorkers() {
    try {
        const answer = await api.get("/workers");
        workersBox.replaceChildren();
        if (!answer.workers.length) {
            workersBox.append("No worker is running. The web server does the jobs itself.");
            return;
        }
        const list = el("ul", {className: "list-unstyled mb-0"});
        for (const worker of answer.workers) {
            list.append(el("li", {}, el("code", {}, worker.id), " on ", worker.hostname,
                ", ", worker.concurrency, " at a time, last seen ", when(worker.last_seen_at)));
        }
        workersBox.append(list);
    } catch (error) {
        workersBox.textContent = error.message;
    }
}

// The schedules, for the filter. ?schedule=NAME (the Jobs link of
// Admin -> Schedules) chooses one.
async function loadSchedules() {
    const select = scheduleSelect;
    const wanted = new URLSearchParams(window.location.search).get("schedule") || "";
    try {
        const found = (await api.get("/schedules")).schedules;
        for (const schedule of found) {
            select.append(el("option", {value: schedule.name, selected: schedule.name === wanted},
                "Schedule " + schedule.name));
        }
    } catch (error) {
        // The filter is a help; the page works without it
    }
    if (!select.querySelector("option[value='" + CSS.escape(wanted) + "']") && wanted) {
        select.append(el("option", {value: wanted, selected: true}, "Schedule " + wanted));
    }
}

// ?appliance=NAME (the Jobs link of the Appliances page), ?command=... and
// the other filters of the form start with those values
for (const [name, value] of new URLSearchParams(window.location.search)) {
    const field = filters.elements.namedItem(name);
    if (field && field.tagName === "INPUT" && field.type !== "checkbox") field.value = value;
}

filters.addEventListener("change", () => { limit = PAGE; refresh(); });
filters.addEventListener("submit", (event) => { event.preventDefault(); refresh(); });
more.addEventListener("click", () => { limit += PAGE; refresh(); });
hiddenBox.querySelector("[data-show-scheduled]").addEventListener("click", () => {
    scheduleSelect.value = "*any";
    limit = PAGE;
    refresh();
});
loadSchedules().then(refresh);
refreshWorkers();
setInterval(() => { if (!document.hidden) refresh(); }, 3000);
setInterval(() => { if (!document.hidden) refreshWorkers(); }, 15000);
