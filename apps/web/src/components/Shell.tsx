import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useAuth, type Role } from "../lib/auth";
import { useHandovers, useOverview } from "../lib/api";
import { londonClock } from "../lib/format";

interface NavItem {
  to: string;
  label: string;
  roles?: Role[];
  count?: number;
  alert?: boolean;
}

function useTheme() {
  const [theme, setTheme] = useState<string>(() => {
    try {
      return localStorage.getItem("theme") ?? "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* storage unavailable: theme still applies for this visit */
    }
  }, [theme]);
  const next = theme === "system" ? "dark" : theme === "dark" ? "light" : "system";
  return { theme, cycle: () => setTheme(next) };
}

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return <span className="num">{londonClock(now)} London</span>;
}

export function Shell({ children }: { children: ReactNode }) {
  const { name, roles, hasRole, logout } = useAuth();
  const { theme, cycle } = useTheme();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const overview = useOverview();
  const handovers = useHandovers("open");

  useEffect(() => setOpen(false), [location.pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const openHandovers = handovers.data?.length ?? 0;
  const urgent = handovers.data?.some((h) => h.urgency === "p1" || h.urgency === "high") ?? false;
  const items: NavItem[] = [
    { to: "/", label: "Overview" },
    { to: "/handovers", label: "Handovers", count: openHandovers, alert: urgent },
    { to: "/conversations", label: "Conversations", roles: ["admin", "manager"] },
    { to: "/leads", label: "Leads", roles: ["admin", "manager"] },
    { to: "/bookings", label: "Bookings", count: overview.data?.bookings_upcoming },
    { to: "/tickets", label: "Tickets", count: overview.data?.tickets_open },
  ];
  const workspace: NavItem[] = [
    { to: "/assistant", label: "Knowledge assistant" },
    { to: "/content", label: "Website content", roles: ["admin", "manager"], count: overview.data?.unanswered_30d },
    { to: "/settings", label: "Settings", roles: ["admin", "manager"] },
    { to: "/access", label: "Client access", roles: ["admin"] },
    { to: "/privacy", label: "Privacy requests", roles: ["admin"] },
    { to: "/audit", label: "Audit log", roles: ["admin"] },
  ];
  const link = (i: NavItem) => (
    <NavLink key={i.to} to={i.to} end={i.to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
      <span>{i.label}</span>
      {i.count ? (
        <span className={`count num${i.alert ? " alert" : ""}`} aria-label={`${i.count} items`}>
          {i.count}
        </span>
      ) : null}
    </NavLink>
  );
  const online = !overview.isError;

  return (
    <div className="shell">
      {open && <div className="scrim" onClick={() => setOpen(false)} aria-hidden />}
      <aside className={`sidebar${open ? " open" : ""}`} aria-label="Main navigation">
        <div className="brand">
          Acme <span className="slash">/</span> Assistant
        </div>
        <nav className="nav">
          <span className="nav-label micro faint">Operations</span>
          {items.filter((i) => !i.roles || hasRole(...i.roles)).map(link)}
          <span className="nav-label micro faint">Workspace</span>
          {workspace.filter((i) => !i.roles || hasRole(...i.roles)).map(link)}
        </nav>
        <div className="sidebar-foot">
          <div className="who">
            <span className="strong small">{name || "Signed in"}</span>
            <span className="micro faint">{roles.join(" · ") || "no role"}</span>
          </div>
          <div className="toolbar">
            <button className="btn ghost" onClick={cycle} title="Switch theme">
              Theme: {theme}
            </button>
            <button className="btn ghost" onClick={() => void logout()}>
              Sign out
            </button>
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="status-line small">
            <button className="btn ghost menu-btn" onClick={() => setOpen(!open)} aria-expanded={open} aria-label="Menu">
              Menu
            </button>
            <span className="live">
              <span className={`dot${online ? "" : " off"}`} aria-hidden />
              <span className="paren">{online ? "Live" : "Offline"}</span>
            </span>
            <span className="muted hide-sm">
              <Clock />
            </span>
          </div>
          <div className="status-line small muted">
            {openHandovers > 0 ? (
              <NavLink to="/handovers" className={urgent ? "strong" : ""}>
                {openHandovers} waiting for a person
              </NavLink>
            ) : (
              <span>Queue clear</span>
            )}
          </div>
        </header>
        <main className="page">{children}</main>
      </div>
    </div>
  );
}
