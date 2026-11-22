// The colour theme, set before the page is drawn so that it does not flash:
// "auto" (the default) follows the system, "light" and "dark" are fixed.
// The choice is kept in localStorage, for this browser only.
(function () {
    var choice = "auto";
    try { choice = localStorage.getItem("mast.theme") || "auto"; } catch (e) { /* no storage */ }
    var dark = choice === "dark" || (choice === "auto" && window.matchMedia &&
        window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-bs-theme", dark ? "dark" : "light");
    document.documentElement.setAttribute("data-mast-theme", choice);
})();
