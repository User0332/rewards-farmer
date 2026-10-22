// Only the shell/reload mock entrypoint injects this Location replacement.
export const location = { reload() { window.__rfReloaded = (window.__rfReloaded || 0) + 1; } };
