// Typed wrappers for every API endpoint the frontend uses. Paths and payloads follow
// backend/app/api/v1; nothing here invents fields.

import type { ApiClient } from "./client";
import type {
  AcceptedMembership,
  Agent,
  AgentCatalog,
  AgentInput,
  AgentStatus,
  AgentUpdate,
  AgentVersion,
  EventPage,
  Execution,
  InvitableRole,
  InvitationCreated,
  Me,
  Member,
  Page,
  Proposal,
  ProposalInput,
  ProposalStatus,
  SessionInfo,
  Task,
  TaskInput,
  TaskUpdate,
  Uuid,
  Workflow,
  WorkflowInput,
  WorkflowStatus,
  Workspace,
} from "./types";

export interface PageQuery {
  limit?: number;
  offset?: number;
}

const enc = encodeURIComponent;

export function createEndpoints(client: ApiClient) {
  const ws = (workspaceId: Uuid) => `/workspaces/${enc(workspaceId)}`;
  const wf = (workspaceId: Uuid, workflowId: Uuid) =>
    `${ws(workspaceId)}/workflows/${enc(workflowId)}`;
  const task = (workspaceId: Uuid, workflowId: Uuid, taskId: Uuid) =>
    `${wf(workspaceId, workflowId)}/tasks/${enc(taskId)}`;
  const agent = (workspaceId: Uuid, agentId: Uuid) => `${ws(workspaceId)}/agents/${enc(agentId)}`;

  return {
    auth: {
      // Credentials are wrong or missing on these; never try to refresh.
      register: (body: { email: string; password: string; workspace_name?: string }) =>
        client.post<SessionInfo>("/auth/register", body, { refreshOnAuthFailure: false }),
      login: (body: { email: string; password: string }) =>
        client.post<SessionInfo>("/auth/login", body, { refreshOnAuthFailure: false }),
      logout: () => client.post<void>("/auth/logout", undefined, { refreshOnAuthFailure: false }),
      me: () => client.get<Me>("/auth/me"),
    },

    workspaces: {
      list: (query: PageQuery = {}) => client.get<Page<Workspace>>("/workspaces", { query: { ...query } }),
      get: (workspaceId: Uuid) => client.get<Workspace>(ws(workspaceId)),
      members: (workspaceId: Uuid, query: PageQuery = {}) =>
        client.get<Page<Member>>(`${ws(workspaceId)}/members`, { query: { ...query } }),
      invite: (workspaceId: Uuid, body: { email: string; role: InvitableRole }) =>
        client.post<InvitationCreated>(`${ws(workspaceId)}/invitations`, body),
      acceptInvitation: (token: string) =>
        client.post<AcceptedMembership>("/invitations/accept", { token }),
    },

    agents: {
      catalog: () => client.get<AgentCatalog>("/agent-catalog"),
      list: (workspaceId: Uuid, query: PageQuery & { status?: AgentStatus } = {}) =>
        client.get<Page<Agent>>(`${ws(workspaceId)}/agents`, { query: { ...query } }),
      get: (workspaceId: Uuid, agentId: Uuid) => client.get<Agent>(agent(workspaceId, agentId)),
      create: (workspaceId: Uuid, body: AgentInput) =>
        client.post<Agent>(`${ws(workspaceId)}/agents`, body),
      update: (workspaceId: Uuid, agentId: Uuid, body: AgentUpdate) =>
        client.patch<Agent>(agent(workspaceId, agentId), body),
      publish: (workspaceId: Uuid, agentId: Uuid, revision: number) =>
        client.post<AgentVersion>(`${agent(workspaceId, agentId)}/publish`, { revision }),
      activate: (workspaceId: Uuid, agentId: Uuid, revision: number) =>
        client.post<Agent>(`${agent(workspaceId, agentId)}/activate`, { revision }),
      deactivate: (workspaceId: Uuid, agentId: Uuid, revision: number) =>
        client.post<Agent>(`${agent(workspaceId, agentId)}/deactivate`, { revision }),
      versions: (workspaceId: Uuid, agentId: Uuid, query: PageQuery = {}) =>
        client.get<Page<AgentVersion>>(`${agent(workspaceId, agentId)}/versions`, {
          query: { ...query },
        }),
    },

    proposals: {
      list: (workspaceId: Uuid, query: PageQuery & { status?: ProposalStatus } = {}) =>
        client.get<Page<Proposal>>(`${ws(workspaceId)}/agent-proposals`, { query: { ...query } }),
      submit: (workspaceId: Uuid, body: ProposalInput) =>
        client.post<Proposal>(`${ws(workspaceId)}/agent-proposals`, body),
      approve: (workspaceId: Uuid, proposalId: Uuid, note: string) =>
        client.post<Proposal>(`${ws(workspaceId)}/agent-proposals/${enc(proposalId)}/approve`, {
          note,
        }),
      reject: (workspaceId: Uuid, proposalId: Uuid, note: string) =>
        client.post<Proposal>(`${ws(workspaceId)}/agent-proposals/${enc(proposalId)}/reject`, {
          note,
        }),
    },

    workflows: {
      list: (workspaceId: Uuid, query: PageQuery & { status?: WorkflowStatus } = {}) =>
        client.get<Page<Workflow>>(`${ws(workspaceId)}/workflows`, { query: { ...query } }),
      get: (workspaceId: Uuid, workflowId: Uuid) => client.get<Workflow>(wf(workspaceId, workflowId)),
      /** `idempotencyKey` must stay the same when the same submission is retried. */
      create: (workspaceId: Uuid, body: WorkflowInput, idempotencyKey: string) =>
        client.post<Workflow>(`${ws(workspaceId)}/workflows`, body, {
          headers: { "Idempotency-Key": idempotencyKey },
        }),
      transition: (
        workspaceId: Uuid,
        workflowId: Uuid,
        body: { to_status: WorkflowStatus; revision: number; reason?: string },
      ) => client.post<Workflow>(`${wf(workspaceId, workflowId)}/transitions`, body),
      /** Requests the prompt analysis again after it failed; it runs in a worker. */
      retryAnalysis: (workspaceId: Uuid, workflowId: Uuid) =>
        client.post<Workflow>(`${wf(workspaceId, workflowId)}/analysis`),
      start: (workspaceId: Uuid, workflowId: Uuid, revision: number) =>
        client.post<Workflow>(`${wf(workspaceId, workflowId)}/start`, { revision }),
      cancel: (workspaceId: Uuid, workflowId: Uuid, revision: number, reason?: string) =>
        client.post<Workflow>(`${wf(workspaceId, workflowId)}/cancel`, { revision, reason }),
      events: (workspaceId: Uuid, workflowId: Uuid, query: { after?: number; limit?: number } = {}) =>
        client.get<EventPage>(`${wf(workspaceId, workflowId)}/events`, { query: { ...query } }),
    },

    tasks: {
      list: (workspaceId: Uuid, workflowId: Uuid, query: PageQuery = {}) =>
        client.get<Page<Task>>(`${wf(workspaceId, workflowId)}/tasks`, { query: { ...query } }),
      create: (workspaceId: Uuid, workflowId: Uuid, body: TaskInput) =>
        client.post<Task>(`${wf(workspaceId, workflowId)}/tasks`, body),
      update: (workspaceId: Uuid, workflowId: Uuid, taskId: Uuid, body: TaskUpdate) =>
        client.patch<Task>(task(workspaceId, workflowId, taskId), body),
      remove: (workspaceId: Uuid, workflowId: Uuid, taskId: Uuid, revision: number) =>
        client.delete<void>(task(workspaceId, workflowId, taskId), { query: { revision } }),
      addDependency: (workspaceId: Uuid, workflowId: Uuid, taskId: Uuid, dependsOn: Uuid) =>
        client.post<Task>(`${task(workspaceId, workflowId, taskId)}/dependencies`, {
          depends_on_task_id: dependsOn,
        }),
      removeDependency: (workspaceId: Uuid, workflowId: Uuid, taskId: Uuid, dependsOn: Uuid) =>
        client.delete<void>(`${task(workspaceId, workflowId, taskId)}/dependencies/${enc(dependsOn)}`),
      assign: (workspaceId: Uuid, workflowId: Uuid, taskId: Uuid, agentId: Uuid, revision: number) =>
        client.post<Task>(`${task(workspaceId, workflowId, taskId)}/assign`, {
          agent_id: agentId,
          revision,
        }),
      cancel: (workspaceId: Uuid, workflowId: Uuid, taskId: Uuid, revision: number, reason?: string) =>
        client.post<Task>(`${task(workspaceId, workflowId, taskId)}/cancel`, { revision, reason }),
      executions: (workspaceId: Uuid, workflowId: Uuid, taskId: Uuid) =>
        client.get<{ items: Execution[] }>(`${task(workspaceId, workflowId, taskId)}/executions`),
    },
  };
}

export type Endpoints = ReturnType<typeof createEndpoints>;
