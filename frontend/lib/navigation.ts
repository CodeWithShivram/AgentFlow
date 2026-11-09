// URL helpers. Workspace selection lives in the URL (/w/<workspace id>/...).

import type { Me, Uuid } from "@/lib/api/types";

export const workspacePath = (workspaceId: Uuid, section = "dashboard") =>
  `/w/${encodeURIComponent(workspaceId)}/${section}`;

export function loginHref(next: string | null, expired = false): string {
  const params = new URLSearchParams();
  if (next && safeNextPath(next)) params.set("next", next);
  if (expired) params.set("expired", "1");
  const search = params.toString();
  return search ? `/login?${search}` : "/login";
}

/**
 * Accepts only same-site absolute paths, so a crafted `?next=` cannot send the user to another
 * site after signing in ("//evil.example", "https://...", "/\\evil" are all rejected).
 */
export function safeNextPath(value: string | null | undefined): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) {
    return null;
  }
  if ([...value].some((char) => char.charCodeAt(0) < 0x20)) return null;
  if (value === "/login" || value.startsWith("/login?") || value.startsWith("/register")) {
    return null;
  }
  return value;
}

/** Where to go after signing in: the requested page, else the first workspace. */
export function homePath(me: Pick<Me, "memberships">, next?: string | null): string {
  const safe = safeNextPath(next);
  if (safe) return safe;
  const first = me.memberships[0];
  return first ? workspacePath(first.workspace_id) : "/account";
}
