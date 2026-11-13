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
  created_at: IsoDateTime;
}

// --- workflows and tasks --------------------------------------------------------------------

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
