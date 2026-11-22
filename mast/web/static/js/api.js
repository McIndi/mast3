// Calls to the JSON API of the web UI (/api/v1). Every call that is not a
// GET sends the CSRF token of the session in the X-CSRF-Token header.

export const CSRF = document.querySelector('meta[name="csrf-token"]').content;

export class ApiError extends Error {
    constructor(status, body) {
        super((body && body.error) || ("The server answered " + status + "."));
        this.status = status;
        this.fields = (body && body.fields) || {};
    }
}

async function call(method, path, body, isForm) {
    const options = {method: method, headers: {"Accept": "application/json"},
                     credentials: "same-origin"};
    if (method !== "GET") {
        options.headers["X-CSRF-Token"] = CSRF;
    }
    if (body !== undefined) {
        if (isForm) {
            options.body = body;
        } else {
            options.headers["Content-Type"] = "application/json";
            options.body = JSON.stringify(body);
        }
    }
    const response = await fetch("/api/v1" + path, options);
    if (response.status === 401) {
        window.location.assign("/login?next=" + encodeURIComponent(window.location.pathname));
        throw new ApiError(401, {error: "Log in first."});
    }
    if (response.status === 204) {
        return null;
    }
    let data = null;
    try { data = await response.json(); } catch (e) { data = null; }
    if (!response.ok) {
        throw new ApiError(response.status, data);
    }
    return data;
}

export const api = {
    get: (path) => call("GET", path),
    post: (path, body) => call("POST", path, body === undefined ? {} : body),
    patch: (path, body) => call("PATCH", path, body),
    del: (path) => call("DELETE", path),
    upload: (file) => {
        const form = new FormData();
        form.append("file", file);
        return call("POST", "/uploads", form, true);
    },
};

// Make an element: el("td", {className: "x"}, "text", child, ...). Strings
// become text nodes, never HTML.
export function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
        if (key === "dataset") {
            Object.assign(node.dataset, value);
        } else if (key.startsWith("aria-") || key === "role" || key === "for") {
            node.setAttribute(key, value);
        } else {
            node[key] = value;
        }
    }
    for (const child of children) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child instanceof Node ? child : String(child));
    }
    return node;
}

export function showError(box, error) {
    if (!box) return;
    if (!error) {
        box.classList.add("d-none");
        box.textContent = "";
        return;
    }
    box.textContent = error.message || String(error);
    box.classList.remove("d-none");
}

export function when(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    return isNaN(date) ? iso : date.toLocaleString();
}

const BADGES = {
    queued: "text-bg-secondary", running: "text-bg-primary",
    succeeded: "text-bg-success", partial: "text-bg-warning",
    failed: "text-bg-danger", cancelled: "text-bg-dark", lost: "text-bg-danger",
};

export function statusBadge(status) {
    return el("span", {className: "badge " + (BADGES[status] || "text-bg-secondary")}, status);
}
