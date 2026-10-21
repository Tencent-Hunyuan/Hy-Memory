import { type GuiLocale, translate } from "@one-memory/contracts/gui-i18n";

/**
 * One clock format for every user-facing timestamp.
 *
 * Three surfaces grew their own copy of this: `CallHistory` (07), where the
 * "今天 HH:MM" wording was settled against the source, `SelectedMemoryDetail`
 * (06E), which agreed on today but differed on older dates, and `HealthPanel`
 * (运行诊断), which had no today branch at all and printed a full
 * `toLocaleString()` — so the same moment read as "今天 14:31" on one page and
 * "2026/9/20 14:31:07" on another.
 *
 * Absolute seconds are not information the user asked for; they were an
 * artefact of the default formatter. The source (139:7984) writes "今天 02:10".
 *
 * Unparseable input returns 时间未知 rather than a fabricated date. That string
 * is already asserted across several suites, so it stays exactly as-is.
 */
export function clockStamp(
  value: string | null | undefined,
  locale: GuiLocale = "zh-CN",
): string {
  if (!value || !Number.isFinite(Date.parse(value)))
    return translate(locale, "clock.unknown");
  const date = new Date(value);
  const now = new Date();
  const clock = new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
  if (
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  )
    return translate(locale, "clock.today", { clock });
  return new Intl.DateTimeFormat(locale, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
