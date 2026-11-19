import { describe, expect, it } from "vitest";

import type { Task } from "@/lib/api/types";

import { criticalPath, downstream, layoutGraph, upstream } from "./graph";

function task(id: string, dependsOn: string[] = [], extra: Partial<Task> = {}): Task {
  return {
    id,
    title: id.toUpperCase(),
    depends_on: dependsOn,
    priority: 0,
    required: true,
    removed: false,
    status: "PENDING",
    ...extra,
  } as Task;
}

// a → b → d, a → c → d, e alone
const DIAMOND = [task("a"), task("b", ["a"]), task("c", ["a"]), task("d", ["b", "c"]), task("e")];

describe("upstream and downstream", () => {
  it("follow dependencies transitively", () => {
    expect([...upstream(DIAMOND, "d")].sort()).toEqual(["a", "b", "c"]);
    expect([...downstream(DIAMOND, "a")].sort()).toEqual(["b", "c", "d"]);
    expect(upstream(DIAMOND, "e").size).toBe(0);
    expect(downstream(DIAMOND, "d").size).toBe(0);
  });
});

describe("criticalPath", () => {
  it("is the longest chain of unfinished required tasks", () => {
    // Ties between b and c go to priority, then title.
    expect(criticalPath(DIAMOND)).toEqual(["a", "b", "d"]);
    const preferC = DIAMOND.map((t) => (t.id === "c" ? { ...t, priority: 5 } : t));
    expect(criticalPath(preferC)).toEqual(["a", "c", "d"]);
  });

  it("leaves out finished, optional and removed tasks", () => {
    const done = DIAMOND.map((t) => (t.id === "a" ? { ...t, status: "COMPLETED" as const } : t));
    expect(criticalPath(done)).toEqual(["b", "d"]);
    const optional = DIAMOND.map((t) => (t.id === "d" ? { ...t, required: false } : t));
    expect(criticalPath(optional)).toEqual(["a", "b"]);
    const finished = DIAMOND.map((t) => ({ ...t, status: "COMPLETED" as const }));
    expect(criticalPath(finished)).toEqual([]);
  });

  it("stays finite on a cycle the API would have refused", () => {
    expect(criticalPath([task("x", ["y"]), task("y", ["x"])]).length).toBeLessThanOrEqual(2);
  });
});

describe("layoutGraph", () => {
  it("places tasks in stages and draws one edge per dependency", () => {
    const layout = layoutGraph(DIAMOND);
    expect(layout.stages.map((s) => s.map((t) => t.id))).toEqual([["a", "e"], ["b", "c"], ["d"]]);
    expect(layout.edges).toEqual([
      { from: "a", to: "b" },
      { from: "a", to: "c" },
      { from: "b", to: "d" },
      { from: "c", to: "d" },
    ]);
    const d = layout.nodes.find((n) => n.task.id === "d")!;
    expect([d.stage, d.row]).toEqual([2, 0]);
  });

  it("orders a stage by the rows of its dependencies to keep edges short", () => {
    // z sits under y in stage 1; their dependents follow that order, not their titles.
    const tasks = [task("y"), task("z", [], { priority: -1 }), task("p", ["z"]), task("q", ["y"])];
    const layout = layoutGraph(tasks);
    expect(layout.stages[0]!.map((t) => t.id)).toEqual(["y", "z"]);
    expect(layout.stages[1]!.map((t) => t.id)).toEqual(["q", "p"]);
  });
});
