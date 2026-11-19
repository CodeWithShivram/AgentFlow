// Response and request shapes of the AgentFlow API (backend/app/schemas). UUIDs and timestamps
// arrive as strings; decimals (budget_limit) arrive as strings to keep their precision.

export type Uuid = string;
export type IsoDateTime = string;

export const WORKSPACE_ROLES = ["OWNER", "ADMIN", "MEMBER", "VIEWER"] as const;
export type WorkspaceRole = (typeof WORKSPACE_ROLES)[number];

export const WORKFLOW_STATUSES = [
  "PENDING",
  "PLANNING",
  "AWAITING_APPROVAL",
  "READY",
  "RUNNING",
  "PAUSED",
  "BLOCKED",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
] as const;
export type WorkflowStatus = (typeof WORKFLOW_STATUSES)[number];

export const TASK_STATUSES = [
  "PENDING",
  "READY",
  "ASSIGNED",
  "QUEUED",
  "IN_PROGRESS",
  "SUBMITTED",
  "VERIFYING",
  "REWORK_REQUIRED",
  "WAITING_APPROVAL",
  "BLOCKED",
  "ESCALATED",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];

export const EXECUTION_STATUSES = ["RUNNING", "SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"] as const;
export type ExecutionStatus = (typeof EXECUTION_STATUSES)[number];

export const AGENT_STATUSES = ["ACTIVE", "INACTIVE"] as const;
export type AgentStatus = (typeof AGENT_STATUSES)[number];

export const PROPOSAL_STATUSES = ["PENDING", "APPROVED", "REJECTED"] as const;
export type ProposalStatus = (typeof PROPOSAL_STATUSES)[number];

export type AutonomyPolicy = "approval_required" | "autonomous";

export const ANALYSIS_STATUSES = ["NOT_REQUESTED", "PENDING", "READY", "FAILED"] as const;
export type AnalysisStatus = (typeof ANALYSIS_STATUSES)[number];

export const PLAN_STATUSES = ["NOT_REQUESTED", "PENDING", "READY", "FAILED"] as const;
export type PlanStatus = (typeof PLAN_STATUSES)[number];

/** A generated plan draft (Step 2.3); the full draft is previewed in Step 2.4. */
export interface PlanDraftSummary {
  version: number;
  task_count: number;
  prompt_version: string;
  /** "replan": the remaining work of a paused or blocked workflow (ADR-0033). */
  kind: "plan" | "replan";
  /** A replan's completed tasks, kept and available to build on. */
  basis: { key: string; task_id: Uuid; title: string }[] | null;
  created_at: IsoDateTime;
}

export interface PlannedTask {
  key: string;
  title: string;
  description: string;
  required_skills: string[];
  deliverables: string[];
  acceptance_criteria: AcceptanceCriterion[];
  priority: number;
  required: boolean;
  depends_on: string[];
  /** Replans only: keys of the kept completed tasks this task builds on. */
  depends_on_completed?: string[];
}

export interface PlanProposal {
  schema_version: number;
  summary: string;
  tasks: PlannedTask[];
  risks: string[];
}

export interface PlanIssue {
  code: string;
  severity: "error" | "warning";
  message: string;
  task_keys: string[];
}

/** A plan draft with its DAG validation (Step 2.4). */
export interface PlanDraftPreview extends PlanDraftSummary {
  plan: PlanProposal;
  stages: string[][];
  issues: PlanIssue[];
}

/** The validated prompt analysis (backend app/planning/analysis.py PromptAnalysis). */
export interface PromptAnalysis {
  schema_version: 1;
  objective: string;
  requirements: { id: string; description: string; priority: "must" | "should" | "could" }[];
  constraints: {
    id: string;
    kind: "budget" | "deadline" | "scope" | "technical" | "compliance" | "other";
    description: string;
  }[];
  deliverables: string[];
  assumptions: string[];
  ambiguities: { id: string; question: string; impact: string }[];
  suggested_skills: string[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// --- auth and workspaces --------------------------------------------------------------------

export interface User {
  id: Uuid;
  email: string;
  created_at: IsoDateTime;
}

export interface Membership {
  workspace_id: Uuid;
  workspace_name: string;
  role: WorkspaceRole;
}

export interface Me {
  user: User;
  memberships: Membership[];
}

export interface SessionInfo extends Me {
  access_expires_at: IsoDateTime;
  session_expires_at: IsoDateTime;
}

export interface Workspace {
  id: Uuid;
  name: string;
  owner_id: Uuid;
  role: WorkspaceRole;
  created_at: IsoDateTime;
}

export interface Member {
  user_id: Uuid;
  email: string;
  role: WorkspaceRole;
  joined_at: IsoDateTime;
}

export type InvitableRole = Exclude<WorkspaceRole, "OWNER">;

export interface InvitationCreated {
  id: Uuid;
  workspace_id: Uuid;
  email: string;
  role: WorkspaceRole;
  expires_at: IsoDateTime;
  invitation_token: string;
}

export interface AcceptedMembership {
  workspace_id: Uuid;
  workspace_name: string;
  role: WorkspaceRole;
}

// --- agents ---------------------------------------------------------------------------------

export type ToolAccess = "read" | "write";

export interface ToolPermission {
  tool: string;
  access: ToolAccess;
}

export interface AgentConfiguration {
  schema_version: 1;
  system_instruction_ref: string;
  model: {
    profile: string;
    temperature: number;
    max_output_tokens: number;
    credential_ref?: string | null;
  };
  capabilities: string[];
  tools: ToolPermission[];
  input_schema_version: number;
  output_schema_version: number;
  limits: {
    timeout_seconds: number;
    max_tokens_per_task?: number | null;
  };
}

export type ToolRisk = "LOW" | "MEDIUM" | "HIGH";

export interface CatalogTool {
  name: string;
  risk: ToolRisk;
  description: string;
  available_in_phase_1: boolean;
  /** Implemented and enabled now; granting an unusable tool records a permission only. */
  usable_now: boolean;
}

export interface AgentCatalog {
  capabilities: string[];
  categories: string[];
  tools: CatalogTool[];
}

export interface Agent {
  id: Uuid;
  workspace_id: Uuid | null;
  is_builtin: boolean;
  name: string;
  category: string;
  description: string;
  status: AgentStatus;
  revision: number;
  active_version: number | null;
  draft_configuration: AgentConfiguration | null;
  has_unpublished_changes: boolean;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface AgentVersion {
  id: Uuid;
  agent_id: Uuid;
  version: number;
  configuration: AgentConfiguration;
  created_at: IsoDateTime;
  created_by: Uuid | null;
}

export interface AgentInput {
  name: string;
  category: string;
  description: string;
  configuration: AgentConfiguration;
}

export interface AgentUpdate extends Partial<AgentInput> {
  revision: number;
}

export interface ProposalInput extends AgentInput {
  rationale: string;
}

export interface Proposal {
  id: Uuid;
  workspace_id: Uuid;
  name: string;
  category: string;
  description: string;
  rationale: string;
  proposed_configuration: AgentConfiguration;
  status: ProposalStatus;
  requested_by: Uuid | null;
  reviewed_by: Uuid | null;
  reviewed_at: IsoDateTime | null;
  review_note: string | null;
  agent_id: Uuid | null;
  /** Set for platform proposals: the workflow task that had no suitable agent. */
  source_workflow_id: Uuid | null;
  source_task_id: Uuid | null;
  created_at: IsoDateTime;
}

// --- workflows and tasks --------------------------------------------------------------------

/** A background job the platform gave up on (ADR-0027): identifiers and an error type only. */
export interface FailedJob {
  id: Uuid;
  event_type: string;
  workflow_id: Uuid | null;
  task_id: Uuid | null;
  attempts: number;
  error_type: string | null;
  created_at: IsoDateTime;
  failed_at: IsoDateTime;
}

// --- requests for people (ADR-0035) --------------------------------------------------------

export type InterventionType = "CLARIFICATION" | "APPROVAL" | "REJECTION" | "MANUAL_RECOVERY";
export type InterventionStatus = "OPEN" | "RESOLVED" | "WITHDRAWN" | "EXPIRED";
export type InterventionSubject = "analysis_question" | "plan_approval" | "agent_action" | "task_escalation";

/**
 * A request that needs a person. `requested_action` and `context` can hold model or agent text
 * (an analysis question, an agent's proposed action): shown as plain text only.
 */
export interface Intervention {
  id: Uuid;
  workflow_id: Uuid;
  task_id: Uuid | null;
  type: InterventionType;
  subject: InterventionSubject;
  status: InterventionStatus;
  requested_action: string;
  context: Record<string, unknown>;
  requested_by: "SYSTEM" | "AGENT" | "USER";
  requested_by_agent_id: Uuid | null;
  resolved_by_user_id: Uuid | null;
  /** {answer}, {decision} (with `then` and a person's `note`), or {outcome} (e.g. expired). */
  resolution: Record<string, unknown> | null;
  resolved_at: IsoDateTime | null;
  expires_at: IsoDateTime | null;
  created_at: IsoDateTime;
  /** The start of the workflow's prompt. */
  workflow_prompt: string;
  /** APPROVAL only: the least role that may decide it (ADR-0038). */
  required_role?: WorkspaceRole | null;
  /** The immutable audit record, once an approval request is decided. */
  decision_record?: ApprovalRecord | null;
}

/** The audit record of a decision on an approval request (ADR-0038). */
export interface ApprovalRecord {
  decision: "approve" | "reject";
  then: string | null;
  /** A person's note: plain text, people only. */
  note: string | null;
  required_role: WorkspaceRole;
  /** Null when the request expired. */
  decided_by_user_id: Uuid | null;
  decided_role: WorkspaceRole | null;
  expired: boolean;
  by_workflow_creator: boolean;
  /** SHA-256 of the action and context the decider was shown. */
  action_fingerprint: string;
  decided_at: IsoDateTime;
}

export interface ApprovalDecision extends ApprovalRecord {
  id: Uuid;
  intervention_id: Uuid;
  workflow_id: Uuid;
  task_id: Uuid | null;
  execution_id: Uuid | null;
  subject: InterventionSubject;
  /** May be agent text: plain text only. */
  requested_action: string;
  workflow_prompt: string;
}

export interface NextStep {
  kind: string;
  actor: "people" | "agents" | "platform" | "none";
  summary: string;
  task_ids: Uuid[];
  assessed_at: IsoDateTime;
  graph_version: string;
}

export interface WorkflowSettings {
  autonomy_policy: AutonomyPolicy;
  budget_limit: string | null;
  currency: string;
  max_parallel_tasks: number;
  allow_dynamic_agent_proposals: boolean;
  deadline: IsoDateTime | null;
}

export interface WorkflowInput {
  prompt: string;
  settings: WorkflowSettings;
}

export interface Workflow {
  id: Uuid;
  workspace_id: Uuid;
  created_by_user_id: Uuid;
  prompt: string;
  status: WorkflowStatus;
  analysis_status: AnalysisStatus;
  normalized_requirements: PromptAnalysis | null;
  plan_status: PlanStatus;
  confirmed_plan_version: number | null;
  /** The orchestration graph's latest assessment; null until the first one (ADR-0025). */
  next_step: NextStep | null;
  autonomy_policy: AutonomyPolicy;
  budget_limit: string | null;
  currency: string;
  max_parallel_tasks: number;
  allow_dynamic_agent_proposals: boolean;
  deadline: IsoDateTime | null;
  revision: number;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  started_at: IsoDateTime | null;
  completed_at: IsoDateTime | null;
  /** Set while PAUSED (ADR-0036). `pause_note` is a person's text: plain text only. */
  pause_reason?: PauseReason | null;
  paused_by_user_id?: Uuid | null;
  paused_at?: IsoDateTime | null;
  pause_note?: string | null;
}

/** Why a workflow is paused. Only AWAITING_DECISION is lifted by the platform itself. */
export type PauseReason = "USER" | "DEADLINE_PASSED" | "AWAITING_DECISION" | "BUDGET_REACHED";

// --- analytics (ADR-0041) -------------------------------------------------------------------

export type AnalyticsGroupBy = "workflow" | "task" | "agent" | "model" | "purpose" | "day";

/** Costs of one currency; actual, estimated and mock are never added together (ADR-0040). */
export interface CostByCurrency {
  currency: string;
  actual: string;
  estimated: string;
  mock: string;
}

export interface UsageTotals {
  calls: number;
  unpriced_calls: number;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  costs: CostByCurrency[];
}

export interface BreakdownRow extends UsageTotals {
  /** An id, "provider/model", a purpose, an ISO date, or "platform:<purpose>". */
  key: string;
  label: string;
}

export interface AnalyticsRange {
  start: IsoDateTime;
  end: IsoDateTime;
}

export interface AnalyticsOverview {
  range: AnalyticsRange;
  totals: UsageTotals;
  workflows: number;
  agents: number;
}

export interface CostBreakdown {
  group_by: AnalyticsGroupBy;
  range: AnalyticsRange;
  rows: BreakdownRow[];
  totals: UsageTotals;
  truncated: boolean;
}

// --- execution report (ADR-0042) --------------------------------------------------------

export interface ReportAttempt {
  attempt_number: number;
  agent_id: Uuid;
  agent_name: string;
  agent_version: number;
  status: string;
  error_code: string | null;
  started_at: IsoDateTime;
  completed_at: IsoDateTime | null;
  duration_seconds: number | null;
  verification: { verifier: string; outcome: string; issue_codes: string[] }[];
}

export interface ReportTask {
  id: Uuid;
  title: string;
  status: string;
  required: boolean;
  removed: boolean;
  priority: number;
  required_skills: string[];
  depends_on: Uuid[];
  assigned_agent_id: Uuid | null;
  assigned_agent_name: string | null;
  assigned_agent_version: number | null;
  started_at: IsoDateTime | null;
  completed_at: IsoDateTime | null;
  attempts: ReportAttempt[];
  /** Agent text: plain text only. */
  deliverables: { name: string; content: string | null; truncated: boolean }[];
}

export interface ReportIntervention {
  id: Uuid;
  task_id: Uuid | null;
  type: string;
  subject: string;
  status: string;
  /** May be model or agent text: plain text only. */
  requested_action: string;
  requested_by: string;
  created_at: IsoDateTime;
  resolved_at: IsoDateTime | null;
  decision: string | null;
  then: string | null;
  outcome: string | null;
  decided_role: string | null;
}

export interface WorkflowReport {
  report_version: string;
  generated_at: IsoDateTime;
  final: boolean;
  workflow: {
    id: Uuid;
    prompt: string;
    status: string;
    pause_reason: string | null;
    autonomy_policy: string;
    budget_limit: string | null;
    currency: string;
    max_parallel_tasks: number;
    deadline: IsoDateTime | null;
    confirmed_plan_version: number | null;
    created_at: IsoDateTime;
    started_at: IsoDateTime | null;
    completed_at: IsoDateTime | null;
  };
  tasks: ReportTask[];
  interventions: ReportIntervention[];
  recovery: {
    retries: number;
    failed_attempts: number;
    reassignments: number;
    escalations: number;
    expired_requests: number;
    failed_background_jobs: number;
    pauses: { at: IsoDateTime; pause_reason: string | null; actor: string | null }[];
  };
  metrics: {
    tasks: number;
    tasks_by_status: Record<string, number>;
    attempts: number;
    attempts_by_status: Record<string, number>;
    verifications_passed: number;
    verifications_failed: number;
    elapsed_seconds: number | null;
  };
  costs: WorkflowCosts;
  usage: { workflow_id: Uuid; totals: UsageTotals; by_task: BreakdownRow[]; by_agent: BreakdownRow[]; by_model: BreakdownRow[] };
  requirements: Record<string, unknown> | null;
}

/** A workflow's model costs (ADR-0040). Amounts are decimal strings. */
export interface CostTotals {
  currency: string;
  actual: string;
  estimated: string;
  /** The mock provider's price: never a real charge. */
  mock: string;
}

export interface WorkflowCosts {
  calls: number;
  calls_by_status: Record<string, number>;
  unpriced_calls: number;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  totals: CostTotals[];
  budget: {
    limit: string;
    currency: string;
    comparable: boolean;
    counted_spend: string;
    reached: boolean;
  } | null;
  checked_calls: number;
  mismatched_calls: number;
  reconciled: boolean;
}

export interface AcceptanceCriterion {
  id: string;
  description: string;
}

export interface Task {
  id: Uuid;
  workflow_id: Uuid;
  title: string;
  description: string;
  required_skills: string[];
  input_references: string[];
  expected_output_schema: Record<string, unknown> | null;
  acceptance_criteria: AcceptanceCriterion[];
  deliverables: string[];
  priority: number;
  required: boolean;
  removed: boolean;
  status: TaskStatus;
  depends_on: Uuid[];
  assigned_agent_id: Uuid | null;
  assigned_agent_version_id: Uuid | null;
  attempt_count: number;
  revision: number;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  started_at: IsoDateTime | null;
  completed_at: IsoDateTime | null;
}

export interface TaskInput {
  title: string;
  description: string;
  required_skills: string[];
  acceptance_criteria: AcceptanceCriterion[];
  deliverables: string[];
  priority: number;
  required: boolean;
  depends_on: Uuid[];
}

export type TaskUpdate = Partial<Omit<TaskInput, "depends_on">> & { revision: number };

export interface WorkflowEvent {
  id: Uuid;
  sequence: number;
  task_id: Uuid | null;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: IsoDateTime;
}

export interface EventPage {
  items: WorkflowEvent[];
  last_sequence: number | null;
}

/** One selection rule's verdict for a candidate (ADR-0023). */
export interface SelectionFactor {
  name: string;
  outcome: "pass" | "fail" | "scored" | "not_scored";
  points: number;
  detail: string;
}

export interface AgentSuggestion {
  agent_id: Uuid;
  agent_name: string;
  agent_version_id: Uuid;
  agent_version: number;
  eligible: boolean;
  score: number;
  /** 1 = best eligible candidate; null for excluded candidates. */
  rank: number | null;
  factors: SelectionFactor[];
}

export interface AgentSuggestionList {
  rules_version: string;
  items: AgentSuggestion[];
}

export interface Execution {
  id: Uuid;
  task_id: Uuid;
  attempt_number: number;
  status: ExecutionStatus;
  agent_id: Uuid;
  agent_version_id: Uuid;
  agent_version: number;
  started_at: IsoDateTime;
  completed_at: IsoDateTime | null;
  error_code: string | null;
  error_summary: string | null;
  result_summary: string | null;
  submission: AgentSubmission | null;
  tool_invocations: ToolInvocation[];
  /** Platform verification of this attempt's submission, oldest first (ADR-0030). */
  verifications: Verification[];
  /** Corrective instructions this attempt was given (ADR-0032): fixed platform sentences. */
  rework_instructions: string[];
}

export type VerificationOutcome = "PASSED" | "FAILED";

/** One verifier's result for one attempt. Issues carry codes and locations, never agent text. */
export interface Verification {
  id: Uuid;
  task_id: Uuid;
  execution_id: Uuid;
  verifier: string;
  outcome: VerificationOutcome;
  checks: { name: string; outcome: "passed" | "failed" | "skipped" }[];
  issues: VerificationIssue[];
  /** The semantic evaluator's findings (ADR-0031): untrusted model text; null for checks. */
  findings: EvaluationFindings | null;
  created_at: IsoDateTime;
}

export type EvaluationVerdict = "met" | "partially_met" | "not_met";

/** Mirrors Evaluation (backend/app/verification/semantic.py). Model output: plain text only. */
export interface EvaluationFindings {
  schema_version: 1;
  objective: EvaluationVerdict;
  objective_reason: string;
  criteria: { criterion_id: string; verdict: EvaluationVerdict; evidence: string; reason: string }[];
  complete: boolean;
  consistent: boolean;
  assumptions_explicit: boolean;
  conflicts_with_requirements: boolean;
  summary: string;
}

export interface VerificationIssue {
  code: string;
  severity: "error" | "warning";
  criterion_id?: string | null;
  deliverable?: string | null;
  location?: string | null;
}

export type ToolInvocationStatus = "SUCCEEDED" | "DENIED" | "FAILED";

/** One tool gateway call (ADR-0019): metadata only, never arguments or output. */
export interface ToolInvocation {
  id: Uuid;
  tool_name: string;
  status: ToolInvocationStatus;
  error_code: string | null;
  duration_ms: number;
  input_hash: string;
  output_reference: string | null;
  created_at: IsoDateTime;
}

/** The agent's own claim about one acceptance criterion; platform verification checks it. */
export type CriterionStatus = "MET" | "PARTIAL" | "NOT_MET";

export interface CriterionClaim {
  criterion_id: string;
  status: CriterionStatus;
  evidence: string;
}

/** Mirrors AgentSubmission (backend/app/agents/contract.py). Untrusted agent output. */
export interface AgentSubmission {
  schema_version: 1;
  summary: string;
  outputs: Record<string, unknown>;
  criteria: CriterionClaim[];
  assumptions: string[];
  limitations: string[];
  open_questions: string[];
}
