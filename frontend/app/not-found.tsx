import Link from "next/link";

export default function NotFound() {
  return (
    <main id="main-content" className="flex min-h-svh flex-col items-center justify-center gap-3 p-6 text-center">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="text-sm text-muted-foreground">This page does not exist or you do not have access to it.</p>
      <Link href="/" className="text-sm underline underline-offset-4">
        Go to AgentFlow
      </Link>
    </main>
  );
}
