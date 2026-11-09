import { startBackend, stopTree } from "./support/stack";

export default async function globalSetup(): Promise<() => void> {
  const processes = await startBackend();
  return () => processes.forEach(stopTree);
}
