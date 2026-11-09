import type { Metadata } from "next";

import { RegisterView } from "@/features/auth/register-view";

export const metadata: Metadata = { title: "Create account" };

export default function RegisterPage() {
  return <RegisterView />;
}
