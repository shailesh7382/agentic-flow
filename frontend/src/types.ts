export type TaskTemplate = {
  id: string;
  title: string;
  description: string;
  icon: string;
  prompt_label: string;
  placeholder: string;
  starter: string;
  output_hint: string;
};

export type Health = {
  status: "ok";
  lmstudio: "connected" | "unavailable";
  model: string | null;
  detail: string | null;
};

export type PlanStep = {
  title: string;
  purpose: string;
};

export type Critique = {
  verdict: "pass" | "revise";
  summary: string;
  issues: string[];
};

export type RunResult = {
  run_id: string;
  task_id: string;
  model: string;
  objective: string;
  plan: PlanStep[];
  critique: Critique;
  answer: string;
  revisions: number;
};

export type StepUpdate = {
  id: string;
  label: string;
  status: "completed";
};

export type RunPayload = {
  task_id: string;
  prompt: string;
  context: string;
  constraints: string[];
};

export type AgentEvent =
  | { event: "run"; data: { run_id: string; task_id: string } }
  | { event: "step"; data: StepUpdate }
  | { event: "result"; data: RunResult }
  | { event: "error"; data: { run_id: string; message: string } }
  | { event: "done"; data: { run_id: string } };

