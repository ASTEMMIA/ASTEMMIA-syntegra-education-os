/** Display helpers. Dates are shown the Nigerian way: 12 Sept 2022. */

export function date(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s.length === 10 ? `${s}T00:00:00` : s.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function dateTime(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s.replace(" ", "T") + (s.includes("Z") || s.includes("+") ? "" : "Z"));
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function words(code: string): string {
  const s = code.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export const PAPER_RULE: Record<string, string> = {
  destroy: "Destroy after retention, once digitised",
  transfer_archives: "Transfer to the State Archives",
  retain: "Keep the paper permanently",
};

export const NUMBERING: Record<string, string> = {
  single: "One running file",
  annual: "A new file each year",
  self: "One file per person",
  alphabetical: "By the subject's initial",
};

export const MOVE: Record<string, string> = {
  issue: "Issued",
  pass: "Passed on",
  external_out: "Sent out",
  return: "Returned",
};
