"use client";

import { BotIcon, CompassIcon, HourglassIcon } from "lucide-react";

import { RelativeTime } from "@/components/common/relative-time";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { NextStep } from "@/lib/api/types";

const TITLES: Record<NextStep["actor"], string> = {
  people: "Next step for you",
  agents: "Agents are working",
  platform: "Waiting for the platform",
  none: "Next step",
};

/**
 * The orchestration graph's latest assessment (Step 4.1, ADR-0025): what has to happen next and
 * who has to act. It is refreshed by a worker after each status change, so it can lag behind
 * the page for a moment; the time shows how recent it is.
 */
export function NextStepBanner({ nextStep }: { nextStep: NextStep | null }) {
  if (nextStep === null || nextStep.kind === "finished") return null;
  const Icon = nextStep.actor === "agents" ? BotIcon : nextStep.actor === "platform" ? HourglassIcon : CompassIcon;
  return (
    <Alert role="region" aria-label="Next step">
      <Icon aria-hidden />
      <AlertTitle>{TITLES[nextStep.actor]}</AlertTitle>
      <AlertDescription>
        <p>{nextStep.summary}</p>
        <p className="text-xs text-muted-foreground">
          Assessed <RelativeTime value={nextStep.assessed_at} />
        </p>
      </AlertDescription>
    </Alert>
  );
}
