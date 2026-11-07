import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import HomePage from "@/app/page";

afterEach(cleanup);

test("renders the AgentFlow heading", () => {
  render(<HomePage />);

  expect(screen.getByRole("heading", { level: 1, name: "AgentFlow" })).toBeDefined();
});

test("states that product features are not available yet", () => {
  render(<HomePage />);

  expect(screen.getByRole("status").textContent).toContain("not available yet");
});
