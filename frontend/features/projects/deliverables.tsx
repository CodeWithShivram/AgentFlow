"use client";

import { useMutation } from "@tanstack/react-query";
import { DownloadIcon, PackageIcon, AlertTriangleIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { ToneBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/errors";
import type { ProjectPackage, Uuid } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";
import { useActionErrors } from "@/lib/hooks/use-action-errors";
import { useWorkspace } from "@/lib/workspace";

import { packageErrorHelp } from "./phase";

/** Packages of the project (ADR-0052), newest first, with download links for ready ones. */
export function Deliverables({ workflowId, packages }: { workflowId: Uuid; packages: ProjectPackage[] }) {
  if (packages.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No packages yet. A verified ZIP is made automatically once the project passes integration.
      </p>
    );
  }
  return (
    <ul className="space-y-3" aria-label="Packages">
      {packages.map((pkg) => (
        <PackageItem key={pkg.id} workflowId={workflowId} pkg={pkg} />
      ))}
    </ul>
  );
}

function PackageItem({ workflowId, pkg }: { workflowId: Uuid; pkg: ProjectPackage }) {
  const workspace = useWorkspace();
  const href = (artifactId: Uuid) => api.projects.artifactDownloadUrl(workspace.id, workflowId, artifactId);
  const verified = pkg.label === "VERIFIED";
  return (
    <li className="space-y-2 rounded-lg border p-3" aria-label={`${verified ? "Verified" : "Partial"} package`}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <ToneBadge tone={verified ? "success" : "warning"}>{verified ? "Verified" : "Partial"}</ToneBadge>
        {pkg.status === "FAILED" ? <ToneBadge tone="danger">Failed</ToneBadge> : null}
        <span className="text-muted-foreground">
          {formatDateTime(pkg.created_at)} · commit <code>{pkg.head_sha.slice(0, 10)}</code>
        </span>
      </div>

      {pkg.status === "FAILED" ? (
        <p className="text-sm text-destructive">
          {pkg.error_code ? <code className="mr-1">{pkg.error_code}</code> : null}
          {packageErrorHelp(pkg.error_code)}
        </p>
      ) : (
        <>
          {verified ? (
            <p className="text-sm text-muted-foreground">This state passed every integration check.</p>
          ) : (
            <p className="flex items-start gap-1.5 text-sm text-amber-800 dark:text-amber-300">
              <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
              Partial: this state did not pass the integration checks. It may be incomplete or may not
              build or run.
            </p>
          )}
          <p className="text-sm">
            {pkg.file_count} {pkg.file_count === 1 ? "file" : "files"}
            {pkg.excluded_count > 0 ? ` · ${pkg.excluded_count} excluded` : ""}
          </p>
          <div className="flex flex-wrap gap-2">
            {pkg.archive_artifact_id ? (
              <Button size="sm" asChild>
                <a href={href(pkg.archive_artifact_id)} download>
                  <DownloadIcon data-icon="inline-start" />
                  Download ZIP
                </a>
              </Button>
            ) : null}
            {pkg.report_artifact_id ? (
              <Button size="sm" variant="outline" asChild>
                <a href={href(pkg.report_artifact_id)} download>
                  <DownloadIcon data-icon="inline-start" />
                  Build report
                </a>
              </Button>
            ) : null}
          </div>
        </>
      )}
    </li>
  );
}

const PACKAGE_CONFLICTS: Record<string, string> = {
  NOT_A_SOFTWARE_PROJECT: "Only software projects can be packaged.",
  PACKAGING_NOT_AVAILABLE: "Packaging is available only while the project is blocked, paused or completed.",
};

/** ADMIN: package the project's current state now. Labelled PARTIAL unless it was verified. */
export function PackageNowAction({ workflowId, onRequested }: { workflowId: Uuid; onRequested(): void }) {
  const workspace = useWorkspace();
  const [open, setOpen] = useState(false);
  const errors = useActionErrors();
  const request = useMutation({
    mutationFn: () => api.projects.requestPackage(workspace.id, workflowId),
    onSuccess: () => {
      setOpen(false);
      toast.success("Package requested. It appears under Deliverables when ready.");
      onRequested();
    },
    onError: (error) => {
      setOpen(false);
      if (error instanceof ApiError && error.status === 409 && PACKAGE_CONFLICTS[error.code]) {
        toast.error(PACKAGE_CONFLICTS[error.code]);
        return;
      }
      errors.handle(error);
    },
  });
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <PackageIcon data-icon="inline-start" />
        Package current state (partial)
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Package the current state?"
        description="AgentFlow packages the project's files as they are now. The ZIP is labelled PARTIAL unless this state passed every integration check."
        confirmLabel="Package now"
        pending={request.isPending}
        onConfirm={() => request.mutate()}
      />
    </>
  );
}
