export function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
        if (value === null || value === undefined) continue;
        if (name === "class") node.className = value;
        else if (name === "text") node.textContent = value;
        else if (name.startsWith("on") && typeof value === "function") node.addEventListener(name.slice(2).toLowerCase(), value);
        else if (["checked", "disabled", "selected", "value", "type", "tabIndex"].includes(name)) node[name] = value;
        else if (value !== false || name.startsWith("aria-")) node.setAttribute(name, name.startsWith("aria-") ? String(value) : value === true ? "" : String(value));
    }
    append(node, children);
    return node;
}

export function append(node, children) {
    for (const child of [children].flat(Infinity)) {
        if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

export function clear(node) { node.replaceChildren(); return node; }
export function text(node, value) { node.textContent = value ?? ""; return node; }
export function svg(tag, attrs = {}, children = []) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
    append(node, children);
    return node;
}
