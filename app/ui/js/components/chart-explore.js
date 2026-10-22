import { el, svg } from "../dom.js";

let chartIdentity = 0;

export function enhanceChart(root, series, mode, services) {
    const plot = root.querySelector(".chart-plot");
    const image = plot?.querySelector(".chart-svg");
    if (!image) return root;
    const t = services.i18n.t;
    const rtl = document.documentElement.dir === "rtl";
    const id = `chart-area-${++chartIdentity}`;
    root.classList.add("chart-explore");
    image.prepend(svg("defs", {}, svg("linearGradient", { id, x1: "0", x2: "0", y1: "0", y2: "1" }, [
        svg("stop", { offset: "0%", class: "chart-fill-top" }),
        svg("stop", { offset: "100%", class: "chart-fill-bottom" }),
    ])));
    const paths = [...image.querySelectorAll(".chart-line")];
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.reducedMotion === "true";
    let pending = 0;
    let resolveAnimation;
    root.chartAnimationDone = new Promise((resolve) => { resolveAnimation = resolve; });
    const finishers = [];
    for (const path of paths) {
        const vertices = [...path.getAttribute("d").matchAll(/[ML]([\d.]+),([\d.]+)/g)].map((match) => [Number(match[1]), Number(match[2])]);
        if (vertices.length < 2) continue;
        path.before(svg("path", { class: "chart-area", fill: `url(#${id})`, d: `${path.getAttribute("d")} L${vertices.at(-1)[0]},138 L${vertices[0][0]},138 Z` }));
        if (reduced) { path.dataset.drawComplete = "true"; continue; }
        pending++;
        let finished = false;
        const finish = () => {
            if (finished) return;
            finished = true;
            path.removeEventListener("animationend", finish);
            path.removeEventListener("animationcancel", finish);
            path.classList.remove("chart-draw");
            path.dataset.drawComplete = "true";
            if (--pending === 0) { root.dataset.drawComplete = "true"; resolveAnimation({ event: "animationend", paths: paths.length }); }
        };
        path.addEventListener("animationend", finish);
        path.addEventListener("animationcancel", finish);
        path.classList.add("chart-draw");
        finishers.push(finish);
    }
    if (!pending) { root.dataset.drawComplete = "true"; resolveAnimation({ event: reduced ? "reduced-motion" : "no-line-animation", paths: paths.length }); }
    root._dispose = () => finishers.forEach((finish) => finish());
    const guide = svg("line", { class: "chart-crosshair", x1: 0, x2: 0, y1: 18, y2: 138 });
    const marker = svg("circle", { class: "chart-active-point", r: 4.5, cx: 0, cy: 0 });
    const surface = svg("rect", { class: "chart-hit", x: 52, y: 18, width: 816, height: 124, fill: "transparent" });
    image.append(guide, marker, surface);
    const instructions = el("span", { class: "sr-only", id: `${id}-help`, text: t("stats.chart.help") });
    const date = el("span", { class: "chart-readout-date" });
    const value = el("strong", { class: "chart-readout-value" });
    const readout = el("output", { class: "chart-readout", id: `${id}-readout`, "aria-live": "polite" }, [date, value]);
    image.setAttribute("tabindex", "0");
    image.setAttribute("role", "group");
    image.setAttribute("aria-describedby", `${id}-help ${id}-readout`);
    root.append(readout, instructions);
    const hoverDate = svg("text", { x: 10, y: 17, class: "chart-hover-date" });
    const hoverValue = svg("text", { x: 10, y: 37, class: "chart-hover-value" });
    const hoverCard = svg("svg", { class: "chart-hover-card", x: 0, y: 4, width: 156, height: 48, "aria-hidden": "true" }, [
        svg("rect", { x: 0, y: 0, width: 156, height: 48, rx: 4, class: "chart-hover-bg" }), hoverDate, hoverValue,
    ]);
    plot.querySelector(".chart-labels").append(hoverCard);
    const coordinates = new Map();
    const indexAt = (x) => Math.max(0, Math.min(series.length - 1, Math.round((rtl ? 1 - (x - 52) / 794 : (x - 74) / 794) * Math.max(1, series.length - 1))));
    for (const path of paths) for (const match of path.getAttribute("d").matchAll(/[ML]([\d.]+),([\d.]+)/g)) coordinates.set(indexAt(Number(match[1])), [Number(match[1]), Number(match[2])]);
    for (const circle of image.querySelectorAll(".chart-point")) coordinates.set(indexAt(Number(circle.getAttribute("cx"))), [Number(circle.getAttribute("cx")), Number(circle.getAttribute("cy"))]);
    for (const bar of image.querySelectorAll(".chart-day-bar")) coordinates.set(Number(bar.dataset.index), [Number(bar.getAttribute("x")) + Number(bar.getAttribute("width")) / 2, Number(bar.getAttribute("y"))]);
    let selected = Math.max(0, series.findLastIndex((point) => typeof point[mode] === "number"));
    const show = (index) => {
        selected = Math.max(0, Math.min(series.length - 1, index));
        const point = series[selected];
        const position = coordinates.get(selected);
        const x = (rtl ? 52 : 74) + (rtl ? 1 - selected / Math.max(1, series.length - 1) : selected / Math.max(1, series.length - 1)) * 794;
        guide.setAttribute("x1", x); guide.setAttribute("x2", x);
        marker.classList.toggle("is-gap", !position);
        if (position) { marker.setAttribute("cx", position[0]); marker.setAttribute("cy", position[1]); }
        for (const bar of image.querySelectorAll(".chart-day-bar")) bar.classList.toggle("is-active", Number(bar.dataset.index) === selected);
        date.textContent = services.i18n.formatDate(point.date);
        value.textContent = point[mode] === null || point[mode] === undefined ? t("common.value.unknown") : services.i18n.formatNumber(point[mode]);
        hoverDate.textContent = date.textContent; hoverValue.textContent = value.textContent;
        const width = plot.getBoundingClientRect().width;
        hoverCard.setAttribute("x", Math.max(8, Math.min(width - 164, x / 920 * width + (rtl ? 10 : -166))));
        root.dataset.activeDate = point.date; root.dataset.activeValue = String(point[mode]);
    };
    show(selected);
    surface.addEventListener("pointermove", (event) => {
        root.classList.add("is-exploring");
        const box = image.getBoundingClientRect();
        show(indexAt((event.clientX - box.left) / box.width * 920));
    });
    for (const bar of image.querySelectorAll(".chart-day-bar")) bar.addEventListener("pointerenter", () => { root.classList.add("is-exploring"); show(Number(bar.dataset.index)); });
    image.addEventListener("keydown", (event) => {
        let next = selected;
        if (event.key === "ArrowRight") next += rtl ? -1 : 1;
        else if (event.key === "ArrowLeft") next += rtl ? 1 : -1;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = series.length - 1;
        else return;
        event.preventDefault(); root.classList.add("is-exploring"); show(next);
    });
    image.addEventListener("focus", () => { root.classList.add("is-exploring"); show(selected); });
    image.addEventListener("blur", () => root.classList.remove("is-exploring"));
    surface.addEventListener("pointerleave", () => root.classList.remove("is-exploring"));
    return root;
}
