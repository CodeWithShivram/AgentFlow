# ADR-0063: "Build it for me" — one click from prompt to a running build

- Status: Accepted
- Date: 2026-11-28
- Implements: Step 9.17

## Context
- **Starting a project takes five manual steps.** Plan & controls → start planning → wait → confirm the draft → send for approval → approve → start. People asked for one action, and every live run used a script that does exactly these API calls.
- **The safeguards must stay.** The workflow state machine, plan confirmation and approval roles (ADR-0009, ADR-0017, ADR-0038) exist so that a person decides. A shortcut must not bypass them.

## Decision
- **A client-side autopilot, not a new server path.** The project page shows **Build it for me** to members while the project is PENDING, PLANNING, AWAITING_APPROVAL or READY. After a confirmation that states what it will do, it repeats one step at a time with the person's own session and the existing endpoints: read the workflow, pick the next step, do it, until the build runs or a stop.
- **The steps** (`nextAutopilotStep`, pure and tested):
  1. PENDING → PLANNING.
  2. Wait while analysis and planning run (polling every 3 s).
  3. Confirm the latest draft, accepting warnings. **Errors stop it.**
  4. PLANNING (confirmed) → AWAITING_APPROVAL.
  5. AWAITING_APPROVAL → READY **only for admins**; members stop with "Waiting for an admin to approve the plan".
  6. READY → start.
- **It stops for people** when planning failed, when the planner has questions (analysis READY but no plan requested), at plan errors, and at approval for non-admins. API errors go through the usual error handling.
- **It needs the page open** until the build starts. Leaving stops it; pressing it again continues from the current state, since every step is read from the server.

## Consequences
- **One click for the common case,** with the same authorization, revisions, events and audit as the manual path. The person who clicks is the person recorded.
- **Warnings are accepted on the person's behalf.** The confirmation dialog says so; anyone who wants to review warnings uses Plan & controls.
- **No server-side autopilot yet.** A run that must continue with the browser closed would need a worker-side orchestrator and a new ADR.
