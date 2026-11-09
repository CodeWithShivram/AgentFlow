"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/lib/auth/auth-provider";
import { homePath, workspacePath } from "@/lib/navigation";
import { useWorkspaceIdParam } from "@/lib/workspace";

import { FullPageSkeleton } from "./require-auth";

/** `/` sends signed-in users to their first workspace and everyone else to sign in. */
export function HomeRedirect() {
  const { status, me } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (status === "authenticated" && me) router.replace(homePath(me));
    if (status === "anonymous") router.replace("/login");
  }, [status, me, router]);
  return <FullPageSkeleton />;
}

/** `/w/<id>` opens that workspace's dashboard. */
export function WorkspaceIndexRedirect() {
  const workspaceId = useWorkspaceIdParam();
  const router = useRouter();
  useEffect(() => {
    router.replace(workspacePath(workspaceId));
  }, [workspaceId, router]);
  return null;
}
