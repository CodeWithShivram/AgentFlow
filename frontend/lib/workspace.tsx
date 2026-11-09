"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { createContext, use } from "react";

import { api } from "@/lib/api";
import type { Uuid, WorkspaceRole } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-provider";
import { roleAtLeast } from "@/lib/roles";

export interface CurrentWorkspace {
  id: Uuid;
  name: string;
  /** The signed-in user's role here. The API enforces permissions; the UI only adapts. */
  role: WorkspaceRole;
}

const WorkspaceContext = createContext<CurrentWorkspace | null>(null);

export const WorkspaceProvider = WorkspaceContext;

export function useWorkspace(): CurrentWorkspace {
  const value = use(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used inside a workspace route");
  return value;
}

export function useCan(minimum: WorkspaceRole): boolean {
  return roleAtLeast(useWorkspace().role, minimum);
}

export function useWorkspaceIdParam(): Uuid {
  const params = useParams<{ workspaceId: string }>();
  return params.workspaceId;
}

/**
 * Resolves the workspace named in the URL. The session lists up to 100 memberships; anything
 * else is looked up, and the API answers 404 to non-members.
 */
export function useResolveWorkspace(workspaceId: Uuid) {
  const { me } = useAuth();
  const membership = me?.memberships.find((m) => m.workspace_id === workspaceId);
  const query = useQuery({
    queryKey: ["workspaces", workspaceId],
    queryFn: () => api.workspaces.get(workspaceId),
    enabled: membership === undefined,
  });
  if (membership) {
    return {
      workspace: { id: workspaceId, name: membership.workspace_name, role: membership.role },
      error: null,
      isLoading: false,
      refetch: query.refetch,
    };
  }
  return {
    workspace: query.data
      ? { id: query.data.id, name: query.data.name, role: query.data.role }
      : null,
    error: query.error,
    isLoading: query.isPending,
    refetch: query.refetch,
  };
}
