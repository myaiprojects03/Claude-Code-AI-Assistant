import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useAuth, userManager, type Role } from "./lib/auth";
import { Shell } from "./components/Shell";
import { Loading } from "./components/ui";
import { Login } from "./pages/Login";

const Overview = lazy(() => import("./pages/Overview"));
const Handovers = lazy(() => import("./pages/Handovers"));
const Conversations = lazy(() => import("./pages/Conversations"));
const ConversationView = lazy(() => import("./pages/ConversationView"));
const Leads = lazy(() => import("./pages/Leads"));
const Bookings = lazy(() => import("./pages/Bookings"));
const Tickets = lazy(() => import("./pages/Tickets"));
const Settings = lazy(() => import("./pages/Settings"));
const Content = lazy(() => import("./pages/Content"));
const Assistant = lazy(() => import("./pages/Assistant"));
const Access = lazy(() => import("./pages/Access"));
const Privacy = lazy(() => import("./pages/Privacy"));
const Audit = lazy(() => import("./pages/Audit"));

function Callback() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const done = useRef(false);
  useEffect(() => {
    if (done.current) return; // StrictMode runs effects twice; the code can only be used once
    done.current = true;
    userManager
      .signinRedirectCallback()
      .then((u) => {
        const returnTo = (u.state as { returnTo?: string } | undefined)?.returnTo;
        // Only a plain path inside this app: never "//host" or "/\host" (open redirects).
        const safe = typeof returnTo === "string" && /^\/(?![/\\])[\w\-/]*$/.test(returnTo) && returnTo !== "/callback";
        navigate(safe ? returnTo : "/", { replace: true });
      })
      .catch((e: Error) => setError(e.message));
  }, [navigate]);
  return (
    <div className="login">
      <div className="login-hero">
        {error ? (
          <>
            <h1>Sign-in didn't complete</h1>
            <p>{error}</p>
            <a className="btn primary" href="/">
              <span className="glyph">↪</span> Try again
            </a>
          </>
        ) : (
          <p className="muted">Signing you in…</p>
        )}
      </div>
    </div>
  );
}

function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { hasRole } = useAuth();
  return hasRole(...roles) ? <>{children}</> : <Navigate to="/" replace />;
}

export function App() {
  const { user, loading, roles } = useAuth();
  const location = useLocation();

  if (location.pathname === "/callback") return <Callback />;
  if (loading) return <div className="login"><div className="login-hero"><p className="muted">Loading…</p></div></div>;
  if (!user) return <Login />;
  if (roles.length === 0) return <Login noRole />;

  return (
    <Shell>
      <Suspense fallback={<Loading rows={6} />}>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/handovers" element={<Handovers />} />
          <Route path="/bookings" element={<Bookings />} />
          <Route path="/tickets" element={<Tickets />} />
          <Route path="/conversations" element={<RequireRole roles={["admin", "manager"]}><Conversations /></RequireRole>} />
          <Route path="/conversations/:id" element={<RequireRole roles={["admin", "manager"]}><ConversationView /></RequireRole>} />
          <Route path="/leads" element={<RequireRole roles={["admin", "manager"]}><Leads /></RequireRole>} />
          <Route path="/assistant" element={<Assistant />} />
          <Route path="/content" element={<RequireRole roles={["admin", "manager"]}><Content /></RequireRole>} />
          <Route path="/access" element={<RequireRole roles={["admin"]}><Access /></RequireRole>} />
          <Route path="/privacy" element={<RequireRole roles={["admin"]}><Privacy /></RequireRole>} />
          <Route path="/audit" element={<RequireRole roles={["admin"]}><Audit /></RequireRole>} />
          <Route path="/settings" element={<RequireRole roles={["admin", "manager"]}><Settings /></RequireRole>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </Shell>
  );
}
