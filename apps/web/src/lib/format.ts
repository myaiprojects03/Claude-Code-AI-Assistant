const TZ = "Europe/London";

export const compact = (n: number) =>
  new Intl.NumberFormat("en-GB", { notation: n >= 10_000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(n);

export const pct = (x: number | null) => (x === null ? "–" : `${Math.round(x * 100)}%`);

export function dateTime(iso: string) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: TZ, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  }).format(new Date(iso));
}

export function shortDate(iso: string) {
  return new Intl.DateTimeFormat("en-GB", { timeZone: TZ, day: "numeric", month: "short" }).format(new Date(iso));
}

export function relative(iso: string, now = Date.now()) {
  const s = Math.round((new Date(iso).getTime() - now) / 1000);
  const abs = Math.abs(s);
  const rtf = new Intl.RelativeTimeFormat("en-GB", { numeric: "auto" });
  if (abs < 60) return rtf.format(s, "second");
  if (abs < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(s / 3600), "hour");
  return rtf.format(Math.round(s / 86400), "day");
}

const ACRONYMS: Record<string, string> = { it: "IT", m365: "M365", vcio: "vCIO", p1: "P1", crm: "CRM" };

export const humanise = (s: string | null | undefined) =>
  (s ?? "")
    .replace(/_/g, " ")
    .split(" ")
    .map((w) => ACRONYMS[w.toLowerCase()] ?? w)
    .join(" ")
    .replace(/^\w/, (c) => c.toUpperCase());

export const londonClock = (d: Date) =>
  new Intl.DateTimeFormat("en-GB", {
    timeZone: TZ, weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).format(d);
