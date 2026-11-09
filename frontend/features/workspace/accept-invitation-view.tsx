"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { FormError, FormField } from "@/components/common/form-field";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import { ME_QUERY_KEY, useAuth } from "@/lib/auth/auth-provider";
import { workspacePath } from "@/lib/navigation";

export function AcceptInvitationView() {
  const { me } = useAuth();
  const router = useRouter();
  const queryClient = useQueryClient();
  // A code may arrive in the URL fragment (#token=...), which browsers never send to servers.
  // It is read once (this view renders only in the browser, after sign-in) and then removed
  // from the address bar and history.
  const [token, setToken] = useState(() =>
    typeof window === "undefined" ? "" : (new URLSearchParams(window.location.hash.slice(1)).get("token") ?? ""),
  );
  useEffect(() => {
    if (window.location.hash) window.history.replaceState(null, "", window.location.pathname);
  }, []);

  const accept = useMutation({
    mutationFn: (value: string) => api.workspaces.acceptInvitation(value),
    onSuccess: async (membership) => {
      setToken("");
      await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
      toast.success(`You joined ${membership.workspace_name}`);
      router.push(workspacePath(membership.workspace_id));
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Accept an invitation"
        description={`Paste the code you were given. It must have been issued to ${me?.user.email ?? "your email"}.`}
      />
      <Card>
        <CardContent>
          <form
            className="grid gap-4"
            noValidate
            onSubmit={(e) => {
              e.preventDefault();
              const value = token.trim();
              if (value && !accept.isPending) accept.mutate(value);
            }}
          >
            <FormError message={accept.isError ? describeError(accept.error) : null} />
            <FormField id="invitation-token" label="Invitation code">
              <Input
                id="invitation-token"
                autoComplete="off"
                spellCheck={false}
                maxLength={200}
                className="font-mono text-xs"
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
            </FormField>
            <div>
              <Button type="submit" disabled={!token.trim() || accept.isPending} aria-busy={accept.isPending}>
                {accept.isPending ? "Joining…" : "Join workspace"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
