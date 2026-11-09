"use client";

import Link from "next/link";

import { PageHeader } from "@/components/common/page-header";
import { RelativeTime } from "@/components/common/relative-time";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth/auth-provider";
import { workspacePath } from "@/lib/navigation";
import { ROLE_LABELS } from "@/lib/roles";

export function AccountView() {
  const { me } = useAuth();
  if (!me) return null;
  return (
    <div className="space-y-6">
      <PageHeader title="Account" />
      <Card>
        <CardHeader>
          <CardTitle>
            <h2>Profile</h2>
          </CardTitle>
          <CardDescription>Changing your email or password is not available yet: the API has no endpoint for it.</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
            <dt className="text-muted-foreground">Email</dt>
            <dd className="font-medium break-all">{me.user.email}</dd>
            <dt className="text-muted-foreground">Member since</dt>
            <dd>
              <RelativeTime value={me.user.created_at} />
            </dd>
          </dl>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>
            <h2>Workspaces</h2>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {me.memberships.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              You are not a member of any workspace.{" "}
              <Link href="/invitations/accept" className="underline underline-offset-4">
                Accept an invitation
              </Link>
              .
            </p>
          ) : (
            <ul className="divide-y">
              {me.memberships.map((m) => (
                <li key={m.workspace_id} className="flex items-center gap-3 py-2">
                  <Link href={workspacePath(m.workspace_id)} className="flex-1 truncate font-medium hover:underline">
                    {m.workspace_name}
                  </Link>
                  <Badge variant="secondary">{ROLE_LABELS[m.role]}</Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
