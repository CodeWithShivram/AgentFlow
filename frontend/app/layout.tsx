import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentFlow",
  description: "Multi-agent AI orchestration platform",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>
        <header className="app-header">
          <span className="app-brand">AgentFlow</span>
        </header>
        <main className="app-main">{children}</main>
      </body>
    </html>
  );
}
