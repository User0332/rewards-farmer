const values = new Map();
const listeners = new Map();
export function get(key) { return values.get(key); }
export function set(key, value) {
    values.set(key, value);
    for (const fn of [...(listeners.get(key) || [])]) fn(value);
    return value;
}
export function on(key, fn) {
    if (!listeners.has(key)) listeners.set(key, new Set());
    listeners.get(key).add(fn);
    return () => listeners.get(key)?.delete(fn);
}
