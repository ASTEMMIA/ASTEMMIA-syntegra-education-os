import { useEffect, useState } from "react";
import { api } from "./api";
import { useLoad } from "./session";
import type { ClassRef, ClassYear, Items, Period, StaffRef } from "./types";

const KEY = "syn.session";

/** The sessions that apply here, and the one the person is working in (remembered per tab). */
export function useAcademicSession() {
  const list = useLoad(() => api<Items<Period>>("/v1/sessions"), []);
  const [chosen, setChosen] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(KEY);
    } catch {
      return null;
    }
  });
  const items = list.data?.items ?? [];
  const current = items.find((s) => s.id === chosen) ?? items.find((s) => s.status === "current") ?? items[0] ?? null;
  useEffect(() => {
    try {
      if (current) sessionStorage.setItem(KEY, current.id);
    } catch {
      /* ignore */
    }
  }, [current]);
  return { sessions: items, session: current, choose: setChosen, loading: list.loading, error: list.error, reload: list.reload };
}

export function useYears() {
  return useLoad(() => api<Items<ClassYear>>("/v1/class-years"), []);
}

export function useClasses(institutionId: string | undefined, sessionId: string | undefined) {
  return useLoad(
    () =>
      institutionId && sessionId
        ? api<Items<ClassRef>>(`/v1/institutions/${institutionId}/classes`, { query: { session_id: sessionId } })
        : Promise.resolve({ items: [] as ClassRef[] }),
    [institutionId, sessionId],
  );
}

/** Teaching staff posted to the institution (for class teachers and subject teachers). */
export function useTeachers(institutionId: string | undefined, enabled: boolean) {
  return useLoad(
    () =>
      institutionId && enabled
        ? api<Items<StaffRef>>("/v1/staff", { query: { institution_id: institutionId, category: "teaching", limit: 200 } })
        : Promise.resolve({ items: [] as StaffRef[] }),
    [institutionId, enabled],
  );
}

export const LEVEL_NAME: Record<string, string> = {
  eccde: "Early years",
  primary: "Primary",
  jss: "Junior secondary",
  sss: "Senior secondary",
  adult: "Adult and non-formal",
  tvet: "Technical and vocational",
  nce: "NCE",
  nd: "ND",
  hnd: "HND",
  degree: "Degree",
  postgraduate: "Postgraduate",
};
