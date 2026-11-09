"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { controlProps, FormError, FormField } from "@/components/common/form-field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { describeError } from "@/lib/api/errors";
import { useAuth } from "@/lib/auth/auth-provider";
import { homePath } from "@/lib/navigation";

import { AuthCard } from "./auth-card";

// Mirrors RegisterRequest: 12-128 characters, not blank; workspace name optional, 1-200.
export const registerSchema = z
  .object({
    email: z.email("Enter a valid email address.").max(320),
    password: z
      .string()
      .min(12, "Use at least 12 characters.")
      .max(128, "Use at most 128 characters.")
      .refine((v) => v.trim().length > 0, "The password must not be blank."),
    confirmPassword: z.string(),
    workspaceName: z.string().trim().max(200, "Use at most 200 characters."),
  })
  .refine((v) => v.password === v.confirmPassword, {
    path: ["confirmPassword"],
    message: "The passwords do not match.",
  });
type Values = z.infer<typeof registerSchema>;

export function RegisterView() {
  const { register: registerAccount, status, me } = useAuth();
  const router = useRouter();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver(registerSchema),
    defaultValues: { email: "", password: "", confirmPassword: "", workspaceName: "" },
  });
  const { errors, isSubmitting } = form.formState;

  useEffect(() => {
    if (status === "authenticated" && me && !form.formState.isSubmitSuccessful) {
      router.replace(homePath(me));
    }
  }, [status, me, router, form.formState.isSubmitSuccessful]);

  async function onSubmit(values: Values) {
    setFormError(null);
    try {
      const session = await registerAccount(
        values.email,
        values.password,
        values.workspaceName || undefined,
      );
      toast.success("Account created");
      router.replace(homePath(session));
    } catch (error) {
      setFormError(describeError(error));
    }
  }

  return (
    <AuthCard
      title="Create your account"
      description="You get a personal workspace where you are the owner."
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-foreground underline underline-offset-4">
            Sign in
          </Link>
        </>
      }
    >
      <form className="grid gap-4" onSubmit={form.handleSubmit(onSubmit)} noValidate>
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
        <FormField
          id="password"
          label="Password"
          error={errors.password?.message}
          hint="At least 12 characters."
        >
          <Input
            type="password"
            autoComplete="new-password"
            {...controlProps("password", errors.password?.message, true)}
            {...form.register("password")}
          />
        </FormField>
        <FormField id="confirmPassword" label="Confirm password" error={errors.confirmPassword?.message}>
          <Input
            type="password"
            autoComplete="new-password"
            {...controlProps("confirmPassword", errors.confirmPassword?.message)}
            {...form.register("confirmPassword")}
          />
        </FormField>
        <FormField
          id="workspaceName"
          label="Workspace name (optional)"
          error={errors.workspaceName?.message}
          hint='Defaults to "Personal workspace".'
        >
          <Input
            autoComplete="organization"
            {...controlProps("workspaceName", errors.workspaceName?.message, true)}
            {...form.register("workspaceName")}
          />
        </FormField>
        <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting}>
          {isSubmitting ? "Creating account…" : "Create account"}
        </Button>
      </form>
    </AuthCard>
  );
}
