// One job: its parameters, its progress (polled with events?after=N), its
// result, its child jobs and its downloads. This is also where an
// inventory is watched while it runs.
import {api, el, showError, statusBadge, when} from "./api.js";
import {renderData} from "./datatable.js";

const root = document.querySelector("[data-job-id]");
const jobId = root.dataset.jobId;
const $ = (name) => root.querySelector("[data-" + name + "]");
let lastEvent = 0;
let me = null;
let shownResult = false;

async function events() {
    const answer = await api.get("/jobs/" + jobId + "/events?after=" + lastEvent);
    const box = $("events");
    for (const event of answer.events) {
        const line = event.kind === "say" ? event.message : "[" + event.message + "]";
        box.append(el("span", {className: event.kind === "say" ? "" : "text-body-secondary"},
                      when(event.created_at) + "  " + line + "\n"));
    }
    if (answer.events.length) box.scrollTop = box.scrollHeight;
    lastEvent = answer.last_event;
}

function summary(job) {
    const box = $("summary");
    box.replaceChildren();
    const items = [["Command", job.command], ["Submitted", when(job.created_at) + " by " + job.submitted_by],
                   ["Started", when(job.started_at)], ["Finished", when(job.finished_at)],
                   ["Appliances", job.targets.join(" ")], ["Worker", job.worker_id || ""]];
    if (job.parent_id) items.push(["Parent", el("a", {href: "/jobs/" + job.parent_id}, job.parent_id.slice(0, 8))]);
    if (job.error) items.push(["Error", job.error]);
    for (const [name, value] of items) {
        box.append(el("dt", {className: "col-sm-3 col-lg-2"}, name), el("dd", {className: "col-sm-9 col-lg-10 mb-1"}, value));
    }
}

function params(job) {
    const body = $("params");
    body.replaceChildren();
    for (const [name, value] of Object.entries(job.params)) {
        const text = Array.isArray(value) ? value.join("\n") : (value === null ? "" : String(value));
        body.append(el("tr", {}, el("th", {className: "fw-normal text-body-secondary"}, name.replace(/_/g, " ")),
                        el("td", {className: "mast-cell"}, text)));
    }
}

function children(job) {
    $("children-box").classList.toggle("d-none", !job.children.length);
    const list = $("children");
    list.replaceChildren();
    const done = job.children.filter((child) => child.finished).length;
    if (job.children.length) {
        const bar = el("div", {className: "progress-bar"});
        bar.style.width = Math.round(100 * done / job.children.length) + "%";
        list.append(el("li", {className: "list-group-item px-0"},
            el("div", {className: "progress", role: "progressbar", "aria-label": "Child jobs done",
                       "aria-valuenow": String(done), "aria-valuemin": "0",
                       "aria-valuemax": String(job.children.length)}, bar),
            el("div", {className: "form-text"}, done + " of " + job.children.length + " done")));
    }
    for (const child of job.children) {
        list.append(el("li", {className: "list-group-item px-0 d-flex gap-2 align-items-center"},
            statusBadge(child.status), el("a", {href: "/jobs/" + child.id}, el("code", {}, child.id.slice(0, 8))),
            el("span", {className: "text-truncate"}, child.targets.join(" "))));
    }
}

function files(job) {
    $("files-box").classList.toggle("d-none", !job.files.length);
    const list = $("files");
    list.replaceChildren();
    for (const file of job.files) {
        list.append(el("li", {}, el("a", {href: "/api/v1/jobs/" + jobId + "/files/" + encodeURIComponent(file.name)}, file.name)));
    }
}

async function result() {
    const answer = await api.get("/jobs/" + jobId + "/result");
    const box = $("result");
    box.replaceChildren();
    if (answer.data) {
        box.append(renderData(answer.data));
    } else if (answer.html) {
        // The html of a result is made on the server, which escapes every
        // value from an appliance (mast.cli.Table.html)
        const html = el("div", {className: "mast-result"});
        html.innerHTML = answer.html;
        box.append(html);
    } else {
        box.append(el("pre", {className: "mast-pre"}, answer.text || "No result."));
    }
    $("history").href = "/api/v1/jobs/" + jobId + "/history";
    $("result-box").classList.remove("d-none");
}

function mayCancel(job) {
    if (!me || job.finished) return false;
    return me.may["cancel-any"] || (me.may["cancel-own"] && job.submitted_by === me.name);
}

async function refresh() {
    try {
        const job = await api.get("/jobs/" + jobId);
        const status = $("status");
        const badge = statusBadge(job.status);
        status.className = badge.className;
        status.textContent = job.status;
        summary(job);
        params(job);
        children(job);
        files(job);
        $("cancel").classList.toggle("d-none", !mayCancel(job));
        await events();
        if (job.finished && !shownResult && job.status !== "cancelled" && job.status !== "lost") {
            try { await result(); } catch (e) { /* no result */ }
            shownResult = true;
        }
        showError($("error"), null);
        return job;
    } catch (error) {
        showError($("error"), error);
        return null;
    }
}

$("cancel").addEventListener("click", async () => {
    try {
        await api.post("/jobs/" + jobId + "/cancel");
        await refresh();
    } catch (error) {
        showError($("error"), error);
    }
});

async function follow() {
    me = await api.get("/me");
    let job = await refresh();
    while (!job || !job.finished || job.children.some((child) => !child.finished)) {
        await new Promise((resolve) => setTimeout(resolve, document.hidden ? 5000 : 1000));
        job = await refresh();
    }
}
follow();
