// What every page of the web UI does: the theme menu.
const ORDER = ["auto", "light", "dark"];

function apply(choice) {
    const dark = choice === "dark" || (choice === "auto" &&
        window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-bs-theme", dark ? "dark" : "light");
    document.documentElement.setAttribute("data-mast-theme", choice);
    for (const name of document.querySelectorAll("[data-theme-name]")) {
        name.textContent = choice;
    }
}

function current() {
    return document.documentElement.getAttribute("data-mast-theme") || "auto";
}

apply(current());
for (const button of document.querySelectorAll("[data-theme-cycle]")) {
    button.addEventListener("click", (event) => {
        event.stopPropagation();
        const next = ORDER[(ORDER.indexOf(current()) + 1) % ORDER.length];
        try { localStorage.setItem("mast.theme", next); } catch (e) { /* no storage */ }
        apply(next);
    });
}
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => apply(current()));
