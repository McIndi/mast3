// Show the data of a result (mast.cli.Result.data): a table that can be
// sorted and filtered, or several parts. Every value is text.
import {el} from "./api.js";

function table(data) {
    const filter = el("input", {className: "form-control form-control-sm mb-2", type: "search",
                                placeholder: "Filter", "aria-label": "Filter the rows"});
    const tbody = el("tbody");
    const headers = data.columns.map((name, index) => {
        const button = el("button", {type: "button", className: "mast-sort"}, name);
        button.addEventListener("click", () => sortBy(index, button));
        return el("th", {scope: "col"}, button);
    });
    let rows = data.rows.slice();
    let sorted = {index: -1, ascending: true};

    function draw() {
        const needle = filter.value.trim().toLowerCase();
        tbody.replaceChildren();
        let shown = 0;
        for (const row of rows) {
            if (needle && !row.some((cell) => String(cell).toLowerCase().includes(needle))) continue;
            tbody.append(el("tr", {}, ...row.map((cell) => el("td", {className: "mast-cell"}, cell))));
            shown += 1;
        }
        count.textContent = shown === rows.length ? rows.length + " rows" : shown + " of " + rows.length + " rows";
    }

    function sortBy(index, button) {
        const ascending = sorted.index === index ? !sorted.ascending : true;
        sorted = {index: index, ascending: ascending};
        const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: "base"});
        rows = data.rows.slice().sort((a, b) => collator.compare(String(a[index] ?? ""), String(b[index] ?? "")) * (ascending ? 1 : -1));
        for (const header of headers) header.firstChild.removeAttribute("aria-sort");
        button.setAttribute("aria-sort", ascending ? "ascending" : "descending");
        draw();
    }

    const count = el("div", {className: "form-text"});
    filter.addEventListener("input", draw);
    const node = el("div", {className: "mb-3"}, filter,
        el("div", {className: "table-responsive"},
           el("table", {className: "table table-sm table-striped align-top small"},
              el("thead", {}, el("tr", {}, ...headers)), tbody)),
        count);
    draw();
    return node;
}

export function renderData(data) {
    if (!data) return el("div");
    if (data.kind === "table") return table(data);
    if (data.kind === "items") return el("div", {}, ...(data.items || []).map(renderData));
    if (data.kind === "text") return el("pre", {className: "mast-pre"}, data.text);
    return el("pre", {className: "mast-pre"}, JSON.stringify(data, null, 2));
}
