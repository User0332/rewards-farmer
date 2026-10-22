const calendarDay = (date) => Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());

export function scheduleTime(schedule, generatedAt, i18n, formatLocale) {
    if (schedule.mode === "manual") return i18n.t("overview.schedule.manual_next");
    if (!schedule.next_at) return i18n.t("overview.schedule.no_time");
    const next = new Date(schedule.next_at);
    const today = new Date(generatedAt);
    const days = (calendarDay(next) - calendarDay(today)) / 86400000;
    const prefix = schedule.next_is_check ? "overview.check_around" : "overview.run_at";
    const time = i18n.formatTime(schedule.next_at);
    if (days === 0 || days === 1) return i18n.t(`${prefix}_${days === 0 ? "today" : "tomorrow"}`, { time });
    const date = next.getFullYear() === today.getFullYear()
        ? new Intl.DateTimeFormat(formatLocale, { month: "short", day: "numeric" }).format(next)
        : i18n.formatDate(schedule.next_at);
    return i18n.t(prefix, { date, time });
}
