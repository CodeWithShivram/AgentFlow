import { ToneBadge } from "@/components/common/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ProjectPhase, ProjectProgress } from "@/lib/api/types";

import { describeRemaining, MILESTONE_LABELS, PHASE_LABELS, PHASE_TONES } from "./phase";

export function PhaseBadge({ phase }: { phase: ProjectPhase }) {
  return <ToneBadge tone={PHASE_TONES[phase]}>{PHASE_LABELS[phase]}</ToneBadge>;
}

/** An accessible progress bar for the API's weighted, verified-only percentage. */
export function EstimateBar({ percent }: { percent: number }) {
  const value = Math.max(0, Math.min(100, Math.round(percent)));
  return (
    <div
      role="progressbar"
      aria-label="Estimated progress"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value}
      aria-valuetext={`${value}% (estimate)`}
      className="h-3 w-full overflow-hidden rounded-full bg-muted"
    >
      <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${value}%` }} />
    </div>
  );
}

/** Time remaining as a range with its confidence; never a countdown or a single number. */
export function RemainingTime({ remaining }: { remaining: ProjectProgress["remaining"] }) {
  const { range, confidence, explanation } = describeRemaining(remaining);
  return (
    <div className="space-y-1" data-testid="remaining-time">
      <p className="text-sm font-medium">Time remaining</p>
      {range ? (
        <>
          <p className="text-lg font-semibold">
            {range} <span className="text-sm font-normal text-muted-foreground">({confidence})</span>
          </p>
          <p className="text-xs text-muted-foreground">{explanation}</p>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">{explanation}</p>
      )}
    </div>
  );
}

export function ProgressSummary({ progress }: { progress: ProjectProgress }) {
  const counts: [string, number][] = [
    ["Total", progress.tasks.total],
    ["Completed", progress.tasks.completed],
    ["In progress", progress.tasks.in_progress],
    ["Waiting", progress.tasks.waiting],
    ["Need attention", progress.tasks.needs_attention],
  ];
  return (
    <Card role="region" aria-label="Progress">
      <CardHeader>
        <CardTitle>
          <h2>Progress</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="space-y-2">
          <div className="flex items-baseline justify-between gap-2">
            <p className="text-sm font-medium">Estimate</p>
            <p className="text-sm tabular-nums">{Math.round(progress.percent)}%</p>
          </div>
          <EstimateBar percent={progress.percent} />
          <p className="text-xs text-muted-foreground">{progress.explanation}</p>
        </div>

        <RemainingTime remaining={progress.remaining} />

        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-5" aria-label="Task counts">
          {counts.map(([label, value]) => (
            <div key={label} className="rounded-md border px-3 py-2">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="text-lg font-semibold tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>

        <dl className="grid gap-1 text-sm sm:grid-cols-2">
          <div className="flex gap-2">
            <dt className="text-muted-foreground">Integration:</dt>
            <dd>{MILESTONE_LABELS[progress.integration]}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-muted-foreground">Package:</dt>
            <dd>{MILESTONE_LABELS[progress.package]}</dd>
          </div>
        </dl>
      </CardContent>
    </Card>
  );
}
