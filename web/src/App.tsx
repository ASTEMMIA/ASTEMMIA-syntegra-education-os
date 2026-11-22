import { NavLink, Navigate, Route, Routes, useLocation } from "react-router";
import { useSession } from "./session";
import { Loading } from "./components/ui";
import { SignIn, SsoCallback } from "./pages/SignIn";
import { SearchPage } from "./pages/Search";
import { DocumentPage } from "./pages/Document";
import { FilesPage, FilePage } from "./pages/Files";
import { FilePlanPage } from "./pages/FilePlan";
import { CapturePage, BatchesPage, BatchPage } from "./pages/Capture";
import { DispositionsPage } from "./pages/Dispositions";
import { OversightPage } from "./pages/Oversight";

interface NavItem {
  to: string;
  label: string;
  show: boolean;
}

function Shell({ children }: { children: React.ReactNode }) {
  const s = useSession();
  const me = s.me!;
  const nav: NavItem[] = [
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
            Records <span className="brand-sub">Syntegra Education</span>
          </span>
          <nav aria-label="Records">
            {nav.filter((n) => n.show).map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === "/"}>
                {n.label}
              </NavLink>
            ))}
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
      <InstitutionBar />
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
        <span className="eyebrow">Institution</span> <strong>{s.institution.name}</strong>{" "}
        <span className="muted mono">{s.institution.institution_code}</span>
      </div>
    ) : null;
  }
  return (
    <div className="inst-bar">
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
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<SearchPage />} />
        <Route path="/documents/:id" element={<DocumentPage />} />
        <Route path="/files" element={<FilesPage />} />
        <Route path="/files/:id" element={<FilePage />} />
        <Route path="/file-plan" element={<FilePlanPage />} />
        <Route path="/capture" element={<CapturePage />} />
        <Route path="/batches" element={<BatchesPage />} />
        <Route path="/batches/:id" element={<BatchPage />} />
        <Route path="/dispositions" element={<DispositionsPage />} />
        <Route path="/oversight" element={<OversightPage />} />
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
