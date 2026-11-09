"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { controlProps, FormError, FormField } from "@/components/common/form-field";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { describeError } from "@/lib/api/errors";
import { useAuth } from "@/lib/auth/auth-provider";
import { homePath } from "@/lib/navigation";

import { AuthCard } from "./auth-card";

const schema = z.object({
  email: z.email("Enter a valid email address.").max(320),
  password: z.string().min(1, "Enter your password.").max(128),
});
type Values = z.infer<typeof schema>;

export function LoginView() {
  const { login, status, me, sessionExpired } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next");
  const expired = params.get("expired") === "1" || sessionExpired;
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { email: "", password: "" } });
  const { errors, isSubmitting } = form.formState;

  useEffect(() => {
    if (status === "authenticated" && me) router.replace(homePath(me, next));
  }, [status, me, next, router]);

  async function onSubmit(values: Values) {
    setFormError(null);
    try {
      const session = await login(values.email, values.password);
      router.replace(homePath(session, next));
    } catch (error) {
      setFormError(describeError(error));
      form.setFocus("password");
    }
  }

  return (
    <AuthCard
      title="Sign in"
      description="Use your AgentFlow account."
      footer={
        <>
          New to AgentFlow?{" "}
          <Link href="/register" className="font-medium text-foreground underline underline-offset-4">
            Create an account
          </Link>
        </>
      }
    >
      <form className="grid gap-4" onSubmit={form.handleSubmit(onSubmit)} noValidate>
        {expired ? (
          <Alert>
            <AlertTitle>Your session expired</AlertTitle>
            <AlertDescription>Sign in again to continue where you left off.</AlertDescription>
          </Alert>
        ) : null}
        <FormError message={formError} />
        <FormField id="email" label="Email" error={errors.email?.message}>
          <Input
            type="email"
            autoComplete="email"
            autoFocus
            {...controlProps("email", errors.email?.message)}
            {...form.register("email")}
          />
        </FormField>
        <FormField id="password" label="Password" error={errors.password?.message}>
          <Input
            type="password"
            autoComplete="current-password"
            {...controlProps("password", errors.password?.message)}
            {...form.register("password")}
          />
        </FormField>
        <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting}>
          {isSubmitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </AuthCard>
  );
}
