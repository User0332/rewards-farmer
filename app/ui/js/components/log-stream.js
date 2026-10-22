const LIMIT = 512 * 1024;
const HIGH_WATER = 640 * 1024;
const starts = new WeakMap();

function validatePage(page, offset) {
    if (!page || typeof page.text !== "string" || !Number.isSafeInteger(page.next_offset) || page.next_offset < offset) throw new Error("Invalid log page offsets");
}

export async function appendLogPage(prior, page, runId, call) {
    validatePage(page, prior.next_offset);
    const start = starts.get(prior) ?? Math.max(0, prior.next_offset - LIMIT);
    let next = { text: prior.text + page.text, next_offset: page.next_offset };
    if (page.next_offset - start > HIGH_WATER) {
        const offset = page.next_offset - LIMIT;
        const tail = await call("get_run_log", { run_id: runId, offset, max_bytes: LIMIT });
        validatePage(tail, offset);
        next = { text: tail.text, next_offset: tail.next_offset };
        starts.set(next, offset);
    } else starts.set(next, start);
    return next;
}

export async function joinLog(prior, data, call) {
    if (!Number.isSafeInteger(data.offset) || !Number.isSafeInteger(data.next_offset) || data.offset < 0 || data.next_offset < data.offset || typeof data.text !== "string") throw new Error("Invalid log push offsets");
    if (data.next_offset <= prior.next_offset) return prior;
    const page = data.offset === prior.next_offset ? data : await call("get_run_log", { run_id: data.run_id, offset: prior.next_offset, max_bytes: data.next_offset - prior.next_offset });
    return appendLogPage(prior, page, data.run_id, call);
}
