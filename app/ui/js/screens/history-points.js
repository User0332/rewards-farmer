export const TASK_ICONS = Object.freeze({ daily_set: "calendar-check", explore_on_bing: "compass", visual_search: "image", misc_cards: "cards", quests: "trophy", searches: "search", bonus: "gift" });

export function bandLabelFits(share, width, textWidth) {
    return Number.isFinite(share) && Number.isFinite(width) && Number.isFinite(textWidth) && width > 0 && textWidth > 0 && share * width >= textWidth + 12;
}

export function localDay(value) {
    const date = new Date(value);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function groupDays(runs) {
    const groups = new Map();
    for (const run of [...runs].sort((a, b) => new Date(b.started_at) - new Date(a.started_at))) {
        const day = localDay(run.started_at);
        if (!groups.has(day)) groups.set(day, []);
        groups.get(day).push(run);
    }
    return groups;
}

export function needsAttention(run) {
    return ["failed", "partial", "crashed", "config_error", "interrupted", "stopped", "unknown"].includes(run.result) || Boolean(run.warnings?.length) || (run.accounts || []).some(account =>
        Boolean(account.error) || account.decision?.reason === "unreadable" || Boolean((account.state_after || account.state_before)?.unreadable) || (account.task_counts?.fail || 0) > 0 || (account.tasks || []).some(task => task.status === "fail"));
}

export function taskShares(tasks) {
    const measured = new Map();
    for (const task of tasks.filter(task => Number.isFinite(task.points) && task.points > 0)) {
        const item = measured.get(task.task) || { task: task.task, name_key: task.name_key, points: 0 };
        item.points += task.points;
        measured.set(task.task, item);
    }
    const known = [...measured.values()];
    const total = known.reduce((sum, task) => sum + task.points, 0);
    return known.map(task => ({ task: task.task, name_key: task.name_key, points: task.points, share: task.points / total }));
}

export function taskGroups(accounts) {
    const tasks = accounts.flatMap(account => account.tasks);
    const withoutReadings = !tasks.some(task => Number.isFinite(task.points));
    const tile = task => ["ok", "running"].includes(task.status) && ((Number.isFinite(task.points) && task.points > 0) || (withoutReadings && task.status === "ok"));
    return {
        tiles: tasks.filter(tile),
        failed: tasks.filter(task => task.status === "fail"),
        noPoints: tasks.filter(task => task.status !== "fail" && !tile(task)),
    };
}
