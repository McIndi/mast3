// Admin -> Users: list, add with a temporary password, choose the groups,
// disable and enable, a new temporary password, delete.
import {api, el, showError, when} from "./api.js";

const rows = document.querySelector("[data-user-rows]");
const errorBox = document.querySelector("[data-error]");
const notice = document.querySelector("[data-notice]");
const addForm = document.querySelector("[data-add-user]");
const GROUPS = [...document.querySelectorAll("#new-groups option")].map((o) => o.value);
let me = null;

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
    } catch (error) {
        showError(errorBox, error);
    }
    await refresh();
}

function state(user) {
    const parts = [];
    if (user.disabled) parts.push("disabled");
    if (user.locked) parts.push("locked");
    if (user.must_change) parts.push("must change password");
    return parts.join(", ");
}

function row(user) {
    const own = me && user.name === me.name;
    const groups = el("select", {className: "form-select form-select-sm", multiple: true, disabled: own,
                                 size: Math.min(GROUPS.length, 4), "aria-label": "Groups of " + user.name});
    for (const name of GROUPS) groups.append(el("option", {value: name, selected: user.groups.includes(name)}, name));
    const save = el("button", {type: "button", className: "btn btn-sm btn-outline-primary mt-1", disabled: own}, "Save groups");
    save.addEventListener("click", () => {
        const chosen = [...groups.selectedOptions].map((o) => o.value);
        act(api.patch("/users/" + encodeURIComponent(user.name), {groups: chosen}),
            user.name + " is now in " + (chosen.join(", ") || "no group") + ".");
    });

    const toggle = el("button", {type: "button", className: "btn btn-sm btn-outline-secondary", disabled: own},
                      user.disabled ? "Enable" : "Disable");
    toggle.addEventListener("click", () => act(
        api.patch("/users/" + encodeURIComponent(user.name), {disabled: !user.disabled}),
        (user.disabled ? "Enabled " : "Disabled ") + user.name + "."));

    // A new temporary password, typed into a password field (not a
    // prompt, which would show it)
    const reset = el("button", {type: "button", className: "btn btn-sm btn-outline-secondary"}, "New password");
    const passwordForm = el("form", {className: "d-none input-group input-group-sm mt-1"});
    const passwordInput = el("input", {type: "password", className: "form-control", autocomplete: "new-password",
                                       placeholder: "Temporary password", "aria-label": "Temporary password for " + user.name});
    passwordForm.append(passwordInput, el("button", {type: "submit", className: "btn btn-outline-primary"}, "Set"));
    reset.addEventListener("click", () => {
        passwordForm.classList.toggle("d-none");
        passwordInput.focus();
    });
    passwordForm.addEventListener("submit", (event) => {
        event.preventDefault();
        if (passwordInput.value) {
            act(api.patch("/users/" + encodeURIComponent(user.name), {password: passwordInput.value}),
                user.name + " has a new temporary password. They choose their own at the next login.");
        }
    });

    const remove = el("button", {type: "button", className: "btn btn-sm btn-outline-danger", disabled: own}, "Delete");
    remove.addEventListener("click", () => {
        if (window.confirm("Delete the user " + user.name + "? Their jobs stay.")) {
            act(api.del("/users/" + encodeURIComponent(user.name)), "Deleted " + user.name + ".");
        }
    });

    return el("tr", {},
        el("td", {}, user.name, own ? el("span", {className: "badge text-bg-info ms-1"}, "you") : null,
           user.kind === "service" ? el("span", {className: "badge text-bg-secondary ms-1"}, "service") : null),
        el("td", {}, groups, save),
        el("td", {className: "small"}, state(user)),
        el("td", {className: "small text-nowrap"}, when(user.last_login_at)),
        el("td", {}, el("div", {className: "d-flex flex-wrap gap-1"}, toggle, reset, remove), passwordForm));
}

async function refresh() {
    try {
        const answer = await api.get("/users");
        rows.replaceChildren(...answer.users.map(row));
    } catch (error) {
        showError(errorBox, error);
    }
}

addForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(addForm);
    const data = {name: form.get("name"), password: form.get("password"), groups: form.getAll("groups")};
    await act(api.post("/users", data), "Added " + data.name + ". They choose their own password at the first login.");
    if (errorBox.classList.contains("d-none")) addForm.reset();
});

(async () => {
    me = await api.get("/me");
    refresh();
})();
