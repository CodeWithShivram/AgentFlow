"use client";

import { Badge } from "@/components/ui/badge";
import type { AgentConfiguration, ToolRisk } from "@/lib/api/types";
import { humanize } from "@/lib/format";

import { useCatalog } from "./use-catalog";

const RISK_LABELS: Record<ToolRisk, string> = { LOW: "Low risk", MEDIUM: "Medium risk", HIGH: "High risk" };

/**
 * What approving a proposal would grant (Step 3.7, ADR-0024): every requested tool with its
 * risk, whether it can run today, and whether approval needs the workspace owner. The server
 * enforces all of this again on approval and at run time (ADR-0008, ADR-0019).
 */
export function PermissionReview({ configuration, isOwner }: { configuration: AgentConfiguration; isOwner: boolean }) {
  const catalog = useCatalog();
  const tools = configuration.tools ?? [];
  const byName = new Map((catalog.data?.tools ?? []).map((t) => [t.name, t]));

  return (
    <section aria-label="Permission review" className="space-y-1 text-sm">
      <p className="font-medium">Permission review</p>
      {tools.length === 0 ? (
        <p className="text-muted-foreground">Requests no tools.</p>
      ) : catalog.isPending ? (
        <p className="text-muted-foreground">Loading tool policy…</p>
      ) : (
        <ul className="space-y-1">
          {tools.map((grant) => {
            const tool = byName.get(grant.tool);
            return (
              <li key={grant.tool} className="flex flex-wrap items-center gap-2">
                <span>{humanize(grant.tool)}</span>
                <span className="text-xs text-muted-foreground">{grant.access ?? "read"} access</span>
                {tool ? (
                  <>
                    <Badge variant={tool.risk === "HIGH" ? "destructive" : "outline"}>{RISK_LABELS[tool.risk]}</Badge>
                    <span className="text-xs text-muted-foreground">
                      {tool.usable_now ? "Usable now" : "Not usable yet: grants a permission only"}
                    </span>
                    {tool.risk === "HIGH" ? (
                      <span className="text-xs text-amber-700 dark:text-amber-400">
                        Approving needs the workspace owner{isOwner ? "" : " (not you)"}.
                      </span>
                    ) : null}
                  </>
                ) : (
                  <span className="text-xs text-destructive">Not in the platform catalog.</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
