import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { devLogin, finishSso, mfa, providers, type MfaChallenge, type Provider, type Tokens } from "../api";
import { useSession } from "../session";
import { Button, ErrorNote, Field, Loading, useAction } from "../components/ui";

const TENANT_KEY = "syn.tenant";

function lastTenant(): string {
  try {
    return localStorage.getItem(TENANT_KEY) ?? "NG-AB";
  } catch {
    return "NG-AB";
  }
}

export function SignIn() {
  const s = useSession();
  const [tenant, setTenant] = useState(lastTenant());
  const [login, setLogin] = useState("");
  const [idps, setIdps] = useState<Provider[]>([]);
  const act = useAction(async () => {
    try {
      localStorage.setItem(TENANT_KEY, tenant.trim().toUpperCase());
    } catch {
      /* ignore */
    }
    await s.signedIn(await devLogin(tenant, login));
  });

  useEffect(() => {
    if (tenant.trim().length < 2) return;
    const t = setTimeout(() => providers(tenant).then(setIdps, () => setIdps([])), 300);
    return () => clearTimeout(t);
  }, [tenant]);

  return (
    <main className="signin" id="main">
      <div className="signin-card">
        <span className="eyebrow">Syntegra Education</span>
        <h1>Sign in to Records</h1>
        {s.ended && <p className="note-info">Your session ended. Sign in again to carry on.</p>}
        <Field label="Organisation code" hint="For Abia State this is NG-AB.">
          {(id) => <input id={id} value={tenant} onChange={(e) => setTenant(e.target.value)} autoComplete="organization" />}
        </Field>
        {idps.length > 0 && (
          <div className="stack">
            {idps.map((p) => (
              <a
                key={p.key}
                className="btn btn-primary"
                href={`/v1/auth/oidc/${encodeURIComponent(p.key)}/start?tenant=${encodeURIComponent(tenant.trim().toUpperCase())}`}
              >
                Sign in with {p.name}
              </a>
            ))}
            <span className="divider">or, on a development server</span>
          </div>
        )}
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            void act.run();
          }}
        >
          <Field label="Login (development sign-in)">
            {(id) => (
              <input id={id} value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" placeholder="records@central.example" required />
            )}
          </Field>
          <ErrorNote error={act.error} />
          <Button busy={act.busy} type="submit">
            Sign in
          </Button>
        </form>
      </div>
    </main>
  );
}

/** Where the identity provider sends the browser back (set SYNTEGRA_OIDC_REDIRECT_URI to it). */
export function SsoCallback() {
  const s = useSession();
  const nav = useNavigate();
  const [challenge, setChallenge] = useState<MfaChallenge | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    window.history.replaceState(null, "", "/app/sso/callback"); // the code is single-use: drop it
    finishSso(params).then(
      async (r) => {
        if ("mfa_required" in r) setChallenge(r);
        else {
          await s.signedIn(r);
          nav("/", { replace: true });
        }
      },
      setError,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error)
    return (
      <main className="signin" id="main">
        <div className="signin-card">
          <h1>Sign-in did not finish</h1>
          <ErrorNote error={error} />
          <a className="btn btn-secondary" href="/app/">
            Start again
          </a>
        </div>
      </main>
    );
  if (!challenge) return <Loading what="Finishing sign-in" />;
  return (
    <MfaStep
      challenge={challenge}
      done={async (t) => {
        await s.signedIn(t);
        nav("/", { replace: true });
      }}
    />
  );
}

function MfaStep({ challenge, done }: { challenge: MfaChallenge; done: (t: Tokens) => Promise<void> }) {
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [recovery, setRecovery] = useState<string[] | null>(null);
  const [tokens, setTokens] = useState<Tokens | null>(null);
  const start = useAction(async () => setSetup(await mfa("enrolment", { mfa_token: challenge.mfa_token })));
  const verify = useAction(async () => {
    if (challenge.enrolment_required) {
      const r = await mfa("enrolment/confirm", { mfa_token: challenge.mfa_token, code: code.trim() });
      setRecovery(r.recovery_codes);
      setTokens(r);
    } else {
      await done(await mfa("verify", { mfa_token: challenge.mfa_token, code: code.trim() }));
    }
  });

  if (recovery && tokens)
    return (
      <main className="signin" id="main">
        <div className="signin-card">
          <h1>Save your recovery codes</h1>
          <p>Each code works once if you lose your phone. Keep them somewhere safe; they are not shown again.</p>
          <ul className="codes mono">
            {recovery.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
          <Button onClick={() => void done(tokens)}>I have saved them</Button>
        </div>
      </main>
    );

  return (
    <main className="signin" id="main">
      <div className="signin-card">
        <h1>{challenge.enrolment_required ? "Set up your authenticator app" : "Enter your code"}</h1>
        {challenge.enrolment_required && !setup && (
          <>
            <p>Your organisation asks for a second step at sign-in. You need an authenticator app on your phone.</p>
            <ErrorNote error={start.error} />
            <Button busy={start.busy} onClick={() => void start.run()}>
              Set it up
            </Button>
          </>
        )}
        {setup && (
          <div className="stack">
            <p>In your authenticator app, add an account and type this key:</p>
            <p className="key mono">{setup.secret.replace(/(.{4})/g, "$1 ").trim()}</p>
          </div>
        )}
        {(!challenge.enrolment_required || setup) && (
          <form
            className="stack"
            onSubmit={(e) => {
              e.preventDefault();
              void verify.run();
            }}
          >
            <Field label="6-digit code" hint={challenge.enrolment_required ? undefined : "Or one of your recovery codes."}>
              {(id) => <input id={id} value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" required />}
            </Field>
            <ErrorNote error={verify.error} />
            <Button busy={verify.busy} type="submit">
              Continue
            </Button>
          </form>
        )}
      </div>
    </main>
  );
}
