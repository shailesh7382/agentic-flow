import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlignLeft,
  ArrowRight,
  Braces,
  ChartNoAxesCombined,
  Check,
  CheckCircle2,
  Clipboard,
  Copy,
  LoaderCircle,
  Map,
  PenLine,
  RotateCcw,
  Server,
  ShieldCheck,
  Sparkles,
  WandSparkles,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getHealth, getTasks, getTools, streamRun } from "./api";
import type { AgentEvent, RunResult, StepUpdate, TaskTemplate } from "./types";

const ICONS = {
  activity: Activity,
  "pen-line": PenLine,
  "chart-no-axes-combined": ChartNoAxesCombined,
  map: Map,
  braces: Braces,
  sparkles: Sparkles,
  "align-left": AlignLeft,
} as const;

const DEFAULT_FLOW = [
  { id: "intake", label: "Understanding the task" },
  { id: "plan", label: "Building an approach" },
  { id: "execute", label: "Creating the first draft" },
  { id: "critique", label: "Checking quality" },
  { id: "revise", label: "Applying improvements", optional: true },
  { id: "finalize", label: "Preparing the answer" },
];

const DIAGNOSTIC_FLOW = [
  { id: "intake", label: "Understanding the incident" },
  { id: "plan", label: "Planning evidence collection" },
  { id: "execute", label: "Running diagnostic tools" },
  { id: "critique", label: "Validating the diagnosis" },
  { id: "revise", label: "Correcting the diagnosis", optional: true },
  { id: "finalize", label: "Preparing the incident report" },
];

function App() {
  const tasksQuery = useQuery({ queryKey: ["tasks"], queryFn: getTasks });
  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 15_000,
  });
  const toolsQuery = useQuery({
    queryKey: ["tools"],
    queryFn: getTools,
    refetchInterval: 30_000,
  });
  const [selectedId, setSelectedId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [context, setContext] = useState("");
  const [constraints, setConstraints] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<StepUpdate[]>([]);
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const resultRef = useRef<HTMLElement | null>(null);

  const tasks = tasksQuery.data ?? [];
  const selected = useMemo(
    () => tasks.find((task) => task.id === selectedId) ?? tasks[0],
    [selectedId, tasks],
  );

  useEffect(() => {
    if (!selectedId && tasks[0]) setSelectedId(tasks[0].id);
  }, [selectedId, tasks]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const chooseTask = (task: TaskTemplate) => {
    if (running) return;
    setSelectedId(task.id);
    setResult(null);
    setSteps([]);
    setError("");
    if (!prompt.trim()) setPrompt(task.starter);
  };

  const handleEvent = (message: AgentEvent) => {
    if (message.event === "step") {
      setSteps((current) => {
        const withoutDuplicate = current.filter((step) => step.id !== message.data.id);
        return [...withoutDuplicate, message.data];
      });
    } else if (message.event === "result") {
      setResult(message.data);
    } else if (message.event === "error") {
      setError(message.data.message);
    }
  };

  const run = async () => {
    if (!selected || prompt.trim().length < 3 || running) return;
    setRunning(true);
    setResult(null);
    setSteps([]);
    setError("");
    setCopied(false);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamRun(
        {
          task_id: selected.id,
          prompt: prompt.trim(),
          context: context.trim(),
          constraints: constraints
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean),
        },
        handleEvent,
        controller.signal,
      );
    } catch (caught) {
      if (!controller.signal.aborted) {
        setError(caught instanceof Error ? caught.message : "The agent run failed.");
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
      window.setTimeout(() => resultRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    setRunning(false);
  };

  const reset = () => {
    setResult(null);
    setSteps([]);
    setError("");
    setPrompt(selected?.starter ?? "");
  };

  const copyAnswer = async () => {
    if (!result) return;
    await navigator.clipboard.writeText(result.answer);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  const connected = healthQuery.data?.lmstudio === "connected";
  const completedIds = new Set(steps.map((step) => step.id));
  const activeFlow = selected?.id === "diagnose" ? DIAGNOSTIC_FLOW : DEFAULT_FLOW;
  const currentIndex = Math.min(steps.length, activeFlow.length - 1);

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#" aria-label="Local Agent Studio home">
          <span className="brand-mark">
            <WandSparkles size={19} strokeWidth={2.25} />
          </span>
          <span>Local Agent Studio</span>
        </a>
        <div className={`status-pill ${connected ? "connected" : "offline"}`}>
          <span className="status-dot" />
          <span>{connected ? healthQuery.data?.model : "LM Studio offline"}</span>
          <Server size={14} />
        </div>
      </header>

      <main>
        <section className="workspace">
          <div className="task-section">
            <div className="section-heading">
              <span className="step-number">1</span>
              <div>
                <h2>Choose a task</h2>
                <p>Each template tunes the workflow for a different kind of outcome.</p>
              </div>
            </div>

            {tasksQuery.isError ? (
              <div className="inline-error">Could not load task templates. Is the API running?</div>
            ) : (
              <div className="task-grid">
                {tasks.map((task) => {
                  const Icon = ICONS[task.icon as keyof typeof ICONS] ?? Sparkles;
                  const active = selected?.id === task.id;
                  return (
                    <button
                      className={`task-card ${active ? "active" : ""}`}
                      key={task.id}
                      onClick={() => chooseTask(task)}
                      type="button"
                    >
                      <span className="task-icon">
                        <Icon size={20} />
                      </span>
                      <span className="task-copy">
                        <strong>{task.title}</strong>
                        <small>{task.description}</small>
                      </span>
                      <span className="task-check">{active && <Check size={15} />}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="composer-section">
            <div className="section-heading">
              <span className="step-number">2</span>
              <div>
                <h2>Describe the outcome</h2>
                <p>{selected?.output_hint ?? "Give the agent a clear target."}</p>
              </div>
            </div>

            {selected?.id === "diagnose" && (
              <div className="tool-status-panel">
                <div className="tool-status-heading">
                  <span>
                    <ShieldCheck size={17} />
                    Read-only diagnostic access
                  </span>
                  <small>
                    {(toolsQuery.data ?? []).filter((tool) => tool.enabled).length} enabled
                  </small>
                </div>
                <div className="tool-status-grid">
                  {(toolsQuery.data ?? []).map((tool) => (
                    <div className="tool-status-item" key={tool.name}>
                      <span
                        className={`tool-status-dot ${tool.enabled ? "enabled" : ""}`}
                        aria-hidden="true"
                      />
                      <span>
                        <strong>
                          {tool.name}
                          <em>{tool.access}</em>
                        </strong>
                        <small>{tool.detail}</small>
                      </span>
                    </div>
                  ))}
                </div>
                {toolsQuery.isError && (
                  <p className="tool-status-error">Tool availability could not be loaded.</p>
                )}
              </div>
            )}

            <div className="composer-card">
              <label htmlFor="prompt">{selected?.prompt_label ?? "What should the agent do?"}</label>
              <textarea
                id="prompt"
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder={selected?.placeholder}
                rows={7}
                disabled={running}
              />

              <button
                className="details-toggle"
                type="button"
                onClick={() => setShowDetails((value) => !value)}
                aria-expanded={showDetails}
              >
                <span>Context & constraints</span>
                <span>{showDetails ? "Hide" : "Optional"}</span>
              </button>

              {showDetails && (
                <div className="detail-fields">
                  <label>
                    Supporting context
                    <textarea
                      value={context}
                      onChange={(event) => setContext(event.target.value)}
                      placeholder="Background, source material, examples, or definitions…"
                      rows={4}
                      disabled={running}
                    />
                  </label>
                  <label>
                    Constraints <small>one per line</small>
                    <textarea
                      value={constraints}
                      onChange={(event) => setConstraints(event.target.value)}
                      placeholder={"Keep it under 600 words\nUse a direct, confident tone"}
                      rows={4}
                      disabled={running}
                    />
                  </label>
                </div>
              )}

              <div className="composer-footer">
                <div className="privacy-note">
                  <span className="privacy-icon">
                    <Server size={15} />
                  </span>
                  <span>
                    Uses the <strong>backend-configured model endpoint</strong>
                  </span>
                </div>
                {running ? (
                  <button className="stop-button" type="button" onClick={stop}>
                    <X size={17} /> Stop
                  </button>
                ) : (
                  <button
                    className="run-button"
                    type="button"
                    onClick={run}
                    disabled={!selected || prompt.trim().length < 3}
                  >
                    Run agent flow <ArrowRight size={18} />
                  </button>
                )}
              </div>
            </div>
          </div>

          {(running || steps.length > 0 || error) && (
            <section className="run-panel" aria-live="polite">
              <div className="run-panel-header">
                <div>
                  <span className="run-kicker">{running ? "Agent flow in progress" : "Agent flow"}</span>
                  <h2>{running ? "Working through your request" : error ? "Run interrupted" : "Run complete"}</h2>
                </div>
                {running && <LoaderCircle className="spinner" size={24} />}
              </div>

              <div className="flow-track">
                {activeFlow.filter((item) => !item.optional || completedIds.has(item.id)).map(
                  (item, index, shownFlow) => {
                    const complete = completedIds.has(item.id);
                    const active = running && !complete && index === currentIndex;
                    return (
                      <div className="flow-item-wrap" key={item.id}>
                        <div className={`flow-item ${complete ? "complete" : ""} ${active ? "active" : ""}`}>
                          <span className="flow-dot">
                            {complete ? (
                              <Check size={13} />
                            ) : active ? (
                              <LoaderCircle className="spinner" size={13} />
                            ) : (
                              index + 1
                            )}
                          </span>
                          <span>{item.label}</span>
                        </div>
                        {index < shownFlow.length - 1 && <span className="flow-line" />}
                      </div>
                    );
                  },
                )}
              </div>

              {error && (
                <div className="error-box">
                  <X size={18} />
                  <div>
                    <strong>Couldn’t finish this run</strong>
                    <p>{error}</p>
                  </div>
                </div>
              )}
            </section>
          )}

          {result && (
            <section className="result-section" ref={resultRef}>
              <div className="result-header">
                <div>
                  <div className="result-label">
                    <CheckCircle2 size={16} /> Final answer
                  </div>
                  <h2>{selected?.title} complete</h2>
                </div>
                <div className="result-actions">
                  <button type="button" onClick={copyAnswer}>
                    {copied ? <Check size={16} /> : <Copy size={16} />}
                    {copied ? "Copied" : "Copy"}
                  </button>
                  <button type="button" onClick={reset}>
                    <RotateCcw size={16} /> New run
                  </button>
                </div>
              </div>

              <div className="result-layout">
                <article className="answer-card markdown-body">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.answer}</ReactMarkdown>
                </article>
                <aside className="run-summary">
                  <div className="summary-card">
                    <span className="summary-title">
                      <Clipboard size={15} /> Run summary
                    </span>
                    <dl>
                      <div>
                        <dt>Model</dt>
                        <dd>{result.model}</dd>
                      </div>
                      <div>
                        <dt>Quality gate</dt>
                        <dd className="pass">{result.critique.verdict}</dd>
                      </div>
                      <div>
                        <dt>Revisions</dt>
                        <dd>{result.revisions}</dd>
                      </div>
                    </dl>
                  </div>
                  <details className="summary-card">
                    <summary>Agent plan</summary>
                    <ol className="plan-list">
                      {result.plan.map((step) => (
                        <li key={step.title}>
                          <strong>{step.title}</strong>
                          <span>{step.purpose}</span>
                        </li>
                      ))}
                    </ol>
                  </details>
                  <details className="summary-card">
                    <summary>Quality review</summary>
                    <p>{result.critique.summary}</p>
                  </details>
                </aside>
              </div>
            </section>
          )}
        </section>
      </main>

      <footer>
        <span>Local Agent Studio</span>
        <span>FastAPI · LangGraph · LM Studio · React</span>
      </footer>
    </div>
  );
}

export default App;
