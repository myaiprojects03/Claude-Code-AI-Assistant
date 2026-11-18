import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { UserManager, WebStorageStateStore, type User } from "oidc-client-ts";

/** Staff sign-in via Keycloak (OIDC authorization code + PKCE). The dashboard never sees
 *  passwords; tokens live in sessionStorage and refresh silently via the refresh token. */
const issuer = import.meta.env.VITE_OIDC_ISSUER ?? "http://localhost:8080/realms/assistant";

export const userManager = new UserManager({
  authority: issuer,
  client_id: "assistant-dashboard",
  redirect_uri: `${window.location.origin}/callback`,
  post_logout_redirect_uri: `${window.location.origin}/`,
  response_type: "code",
  scope: "openid profile email",
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  automaticSilentRenew: true,
});

export type Role = "admin" | "manager" | "consultant";

interface AuthState {
  user: User | null;
  loading: boolean;
  roles: Role[];
  name: string;
  login: (returnTo?: string) => Promise<void>;
  logout: () => Promise<void>;
  hasRole: (...roles: Role[]) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

function rolesOf(user: User | null): Role[] {
  if (!user) return [];
  try {
    const payload = JSON.parse(atob(user.access_token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    const all: string[] = payload?.realm_access?.roles ?? [];
    return all.filter((r): r is Role => ["admin", "manager", "consultant"].includes(r));
  } catch {
    return [];
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    userManager
      .getUser()
      .then(async (u) => {
        if (u && u.expired) {
          try {
            u = await userManager.signinSilent();
          } catch {
            u = null;
          }
        }
        if (alive) setUser(u);
      })
      .finally(() => alive && setLoading(false));
    const onLoaded = (u: User) => setUser(u);
    const onUnloaded = () => setUser(null);
    userManager.events.addUserLoaded(onLoaded);
    userManager.events.addUserUnloaded(onUnloaded);
    userManager.events.addSilentRenewError(onUnloaded);
    return () => {
      alive = false;
      userManager.events.removeUserLoaded(onLoaded);
      userManager.events.removeUserUnloaded(onUnloaded);
      userManager.events.removeSilentRenewError(onUnloaded);
    };
  }, []);

  const login = useCallback(async (returnTo?: string) => {
    await userManager.signinRedirect({ state: { returnTo: returnTo ?? window.location.pathname } });
  }, []);

  const logout = useCallback(async () => {
    const idToken = user?.id_token;
    await userManager.removeUser();
    try {
      await userManager.signoutRedirect({ id_token_hint: idToken });
    } catch {
      window.location.assign("/");
    }
  }, [user]);

  const value = useMemo<AuthState>(() => {
    const roles = rolesOf(user);
    const profile = user?.profile;
    return {
      user,
      loading,
      roles,
      name: (profile?.given_name as string) || (profile?.preferred_username as string) || "",
      login,
      logout,
      hasRole: (...wanted) => wanted.some((r) => roles.includes(r)),
    };
  }, [user, loading, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
