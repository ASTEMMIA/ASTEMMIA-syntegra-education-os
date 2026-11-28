import { useState } from "react";

/** A state's coat of arms, from public/brand/<organisation code>.webp (e.g. ng-ab.webp).
 * Nothing is shown for a state whose emblem has not been supplied. */
export function Emblem({ code, size, className = "" }: { code: string | null | undefined; size: number; className?: string }) {
  const [missing, setMissing] = useState<string | null>(null);
  const key = (code ?? "").trim().toLowerCase();
  if (!/^[a-z]{2}(-[a-z0-9]{1,8})?$/.test(key) || missing === key) return null;
  return (
    <img
      className={`emblem ${className}`}
      src={`${import.meta.env.BASE_URL}brand/${key}.webp`}
      alt="State coat of arms"
      height={size}
      style={{ height: size, width: "auto" }}
      onError={() => setMissing(key)}
    />
  );
}
