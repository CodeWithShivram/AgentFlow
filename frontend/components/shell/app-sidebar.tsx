"use client";

import {
  ActivityIcon,
  BotIcon,
  CheckIcon,
  ChevronsUpDownIcon,
  ClipboardCheckIcon,
  LayoutDashboardIcon,
  SettingsIcon,
  UsersIcon,
  WorkflowIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { useAuth } from "@/lib/auth/auth-provider";
import { workspacePath } from "@/lib/navigation";
import { ROLE_LABELS } from "@/lib/roles";
import { useWorkspace } from "@/lib/workspace";

const MAIN_NAV = [
  { section: "dashboard", label: "Dashboard", icon: LayoutDashboardIcon },
  { section: "workflows", label: "Workflows", icon: WorkflowIcon },
  { section: "agents", label: "Agents", icon: BotIcon },
  { section: "proposals", label: "Agent proposals", icon: ClipboardCheckIcon },
  { section: "activity", label: "Activity", icon: ActivityIcon },
] as const;

const WORKSPACE_NAV = [
  { section: "members", label: "Members", icon: UsersIcon },
  { section: "settings", label: "Settings", icon: SettingsIcon },
] as const;

export function AppSidebar() {
  const workspace = useWorkspace();
  const pathname = usePathname();
  const { setOpenMobile } = useSidebar();

  const item = ({ section, label, icon: Icon }: (typeof MAIN_NAV | typeof WORKSPACE_NAV)[number]) => {
    const href = workspacePath(workspace.id, section);
    const active = pathname === href || pathname.startsWith(`${href}/`);
    return (
      <SidebarMenuItem key={section}>
        <SidebarMenuButton asChild isActive={active} tooltip={label}>
          <Link
            href={href}
            aria-current={active ? "page" : undefined}
            onClick={() => setOpenMobile(false)}
          >
            <Icon />
            <span>{label}</span>
          </Link>
        </SidebarMenuButton>
      </SidebarMenuItem>
    );
  };

  return (
    <Sidebar collapsible="icon" aria-label="Main navigation">
      <SidebarHeader>
        <WorkspaceSwitcher />
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Orchestration</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>{MAIN_NAV.map(item)}</SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        <SidebarGroup>
          <SidebarGroupLabel>Workspace</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>{WORKSPACE_NAV.map(item)}</SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <p className="px-2 text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
          Your role: {ROLE_LABELS[workspace.role]}
        </p>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}

function WorkspaceSwitcher() {
  const workspace = useWorkspace();
  const { me } = useAuth();
  const router = useRouter();
  const { setOpenMobile } = useSidebar();
  const memberships = me?.memberships ?? [];

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton size="lg" aria-label={`Workspace: ${workspace.name}. Switch workspace`}>
              <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                <WorkflowIcon className="size-4" />
              </div>
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-semibold">{workspace.name}</span>
                <span className="truncate text-xs text-muted-foreground">AgentFlow</span>
              </div>
              <ChevronsUpDownIcon className="ml-auto" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-64">
            <DropdownMenuLabel>Workspaces</DropdownMenuLabel>
            {memberships.map((m) => (
              <DropdownMenuItem
                key={m.workspace_id}
                onSelect={() => {
                  setOpenMobile(false);
                  router.push(workspacePath(m.workspace_id));
                }}
              >
                <span className="flex-1 truncate">{m.workspace_name}</span>
                <span className="text-xs text-muted-foreground">{ROLE_LABELS[m.role]}</span>
                {m.workspace_id === workspace.id ? (
                  <CheckIcon aria-label="Current workspace" />
                ) : null}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/invitations/accept">Join with an invitation…</Link>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
