/**
 * Government Monitoring Dashboard (ADR-031): the institutions beneath the active office,
 * counted from the platform's records. Demonstration data is counted and shown separately; areas
 * the platform does not record yet are listed as such, never filled with invented figures.
 */
import { useEffect } from "react";
import { api } from "../api";
import { useLoad } from "../session";
import { ErrorNote, Loading, PageHead } from "../components/ui";

interface SchoolStat {
  id: string;
  name: string;
  code: string;
  lga: string;
  institution_type: string;
  levels: string[];
  latitude: number | null;
  longitude: number | null;
  learners: number;
  girls: number;
  boys: number;
  demo_learners: number;
  teachers: number;
  demo_teachers: number;
  classes: number;
  places: number;
  pupil_teacher_ratio: number | null;
}

interface AreaStat {
  lga: string;
  institutions: number;
  learners: number;
  teachers: number;
}

export interface Overview {
  as_of: string;
  scope_name: string;
  institutions: number;
  institutions_by_type: Record<string, number>;
  learners: number;
  girls: number;
  boys: number;
  demo_learners: number;
  teachers: number;
  demo_teachers: number;
  classes: number;
  places: number;
  pupil_teacher_ratio: number | null;
  learners_by_level: Record<string, number>;
  admissions_open: number;
  admissions_overdue: number;
  transfers_waiting: number;
  documents: number;
  messages_7_days: number;
  by_lga: AreaStat[];
  schools: SchoolStat[];
  not_yet: { area: string; needs: string }[];
}

const TYPE: Record<string, [string, string]> = {
  school: ["school", "schools"],
  polytechnic: ["polytechnic", "polytechnics"],
  technical_college: ["technical college", "technical colleges"],
  college_of_education: ["college of education", "colleges of education"],
  university: ["university", "universities"],
  adult_centre: ["adult learning centre", "adult learning centres"],
};

function kind(type: string, count: number): string {
  const [one, many] = TYPE[type] ?? [type.replace(/_/g, " "), type.replace(/_/g, " ")];
  return `${count} ${count === 1 ? one : many}`;
}

const n = (v: number) => v.toLocaleString("en-NG");

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      {note && <span className="tile-note">{note}</span>}
    </div>
  );
}

/** One series of horizontal bars, largest first, each labelled with its value. */
function Bars({ data, label }: { data: [string, number][]; label: string }) {
  const max = Math.max(1, ...data.map(([, v]) => v));
  return (
    <ul className="hbars" aria-label={label}>
      {data.map(([k, v]) => (
        <li key={k} title={`${k}: ${n(v)}`}>
          <span className="hbar-label">{k}</span>
          <span className="hbar-track">
            <span className="hbar" style={{ width: `${(100 * v) / max}%` }} />
          </span>
          <span className="hbar-value">{n(v)}</span>
        </li>
      ))}
    </ul>
  );
}

// Abia State's extent, for placing institutions by their coordinates.
const BOX = { south: 4.75, north: 6.1, west: 7.15, east: 8.05 };

/** Where the institutions are: a dot per institution, sized by learners. No map tiles (the app
 * loads nothing from other sites), so positions are drawn on the State's extent. */
function Places({ schools }: { schools: SchoolStat[] }) {
  const placed = schools.filter((s) => s.latitude !== null && s.longitude !== null);
  const W = 420;
  const H = Math.round((W * (BOX.north - BOX.south)) / (BOX.east - BOX.west));
  const max = Math.max(1, ...placed.map((s) => s.learners));
  const x = (lng: number) => ((lng - BOX.west) / (BOX.east - BOX.west)) * W;
  const y = (lat: number) => ((BOX.north - lat) / (BOX.north - BOX.south)) * H;
  return (
    <figure className="places">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Locations of ${placed.length} institutions in Abia State`}>
        <rect x="0" y="0" width={W} height={H} className="places-bg" rx="8" />
        {placed.map((s) => {
          const r = 5 + 9 * Math.sqrt(s.learners / max);
          return (
            <g key={s.id}>
              <circle cx={x(s.longitude!)} cy={y(s.latitude!)} r={r + 2} className="dot-ring" />
              <circle cx={x(s.longitude!)} cy={y(s.latitude!)} r={r} className="dot">
                <title>{`${s.name}, ${s.lga}: ${n(s.learners)} learners, ${n(s.teachers)} teachers`}</title>
              </circle>
            </g>
          );
        })}
      </svg>
      <figcaption className="small muted">
        {placed.length} of {schools.length} institutions placed by their recorded coordinates; dot size shows learners. Hover a dot for its figures.
      </figcaption>
    </figure>
  );
}

export function MonitoringPage() {
  const o = useLoad(() => api<Overview>("/v1/monitoring/overview"), []);
  useEffect(() => {
    const t = setInterval(() => document.visibilityState === "visible" && o.reload(), 60_000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (o.loading && !o.data) return <Loading what="Counting" />;
  if (o.error && !o.data) return <ErrorNote error={o.error} />;
  const d = o.data!;
  const used = d.places ? Math.round((100 * d.schools.reduce((a, s) => a + (s.places ? s.learners : 0), 0)) / d.places) : null;
  return (
    <div className="page">
      <PageHead eyebrow={d.scope_name} title="Government Monitoring Dashboard">
        <span className="small muted" role="status">
          As of {new Date(d.as_of + "Z").toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })} · updates every minute
        </span>
      </PageHead>
      {(d.demo_learners > 0 || d.demo_teachers > 0) && (
        <p className="demo-banner" role="note">
          <strong>Includes demonstration data.</strong> {n(d.demo_learners)} of {n(d.learners)} learners and {n(d.demo_teachers)} of{" "}
          {n(d.teachers)} teachers are made up for the demonstration; they are not real people.
        </p>
      )}
      <section className="tiles" aria-label="Headline figures">
        <Tile
          label="Institutions onboarded"
          value={n(d.institutions)}
          note={Object.entries(d.institutions_by_type)
            .map(([k, v]) => kind(k, v))
            .join(" · ")}
        />
        <Tile label="Learners enrolled" value={n(d.learners)} note={`${n(d.girls)} girls · ${n(d.boys)} boys`} />
        <Tile label="Teachers in post" value={n(d.teachers)} note="Teaching staff posted to these institutions" />
        <Tile label="Learners per teacher" value={d.pupil_teacher_ratio?.toString() ?? "—"} />
        <Tile label="Classes" value={n(d.classes)} note={used !== null ? `${used}% of ${n(d.places)} places filled` : undefined} />
        <Tile
          label="Admissions waiting"
          value={n(d.admissions_open)}
          note={d.admissions_overdue ? `${n(d.admissions_overdue)} past the 10-day decision date` : "None past the decision date"}
        />
        <Tile label="Transfers waiting" value={n(d.transfers_waiting)} />
        <Tile label="Records digitised" value={n(d.documents)} note="Documents in the repository" />
        <Tile label="Messages, last 7 days" value={n(d.messages_7_days)} note="Counted only; no content" />
      </section>
      <div className="two">
        <section className="panel stack">
          <h2>Learners by level</h2>
          <Bars label="Learners by level" data={Object.entries(d.learners_by_level)} />
        </section>
        <section className="panel stack">
          <h2>Where the institutions are</h2>
          <Places schools={d.schools} />
        </section>
      </div>
      <section className="panel stack">
        <h2>By local government area</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>LGA</th>
                <th className="num">Institutions</th>
                <th className="num">Learners</th>
                <th className="num">Teachers</th>
                <th className="num">Learners per teacher</th>
              </tr>
            </thead>
            <tbody>
              {d.by_lga.map((a) => (
                <tr key={a.lga}>
                  <td>{a.lga}</td>
                  <td className="num">{n(a.institutions)}</td>
                  <td className="num">{n(a.learners)}</td>
                  <td className="num">{n(a.teachers)}</td>
                  <td className="num">{a.teachers ? (a.learners / a.teachers).toFixed(1) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel stack">
        <h2>Institutions</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Institution</th>
                <th>LGA</th>
                <th className="num">Learners</th>
                <th className="num">Girls</th>
                <th className="num">Boys</th>
                <th className="num">Teachers</th>
                <th className="num">Per teacher</th>
                <th className="num">Classes</th>
              </tr>
            </thead>
            <tbody>
              {d.schools.map((s) => (
                <tr key={s.id}>
                  <td>
                    {s.name}
                    {s.demo_learners > 0 && <span className="badge demo-badge">Demonstration data</span>}
                    <div className="small muted mono">{s.code}</div>
                  </td>
                  <td>{s.lga}</td>
                  <td className="num">{n(s.learners)}</td>
                  <td className="num">{n(s.girls)}</td>
                  <td className="num">{n(s.boys)}</td>
                  <td className="num">{n(s.teachers)}</td>
                  <td className="num">{s.pupil_teacher_ratio ?? "—"}</td>
                  <td className="num">{s.classes ? n(s.classes) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel stack">
        <h2>Not recorded yet</h2>
        <p className="small muted">The dashboard shows these once the platform records them. Nothing here is estimated.</p>
        <ul className="not-yet">
          {d.not_yet.map((x) => (
            <li key={x.area}>
              <strong>{x.area}</strong>
              <span className="small muted">{x.needs}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
