import { RelativeTime } from "@/components/common/relative-time";
import { type Tone, ToneBadge } from "@/components/common/status-badge";
import type { IntegrationCheck, IntegrationRun, IntegrationStatus } from "@/lib/api/types";
import { humanize } from "@/lib/format";

const RUN_TONES: Record<IntegrationStatus, Tone> = {
  RUNNING: "progress",
  PASSED: "success",
  FAILED: "danger",
  NOT_RUN: "neutral",
};

const CHECK_TONES: Record<IntegrationCheck["outcome"], Tone> = {
  passed: "success",
  failed: "danger",
  not_run: "neutral",
};

const SHOWN = 5;

/** Integration gate runs (ADR-0051), newest first. Check output is tool text: plain text only. */
export function IntegrationRuns({ runs }: { runs: IntegrationRun[] }) {
  if (runs.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No integration runs yet. The project is checked in a sandbox once its tasks are built.
      </p>
    );
  }
  const shown = runs.slice(0, SHOWN);
  return (
    <div className="space-y-3">
      <ul className="space-y-3" aria-label="Integration runs">
        {shown.map((run) => (
          <li key={run.id} className="space-y-2 rounded-lg border p-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <ToneBadge tone={RUN_TONES[run.status]}>{humanize(run.status)}</ToneBadge>
              <span className="text-muted-foreground">
                {run.stack_profile} · commit <code>{run.head_sha.slice(0, 10)}</code> ·{" "}
                <RelativeTime value={run.created_at} />
              </span>
            </div>
            {run.error_code ? (
              <p className="text-sm text-destructive">Error: {humanize(run.error_code)}</p>
            ) : null}
            {run.checks.length > 0 ? (
              <ul className="space-y-2" aria-label="Checks">
                {run.checks.map((check, index) => (
                  <CheckItem key={`${check.name}-${index}`} check={check} />
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ul>
      {runs.length > SHOWN ? (
        <p className="text-xs text-muted-foreground">
          Showing the latest {SHOWN} of {runs.length} runs.
        </p>
      ) : null}
    </div>
  );
}

function CheckItem({ check }: { check: IntegrationCheck }) {
  const facts = [
    typeof check.exit_code === "number" ? `exit code ${check.exit_code}` : null,
    check.timed_out ? "timed out" : null,
    typeof check.duration_ms === "number" ? `${(check.duration_ms / 1000).toFixed(1)} s` : null,
  ].filter(Boolean);
  return (
    <li className="space-y-1 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <ToneBadge tone={CHECK_TONES[check.outcome]}>{humanize(check.outcome)}</ToneBadge>
        <span className="font-medium">{check.name}</span>
        {facts.length > 0 ? <span className="text-xs text-muted-foreground">{facts.join(" · ")}</span> : null}
      </div>
      {check.output ? (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">Output</summary>
          <pre className="mt-1 max-h-80 overflow-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap break-words">
            {check.output}
          </pre>
        </details>
      ) : null}
    </li>
  );
}
