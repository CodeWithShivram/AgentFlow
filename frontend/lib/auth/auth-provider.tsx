"use client";

import { type QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, use, useCallback, useEffect, useMemo, useState } from "react";

import { api, apiClient } from "@/lib/api";
import { ApiError } from "@/lib/api/errors";
import type { Me, SessionInfo } from "@/lib/api/types";

export const ME_QUERY_KEY = ["auth", "me"] as const;

type AuthStatus = "loading" | "authenticated" | "anonymous" | "unavailable";

interface AuthContextValue {
  status: AuthStatus;
  me: Me | null;
  /** True after a session ended on its own (refresh failed), until the user signs in again. */
  sessionExpired: boolean;
  login(email: string, password: string): Promise<SessionInfo>;
  register(email: string, password: string, workspaceName?: string): Promise<SessionInfo>;
  logout(): Promise<void>;
  retry(): void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/** Removes every cached response except the session query, which components observe. */
function forgetSessionData(queryClient: QueryClient): void {
  void queryClient.cancelQueries({ predicate: (query) => query.queryKey[0] !== ME_QUERY_KEY[0] });
  queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== ME_QUERY_KEY[0] });
}

async function fetchMe(): Promise<Me | null> {
  try {
    return await api.auth.me();
  } catch (error) {
    // The client already tried to refresh; a 401 here means there is no session.
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [sessionExpired, setSessionExpired] = useState(false);
  const meQuery = useQuery({
    queryKey: ME_QUERY_KEY,
    queryFn: fetchMe,
    staleTime: 60_000,
  });

  useEffect(
    () =>
      apiClient.onSessionExpired(() => {
        const hadSession = queryClient.getQueryData(ME_QUERY_KEY) != null;
        // Drop every cached workspace response; nothing from the old session stays visible.
        forgetSessionData(queryClient);
        queryClient.setQueryData(ME_QUERY_KEY, null);
        if (hadSession) setSessionExpired(true);
      }),
    [queryClient],
  );

  const startSession = useCallback(
    (session: SessionInfo) => {
      forgetSessionData(queryClient);
      queryClient.setQueryData<Me>(ME_QUERY_KEY, {
        user: session.user,
        memberships: session.memberships,
      });
      setSessionExpired(false);
      return session;
    },
    [queryClient],
  );

  const login = useCallback(
    async (email: string, password: string) => startSession(await api.auth.login({ email, password })),
    [startSession],
  );

  const register = useCallback(
    async (email: string, password: string, workspaceName?: string) =>
      startSession(
        await api.auth.register({
          email,
          password,
          ...(workspaceName ? { workspace_name: workspaceName } : {}),
        }),
      ),
    [startSession],
  );

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      // Even if the call failed, forget everything locally; the cookies are HttpOnly.
      forgetSessionData(queryClient);
      queryClient.setQueryData(ME_QUERY_KEY, null);
      setSessionExpired(false);
    }
  }, [queryClient]);

  const { refetch } = meQuery;
  const retry = useCallback(() => void refetch(), [refetch]);

  const value = useMemo<AuthContextValue>(() => {
    let status: AuthStatus;
    if (meQuery.data) status = "authenticated";
    else if (meQuery.data === null) status = "anonymous";
    else if (meQuery.isError) status = "unavailable";
    else status = "loading";
    return {
      status,
      me: meQuery.data ?? null,
      sessionExpired,
      login,
      register,
      logout,
      retry,
    };
  }, [meQuery.data, meQuery.isError, sessionExpired, login, register, logout, retry]);

  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthContextValue {
  const value = use(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
