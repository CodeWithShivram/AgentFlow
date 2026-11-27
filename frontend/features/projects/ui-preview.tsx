import { api } from "@/lib/api";
import type { IntegrationRun, Uuid } from "@/lib/api/types";
import { useWorkspace } from "@/lib/workspace";

/** The newest UI screenshot a web project's integration runs took (ADR-0057), with its run. */
export function latestPreview(runs: IntegrationRun[]): { run: IntegrationRun; artifactId: Uuid } | null {
  for (const run of runs) {
    const check = run.checks.find((c) => c.preview_artifact_id);
    if (check?.preview_artifact_id) return { run, artifactId: check.preview_artifact_id };
  }
  return null;
}

/**
 * A web project's page as the sandbox's headless browser rendered it during its latest
 * integration run. The image is a stored artifact, served by the workspace-scoped artifacts API.
 */
export function UiPreview({ workflowId, runs }: { workflowId: Uuid; runs: IntegrationRun[] }) {
  const workspace = useWorkspace();
  const preview = latestPreview(runs);
  if (preview === null) return null;
  const passed = preview.run.checks.find((c) => c.preview_artifact_id)?.outcome === "passed";
  return (
    <figure className="space-y-2">
      {/* A plain <img>: next/image would fetch it server-side without the user's session
          cookie, which the workspace-scoped artifacts API requires. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={api.projects.artifactDownloadUrl(workspace.id, workflowId, preview.artifactId)}
        alt="Screenshot of the project's web page, rendered in the sandbox"
        loading="lazy"
        className="w-full rounded-md border bg-white"
      />
      <figcaption className="text-xs text-muted-foreground">
        Rendered by a headless browser in the sandbox at commit{" "}
        <code>{preview.run.head_sha.slice(0, 10)}</code>
        {passed ? " (UI checks passed)." : " (UI checks did not pass yet)."}
      </figcaption>
    </figure>
  );
}
