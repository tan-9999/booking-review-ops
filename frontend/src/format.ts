// Date ranges and plain-language formatting helpers.

export function iso(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

export function addDays(d: Date, days: number): Date {
  const copy = new Date(d);
  copy.setDate(copy.getDate() + days);
  return copy;
}

export function mondayOf(d: Date): Date {
  return addDays(d, -((d.getDay() + 6) % 7));
}

export type Preset = "week" | "4w" | "3m" | "12m" | "custom";

export const PRESETS: { key: Preset; label: string }[] = [
  { key: "week", label: "This week" },
  { key: "4w", label: "Last 4 weeks" },
  { key: "3m", label: "Last 3 months" },
  { key: "12m", label: "Last 12 months" },
  { key: "custom", label: "Custom dates" },
];

export function presetRange(preset: Preset, today = new Date()): { start: string; end: string } {
  const end = iso(today);
  switch (preset) {
    case "week":
      return { start: iso(mondayOf(today)), end };
    case "4w":
      return { start: iso(addDays(today, -27)), end };
    case "3m":
      return { start: iso(addDays(today, -90)), end };
    default:
      return { start: iso(addDays(today, -364)), end };
  }
}

const dateFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const shortDate = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const monthFormat = new Intl.DateTimeFormat("en-GB", { month: "short", year: "2-digit" });

const parse = (value: string) => new Date(`${value}T00:00:00`);

export const fmtDate = (value: string) => dateFormat.format(parse(value));
export const fmtShortDate = (value: string) => shortDate.format(parse(value));
export const fmtMonth = (value: string) => monthFormat.format(parse(value));
export const fmtRating = (value: number | null) => (value === null ? "–" : value.toFixed(1));

export function timeAgo(value: string | null): string {
  if (!value) return "never";
  const minutes = Math.round((Date.now() - new Date(value).getTime()) / 60000);
  if (minutes < 60) return `${Math.max(minutes, 1)} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
