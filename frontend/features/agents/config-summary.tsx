import { Badge } from "@/components/ui/badge";
import type { AgentConfiguration } from "@/lib/api/types";
import { humanize } from "@/lib/format";

/** A readable view of a configuration. Every value is rendered as text. */
export function ConfigSummary({ configuration }: { configuration: AgentConfiguration | null }) {
  if (!configuration) return <p className="text-sm text-muted-foreground">No configuration.</p>;
  const c = configuration;
  return (
    <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
      <dt className="text-muted-foreground">Instruction reference</dt>
      <dd className="font-mono text-xs break-all">{c.system_instruction_ref}</dd>
      <dt className="text-muted-foreground">Capabilities</dt>
      <dd className="flex flex-wrap gap-1">
        {c.capabilities.map((cap) => (
          <Badge key={cap} variant="secondary">
            {humanize(cap)}
          </Badge>
        ))}
      </dd>
      <dt className="text-muted-foreground">Tools</dt>
      <dd>
        {c.tools.length === 0
          ? "None"
          : c.tools.map((t) => `${t.tool} (${t.access})`).join(", ")}
      </dd>
      <dt className="text-muted-foreground">Model</dt>
      <dd>
        Profile {c.model.profile} · temperature {c.model.temperature} · up to{" "}
        {c.model.max_output_tokens.toLocaleString()} output tokens
      </dd>
      <dt className="text-muted-foreground">Limits</dt>
      <dd>
        Timeout {c.limits.timeout_seconds}s
        {c.limits.max_tokens_per_task ? ` · ${c.limits.max_tokens_per_task.toLocaleString()} tokens per task` : ""}
      </dd>
    </dl>
  );
}
