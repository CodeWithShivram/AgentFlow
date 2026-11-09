"use client";

import { BellIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Fragment } from "react";

import { ErrorState } from "@/components/common/states";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { workspacePath } from "@/lib/navigation";
import { shortId } from "@/lib/format";
import {
  type CurrentWorkspace,
  useResolveWorkspace,
  useWorkspaceIdParam,
  WorkspaceProvider,
} from "@/lib/workspace";

import { AccountMenu } from "./account-menu";
import { AppSidebar } from "./app-sidebar";
import { FullPageSkeleton, RequireAuth } from "./require-auth";
import { ThemeToggle } from "./theme-toggle";

export function WorkspaceShell({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <ResolvedWorkspace>{children}</ResolvedWorkspace>
    </RequireAuth>
  );
}

function ResolvedWorkspace({ children }: { children: React.ReactNode }) {
  const workspaceId = useWorkspaceIdParam();
  const { workspace, error, isLoading, refetch } = useResolveWorkspace(workspaceId);
  if (workspace) {
    return (
      <WorkspaceProvider value={workspace}>
        <SidebarProvider>
          <AppSidebar />
          <SidebarInset>
            <ShellHeader workspace={workspace} />
            <main id="main-content" tabIndex={-1} className="flex-1 p-4 outline-none md:p-6">
              <div className="mx-auto w-full max-w-6xl">{children}</div>
            </main>
          </SidebarInset>
        </SidebarProvider>
      </WorkspaceProvider>
    );
  }
  if (isLoading) return <FullPageSkeleton />;
  return (
    <PlainShell>
      <ErrorState error={error} onRetry={() => void refetch()} />
      <p className="mt-4 text-center text-sm">
        <Link className="underline underline-offset-4" href="/">
          Go to your workspaces
        </Link>
      </p>
    </PlainShell>
  );
}

/** Header-only layout for pages outside a workspace (account, invitations, errors). */
export function PlainShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-svh flex-col">
      <header className="flex h-14 items-center gap-2 border-b px-4">
        <Link href="/" className="font-semibold">
          AgentFlow
        </Link>
        <div className="ml-auto flex items-center gap-1">
          <ThemeToggle />
          <AccountMenu />
        </div>
      </header>
      <main id="main-content" tabIndex={-1} className="flex-1 p-4 outline-none md:p-6">
        <div className="mx-auto w-full max-w-3xl">{children}</div>
      </main>
    </div>
  );
}

const SECTION_LABELS: Record<string, string> = {
  dashboard: "Dashboard",
  workflows: "Workflows",
  agents: "Agents",
  proposals: "Agent proposals",
  activity: "Activity",
  members: "Members",
  settings: "Settings",
};

const DETAIL_LABELS: Record<string, string> = { workflows: "Workflow", agents: "Agent" };

function ShellHeader({ workspace }: { workspace: CurrentWorkspace }) {
  const pathname = usePathname();
  // /w/<id>/<section>/<detail>
  const [, , , section, detail] = pathname.split("/");
  const crumbs: { label: string; href?: string }[] = [
    { label: workspace.name, href: workspacePath(workspace.id) },
  ];
  if (section && SECTION_LABELS[section]) {
    crumbs.push({ label: SECTION_LABELS[section], href: workspacePath(workspace.id, section) });
    if (detail) {
      crumbs.push({
        label: detail === "new" ? "New" : `${DETAIL_LABELS[section] ?? "Item"} ${shortId(detail)}`,
      });
    }
  }
  const current = crumbs.length - 1;

  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-background/95 px-4 backdrop-blur">
      <SidebarTrigger aria-label="Toggle navigation" />
      <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-5" />
      <Breadcrumb className="min-w-0">
        <BreadcrumbList className="flex-nowrap">
          {crumbs.map((crumb, index) => (
            <Fragment key={`${index}-${crumb.label}`}>
              {index > 0 ? <BreadcrumbSeparator className="hidden sm:block" /> : null}
              <BreadcrumbItem className={index < current ? "hidden truncate sm:inline-flex" : "truncate"}>
                {index === current || !crumb.href ? (
                  <BreadcrumbPage className="truncate">{crumb.label}</BreadcrumbPage>
                ) : (
                  <BreadcrumbLink asChild>
                    <Link href={crumb.href}>{crumb.label}</Link>
                  </BreadcrumbLink>
                )}
              </BreadcrumbItem>
            </Fragment>
          ))}
        </BreadcrumbList>
      </Breadcrumb>
      <div className="ml-auto flex items-center gap-1">
        <Button variant="ghost" size="icon" asChild>
          <Link href={workspacePath(workspace.id, "activity")} aria-label="Recent activity">
            <BellIcon />
          </Link>
        </Button>
        <ThemeToggle />
        <AccountMenu />
      </div>
    </header>
  );
}
