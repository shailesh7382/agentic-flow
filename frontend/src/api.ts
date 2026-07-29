import type { AgentEvent, Health, RunPayload, TaskTemplate } from "./types";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const getTasks = () => getJson<TaskTemplate[]>("/api/tasks");
export const getHealth = () => getJson<Health>("/api/health");

function decodeEvent(block: string): AgentEvent | null {
  const lines = block.split(/\r?\n/);
  const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
  const data = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");

  if (!event || !data) return null;
  return { event, data: JSON.parse(data) } as AgentEvent;
}

export async function streamRun(
  payload: RunPayload,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(error?.detail ?? `Request failed with status ${response.status}`);
  }
  if (!response.body) {
    throw new Error("This browser does not support streamed responses.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";

    for (const block of blocks) {
      const parsed = decodeEvent(block);
      if (parsed) onEvent(parsed);
    }
    if (done) break;
  }

  if (buffer.trim()) {
    const parsed = decodeEvent(buffer);
    if (parsed) onEvent(parsed);
  }
}

