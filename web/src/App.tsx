import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router";
import { useSession } from "./session";
import { Loading } from "./components/ui";
import { Emblem } from "./components/Emblem";
import { SignIn, SsoCallback } from "./pages/SignIn";
import { SearchPage } from "./pages/Search";
import { DocumentPage } from "./pages/Document";
import { FilesPage, FilePage } from "./pages/Files";
import { FilePlanPage } from "./pages/FilePlan";
import { CapturePage, BatchesPage, BatchPage } from "./pages/Capture";
import { DispositionsPage } from "./pages/Dispositions";
import { OversightPage } from "./pages/Oversight";
import { ClassPage, ClassesPage, MyClassesPage } from "./pages/Classes";
import { AdmissionsPage } from "./pages/Admissions";
import { ImportPage, LearnerPage, LearnersPage } from "./pages/Learners";
import { CalendarPage } from "./pages/Calendar";
import { MessagesPage, ModerationPage, useUnread } from "./pages/Messages";
import { MonitoringPage } from "./pages/Monitoring";

const RECORDS = ["/", "/documents", "/files", "/file-plan", "/capture", "/batches", "/dispositions", "/oversight"];

interface NavItem {
  to: string;
  label: string;
  show: boolean;
}

function Shell({ children }: { children: React.ReactNode }) {
  const s = useSession();
  const me = s.me!;
  const loc = useLocation();
  const unread = useUnread();
  const parentOnly = me.grants.length > 0 && me.grants.every((g) => g.role === "PAR");
  const inRecords = !parentOnly && RECORDS.some((p) => (p === "/" ? loc.pathname === "/" : loc.pathname.startsWith(p)));
  const school: NavItem[] = [
    { to: "/learners", label: "Learners", show: s.can("learner:register") || (s.can("learner:view") && s.cell("learner:view") !== "C") },
    { to: "/admissions", label: "Admissions", show: s.can("learner:register", "admission:decide") },
    { to: "/classes", label: "Classes", show: s.can("class:manage", "teaching:assign", "learner:register") },
    { to: "/my-classes", label: "My classes", show: me.grants.some((g) => g.role === "TCH") },
    { to: "/calendar", label: "Calendar", show: s.can("calendar:manage", "curriculum:publish", "class:manage") },
  ];
  const records: NavItem[] = [
    { to: "/", label: "Search", show: true },
    { to: "/files", label: "Files", show: s.can("file:manage", "records:admin", "records:oversight") },
    { to: "/file-plan", label: "File plan", show: s.can("file:manage", "records:admin", "records:oversight") },
    { to: "/capture", label: "My scanning", show: s.can("document:capture") },
    { to: "/batches", label: "Batches", show: s.can("records:admin", "document:index", "records:oversight") },
    { to: "/dispositions", label: "Paper disposal", show: s.can("disposal:approve", "records:admin", "records:oversight") },
    { to: "/oversight", label: "Overview", show: s.can("records:oversight", "records:admin") },
  ];
  const scopes = me.grants.filter((g, i, a) => a.findIndex((x) => x.scope_node_id === g.scope_node_id) === i);
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="bar">
        <div className="bar-inner">
          <span className="brand">
            <Emblem code={me.tenant_code} size={34} />
            Syntegra Education
          </span>
          <nav aria-label="Main">
            <NavLink to="/messages">
              Messages
              {unread > 0 && (
                <span className="nav-count" aria-label={`${unread} unread`}>
                  {unread > 99 ? "99+" : unread}
                </span>
              )}
            </NavLink>
            {s.can("monitoring:view") && <NavLink to="/monitoring">Monitoring</NavLink>}
            {s.can("message:moderate") && <NavLink to="/moderation">Reports</NavLink>}
            {!parentOnly &&
              school.filter((n) => n.show).map((n) => (
                <NavLink key={n.to} to={n.to}>
                  {n.label}
                </NavLink>
              ))}
            {!parentOnly && (
              <Link to="/" className={inRecords ? "active" : ""} aria-current={inRecords ? "page" : undefined}>
                Records
              </Link>
            )}
          </nav>
          <div className="who">
            <label className="sr" htmlFor="scope">
              Working at
            </label>
            <select id="scope" value={me.active_scope_id} onChange={(e) => void s.switchScope(e.target.value)}>
              {scopes.map((g) => (
                <option key={g.scope_node_id} value={g.scope_node_id}>
                  {g.scope_name ?? "Scope"} · {g.role_name ?? g.role}
                </option>
              ))}
            </select>
            <span className="me">{me.display_name}</span>
            <button className="link" onClick={() => void s.signOut()}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      {inRecords && (
        <nav className="subnav" aria-label="Records">
          <div className="subnav-inner">
            {records.filter((n) => n.show).map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === "/"}>
                {n.label}
              </NavLink>
            ))}
          </div>
        </nav>
      )}
      {!parentOnly && <InstitutionBar />}
      <main id="main" className="main">
        {children}
      </main>
    </>
  );
}

function InstitutionBar() {
  const s = useSession();
  if (s.institutions.length <= 1) {
    return s.institution ? (
      <div className="inst-bar">
        <Emblem code={s.me?.tenant_code} size={48} />
        <span className="eyebrow">Institution</span> <strong>{s.institution.name}</strong>{" "}
        <span className="muted mono">{s.institution.institution_code}</span>
      </div>
    ) : null;
  }
  return (
    <div className="inst-bar">
      <Emblem code={s.me?.tenant_code} size={48} />
      <label className="eyebrow" htmlFor="inst">
        Institution
      </label>
      <select id="inst" value={s.institution?.id ?? ""} onChange={(e) => s.chooseInstitution(e.target.value)}>
        <option value="">All institutions in scope (search only)</option>
        {s.institutions.map((i) => (
          <option key={i.id} value={i.id}>
            {i.name} · {i.lga}
          </option>
        ))}
      </select>
    </div>
  );
}

export function App() {
  const s = useSession();
  const loc = useLocation();
  if (loc.pathname.startsWith("/sso/callback")) return <SsoCallback />;
  if (s.status === "loading") return <Loading what="Signing you in" />;
  if (s.status === "signedout") return <SignIn />;
  const parentOnly = !!s.me && s.me.grants.length > 0 && s.me.grants.every((g) => g.role === "PAR");
  return (
    <Shell>
      <Routes>
        <Route path="/" element={parentOnly ? <Navigate to="/messages" replace /> : <SearchPage />} />
        <Route path="/messages" element={<MessagesPage />} />
        <Route path="/messages/:id" element={<MessagesPage />} />
        <Route path="/moderation" element={<ModerationPage />} />
        <Route path="/monitoring" element={<MonitoringPage />} />
        <Route path="/documents/:id" element={<DocumentPage />} />
        <Route path="/files" element={<FilesPage />} />
        <Route path="/files/:id" element={<FilePage />} />
        <Route path="/file-plan" element={<FilePlanPage />} />
        <Route path="/capture" element={<CapturePage />} />
        <Route path="/batches" element={<BatchesPage />} />
        <Route path="/batches/:id" element={<BatchPage />} />
        <Route path="/dispositions" element={<DispositionsPage />} />
        <Route path="/oversight" element={<OversightPage />} />
        <Route path="/learners" element={<LearnersPage />} />
        <Route path="/learners/:id" element={<LearnerPage />} />
        <Route path="/class-lists" element={<ImportPage />} />
        <Route path="/admissions" element={<AdmissionsPage />} />
        <Route path="/classes" element={<ClassesPage />} />
        <Route path="/classes/:id" element={<ClassPage />} />
        <Route path="/my-classes" element={<MyClassesPage />} />
        <Route path="/calendar" element={<CalendarPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}

/** Pages that work on one institution say so when none is chosen. */
export function NeedInstitution() {
  return (
    <div className="empty">
      <strong>Choose an institution</strong>
      <div>Pick one from the list above to see its files, file plan and batches.</div>
    </div>
  );
}
