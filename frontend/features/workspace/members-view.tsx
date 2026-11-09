"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { CopyIcon, UserPlusIcon } from "lucide-react";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { controlProps, FormError, FormField } from "@/components/common/form-field";
import { PageHeader } from "@/components/common/page-header";
import { Pagination } from "@/components/common/pagination";
import { RelativeTime } from "@/components/common/relative-time";
import { ErrorState, ListSkeleton } from "@/components/common/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import type { InvitableRole, InvitationCreated } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-provider";
import { formatDateTime } from "@/lib/format";
import { ROLE_LABELS } from "@/lib/roles";
import { useCan, useWorkspace } from "@/lib/workspace";

const PAGE_SIZE = 50;

const inviteSchema = z.object({
  email: z.email("Enter a valid email address.").max(320),
  role: z.enum(["ADMIN", "MEMBER", "VIEWER"]),
});
type InviteValues = z.infer<typeof inviteSchema>;

export function MembersView() {
  const workspace = useWorkspace();
  const { me } = useAuth();
  const isAdmin = useCan("ADMIN");
  const isOwner = useCan("OWNER");
  const [offset, setOffset] = useState(0);
  const [inviteOpen, setInviteOpen] = useState(false);

  const members = useQuery({
    queryKey: ["workspaces", workspace.id, "members", { offset }],
    queryFn: () => api.workspaces.members(workspace.id, { offset, limit: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Members"
        description="People with access to this workspace."
        actions={
          isAdmin ? (
            <Button onClick={() => setInviteOpen(true)}>
              <UserPlusIcon data-icon="inline-start" /> Invite
            </Button>
          ) : null
        }
      />
      {members.isPending ? (
        <ListSkeleton label="Loading members" />
      ) : members.isError ? (
        <ErrorState error={members.error} onRetry={() => void members.refetch()} />
      ) : (
        <>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead className="hidden sm:table-cell">Joined</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {members.data.items.map((m) => (
                  <TableRow key={m.user_id}>
                    <TableCell className="max-w-[14rem] truncate sm:max-w-none">
                      {m.email}
                      {m.user_id === me?.user.id ? <span className="text-muted-foreground"> (you)</span> : null}
                    </TableCell>
                    <TableCell>
                      <Badge variant={m.role === "OWNER" ? "default" : "secondary"}>{ROLE_LABELS[m.role]}</Badge>
                    </TableCell>
                    <TableCell className="hidden sm:table-cell">
                      <RelativeTime value={m.joined_at} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Pagination offset={offset} limit={PAGE_SIZE} total={members.data.total} onChange={setOffset} />
        </>
      )}
      <p className="text-xs text-muted-foreground">
        Changing a member&rsquo;s role or removing a member is not available yet: the API has no endpoint
        for it.
      </p>
      {isAdmin ? <InviteDialog open={inviteOpen} onOpenChange={setInviteOpen} allowAdmin={isOwner} /> : null}
    </div>
  );
}

function InviteDialog({ open, onOpenChange, allowAdmin }: { open: boolean; onOpenChange(open: boolean): void; allowAdmin: boolean }) {
  const workspace = useWorkspace();
  const [created, setCreated] = useState<InvitationCreated | null>(null);
  const form = useForm<InviteValues>({ resolver: zodResolver(inviteSchema), defaultValues: { email: "", role: "MEMBER" } });
  const { errors } = form.formState;

  const invite = useMutation({
    mutationFn: (values: InviteValues) =>
      api.workspaces.invite(workspace.id, { email: values.email, role: values.role as InvitableRole }),
    onSuccess: (result) => {
      setCreated(result);
      form.reset();
    },
  });

  const close = (next: boolean) => {
    if (invite.isPending) return;
    if (!next) {
      // The token is shown once; forget it when the dialog closes.
      setCreated(null);
      invite.reset();
      form.reset();
    }
    onOpenChange(next);
  };

  const copy = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.invitation_token);
      toast.success("Invitation code copied");
    } catch {
      toast.error("Copy failed. Select the code and copy it manually.");
    }
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite to {workspace.name}</DialogTitle>
          <DialogDescription>
            Email delivery is not part of the MVP. You get a one-time code to share with the person.
          </DialogDescription>
        </DialogHeader>
        {created ? (
          <div className="space-y-3">
            <Alert>
              <AlertTitle>Share this code now</AlertTitle>
              <AlertDescription>
                It is shown only once. {created.email} enters it on the &ldquo;Accept an invitation&rdquo; page after
                signing in. It expires {formatDateTime(created.expires_at)}.
              </AlertDescription>
            </Alert>
            <div className="flex gap-2">
              <Input readOnly value={created.invitation_token} aria-label="Invitation code" className="font-mono text-xs" onFocus={(e) => e.currentTarget.select()} />
              <Button variant="outline" size="icon" aria-label="Copy invitation code" onClick={() => void copy()}>
                <CopyIcon />
              </Button>
            </div>
            <DialogFooter>
              <Button onClick={() => close(false)}>Done</Button>
            </DialogFooter>
          </div>
        ) : (
          <form className="grid gap-4" noValidate onSubmit={form.handleSubmit((v) => invite.mutate(v))}>
            <FormError message={invite.isError ? describeError(invite.error) : null} />
            <FormField id="invite-email" label="Email" error={errors.email?.message}>
              <Input type="email" {...controlProps("invite-email", errors.email?.message)} {...form.register("email")} />
            </FormField>
            <FormField id="invite-role" label="Role" hint={allowAdmin ? undefined : "Only the owner can invite admins."}>
              <Controller
                control={form.control}
                name="role"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger className="w-full" {...controlProps("invite-role", undefined, !allowAdmin)}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {allowAdmin ? <SelectItem value="ADMIN">Admin</SelectItem> : null}
                      <SelectItem value="MEMBER">Member</SelectItem>
                      <SelectItem value="VIEWER">Viewer</SelectItem>
                    </SelectContent>
                  </Select>
                )}
              />
            </FormField>
            <DialogFooter>
              <Button type="button" variant="outline" disabled={invite.isPending} onClick={() => close(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={invite.isPending} aria-busy={invite.isPending}>
                {invite.isPending ? "Creating…" : "Create invitation"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
