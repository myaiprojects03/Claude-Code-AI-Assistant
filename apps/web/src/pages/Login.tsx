import { useAuth } from "../lib/auth";

export function Login({ noRole = false }: { noRole?: boolean }) {
  const { login, logout } = useAuth();
  return (
    <div className="login">
      <header className="login-bar">
        <span className="strong">
          Acme <span className="faint">/</span> Assistant
        </span>
        <span className="small muted">
          <span className="paren">Staff only</span>
        </span>
      </header>

      <section className="login-hero">
        <h1>
          The operations desk for <span className="dim">your website assistant</span> &{" "}
          <span className="dim">client support.</span>
        </h1>
        {noRole ? (
          <>
            <p>
              You're signed in, but your account doesn't have a dashboard role yet. Ask an admin to give you
              consultant, manager or admin access in Keycloak.
            </p>
            <div className="toolbar">
              <button className="btn" onClick={() => void logout()}>
                <span className="glyph">↪</span> Sign out
              </button>
            </div>
          </>
        ) : (
          <>
            <p>
              Handovers waiting for a person, today's conversations, qualified leads, discovery calls, client tickets
              and the assistant's rules, in one place.
            </p>
            <div className="toolbar">
              <button className="btn primary" onClick={() => void login("/")}>
                <span className="glyph">↪</span> Sign in with company SSO
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
