# Local Agent Studio

A software and platform diagnostics console built around a deliberate agent workflow. It can
inspect allowlisted REST endpoints, query Oracle through a read-only SQL gate, collect Unix host
and service state, and search bounded log windows before producing an evidence-backed diagnosis.
General writing, analysis, planning, coding, brainstorming, and summarization workflows remain
available.

The default language-model endpoint is [LM Studio](https://lmstudio.ai/) on the local machine.
Any HTTP or HTTPS OpenAI-compatible chat-completions endpoint can also be configured. The
application itself is a FastAPI/Uvicorn API with LangGraph orchestration, a LangChain diagnostics
agent, exhaustive JSONL logging, and a responsive React/Vite interface.

## Quick start

### 1. Start a model in LM Studio

1. Install and open LM Studio.
2. Download and load a chat/instruct model that fits your machine.
3. Open **Developer**, start the local server, and keep the default
   `http://127.0.0.1:1234` address.

The backend automatically uses the first loaded model. To force a particular model ID, set
`LMSTUDIO_MODEL` in `backend/.env`. See [Model endpoints](#model-endpoints) for a remote HTTPS
provider.

### 2. Install and run

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), and Node.js 22+.

```bash
./scripts/setup.sh
./scripts/dev.sh
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). The header changes from
**LM Studio offline** to the loaded model ID when the connection is ready.

`setup.sh` creates `backend/.env`, resolves the locked Python environment with uv, and installs
the frontend packages. `dev.sh` runs both development servers and stops both when you press
Ctrl+C.

## What you can run

The UI includes seven prompt templates:

| Template | Best for | Result shape |
| --- | --- | --- |
| Software diagnostics | Service incidents, API failures, Oracle symptoms, Unix health, and logs | Evidence, ranked causes, confidence, and next checks |
| Write & refine | Announcements, briefs, copy, and explanations | Publication-ready Markdown |
| Analyze | Decisions, documents, situations, and trade-offs | Findings, risks, and recommendation |
| Make a plan | Projects, pilots, migrations, and launches | Phases, milestones, owners, and measures |
| Technical copilot | Design, code review, explanation, and troubleshooting | Precise technical guidance |
| Brainstorm | Product, process, and workflow ideas | Clustered and ranked ideas |
| Summarize | Notes, reports, and source material | Audience-aware decisions and actions |

Add supporting context and one constraint per line when the output needs tighter boundaries.

## Architecture

```mermaid
flowchart LR
    U["Browser user"] -->|REST + streamed events| V["React 19 + Vite 8"]
    V -->|"/api/tasks, /api/tools, /api/health"| F["FastAPI + Uvicorn"]
    V -->|"POST /api/runs (SSE)"| F
    F --> G["LangGraph workflow"]
    G -->|"general tasks"| C["OpenAI-compatible client"]
    G -->|"diagnose"| A["LangChain diagnostic agent"]
    A --> R["Capability-scoped tool registry"]
    R --> O["Oracle SELECT"]
    R --> H["Generic allowlisted HTTP(S) GET / HEAD"]
    R --> T["CSV-defined GET / templated POST tools"]
    R --> X["Local or host-bound SSH diagnostics"]
    R --> Z["Bounded log read / search / secure copy"]
    C --> E["Configured model endpoint"]
    A --> E
    E --> L["LM Studio or remote OpenAI-compatible model"]

    style V fill:#eff6ff,stroke:#64748b
    style F fill:#dbeafe,stroke:#475569
    style G fill:#eff6ff,stroke:#2563eb
    style A fill:#dbeafe,stroke:#2563eb
    style R fill:#f8fafc,stroke:#64748b
    style E fill:#eff6ff,stroke:#475569
```

The browser never receives model, Oracle, or SSH credentials. It talks only to FastAPI. FastAPI
owns endpoint configuration, prompts, graph state, tool policy, validation, error normalization,
logging, and the streamed run contract.

## Agent flow

```mermaid
flowchart TD
    A["Intake agent<br/>turn request into an objective"] --> B["Planning agent<br/>create 3–6 execution steps"]
    B --> C["Execution agent<br/>produce a complete draft"]
    C --> D["Critic agent<br/>check usefulness and correctness"]
    D -->|"pass"| F["Final editor<br/>remove meta-commentary and polish"]
    D -->|"material issues"| E["Revision agent<br/>apply every review issue"]
    E --> D
    D -->|"revision limit reached"| F
    F --> G["Stream final Markdown result"]
```

The quality gate is deterministic: the critic returns `pass` or `revise`, and LangGraph chooses
the next edge. `AGENT_MAX_REVISIONS` caps the review loop, preventing an accidental infinite run.
The default is one revision.

For the **Software diagnostics** template, the execution node contains a second bounded agent loop:

```mermaid
flowchart TD
    I["Incident objective and proposed checks"] --> A["LangChain create_agent"]
    A --> D{"Enough evidence?"}
    D -->|"No"| T{"Choose an enabled tool"}
    T -->|"database"| O["oracle_select"]
    T -->|"API"| R["Generic or CSV-defined REST tool"]
    T -->|"host"| S["Snapshot / disks / processes"]
    T -->|"service"| V["Bound service-status tool"]
    T -->|"logs"| L["Tail / search / fetch tool"]
    O --> A
    R --> A
    S --> A
    V --> A
    L --> A
    D -->|"Yes or access unavailable"| P["Evidence-backed diagnosis"]
    P --> Q["LangGraph quality review and final edit"]
```

LangGraph controls the overall lifecycle and quality gate. LangChain provides model/tool
selection within the diagnostic execution stage. The configured
`DIAGNOSTICS_MAX_ITERATIONS` and LangGraph recursion limit bound that inner loop.

## Request lifecycle

```mermaid
sequenceDiagram
    participant UI as React UI
    participant API as FastAPI
    participant Graph as LangGraph
    participant Agent as LangChain diagnostics agent
    participant Tool as Capability-scoped diagnostic tool
    participant LM as Model endpoint

    UI->>API: POST /api/runs
    API-->>UI: event: run
    Graph->>LM: intake and investigation plan
    LM-->>Graph: objective and plan
    Graph->>Agent: execute diagnostic investigation
    loop Until sufficient evidence or iteration limit
        Agent->>LM: observations and available tools
        LM-->>Agent: tool call or final diagnosis
        Agent->>Tool: validated read-only operation
        Tool-->>Agent: bounded structured observation
    end
    Agent-->>Graph: diagnostic report
    loop Review and final edit
        Graph->>LM: OpenAI-compatible chat completion
        LM-->>Graph: model response
        Graph-->>UI: event: step
    end
    Graph-->>UI: event: result
    API-->>UI: event: done
```

Server-Sent Events are used over the streaming `fetch()` response. This keeps the protocol
simple, works through the Vite development proxy, and lets the UI show completed graph stages
without polling.

## Project layout

```text
.
├── SAMPLE_INTERACTION.md   # Annotated prompts and responses from a real run
├── backend/
│   ├── app/
│   │   ├── config.py       # Environment-backed settings
│   │   ├── diagnostics.py  # LangChain diagnostic agent and tracing callbacks
│   │   ├── graph.py        # LangGraph nodes, routing, and stream
│   │   ├── llm.py          # OpenAI-compatible model discovery and completions
│   │   ├── logging_config.py # JSONL logging, rotation, and correlation context
│   │   ├── main.py         # FastAPI app and SSE endpoint
│   │   ├── models.py       # Validated API and workflow models
│   │   ├── templates.py    # Task templates
│   │   └── tools/
│   │       ├── oracle.py   # Parsed, row-capped SELECT / WITH queries
│   │       ├── rest_api.py # Allowlisted GET / HEAD requests
│   │       ├── rest_catalog.py # CSV and JSON-templated GET / POST tools
│   │       ├── unix.py     # Fixed local and SSH inspection operations
│   │       ├── unix_catalog.py # CSV-defined host inventory
│   │       └── registry.py # Tool enablement and public status
│   ├── config/
│   │   ├── rest-tools.csv  # One row per separately registered REST tool
│   │   ├── rest-templates/ # Typed path, query, header, and JSON body templates
│   │   └── unix-hosts.csv  # SSH aliases and enabled host capabilities
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── App.tsx         # Task console, progress, and result UI
│   │   ├── api.ts          # REST and SSE client
│   │   └── index.css       # Responsive visual system
│   └── package.json
└── scripts/
    ├── setup.sh
    ├── dev.sh
    ├── backend.sh
    └── frontend.sh
```

## Configuration

Edit `backend/.env` after running setup:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LMSTUDIO_BASE_URL` | `http://127.0.0.1:1234/v1` | LM Studio OpenAI-compatible API |
| `LMSTUDIO_API_KEY` | `lm-studio` | Placeholder key expected by the client |
| `LMSTUDIO_MODEL` | empty | Model ID override; empty enables automatic discovery |
| `AGENT_TEMPERATURE` | `0.35` | Default generation temperature |
| `AGENT_MAX_TOKENS` | `1800` | Per-agent response ceiling |
| `AGENT_MAX_REVISIONS` | `1` | Maximum critic/revision loops |
| `CORS_ORIGINS` | local Vite origins | Comma-separated allowed browser origins |
| `DIAGNOSTICS_ENABLED` | `true` | Master switch for all diagnostic tools |
| `DIAGNOSTICS_MAX_ITERATIONS` | `8` | Maximum model/tool investigation iterations |
| `DIAGNOSTICS_MAX_CONFIGURED_TOOLS` | `64` | Maximum registered tools, including generated ones |
| `DIAGNOSTICS_TOOL_TIMEOUT_SECONDS` | `30` | Per-tool timeout |
| `DIAGNOSTICS_REST_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Exact hosts or `*.domain` patterns |
| `DIAGNOSTICS_REST_MAX_RESPONSE_BYTES` | `1048576` | Maximum HTTP response body retained |
| `DIAGNOSTICS_REST_HEADERS_JSON` | `{}` | Operator-supplied headers; use secrets carefully |
| `DIAGNOSTICS_REST_TOOLS_CSV` | `config/rest-tools.csv` | REST operation catalog |
| `DIAGNOSTICS_REST_TEMPLATE_ROOT` | `config/rest-templates` | Permitted JSON-template directory |
| `ORACLE_DSN` | empty | Oracle Easy Connect string or configured alias |
| `ORACLE_USER` | empty | Oracle diagnostic account |
| `ORACLE_PASSWORD` | empty | Oracle diagnostic account password |
| `ORACLE_MAX_ROWS` | `200` | Hard row cap for Oracle results |
| `DIAGNOSTICS_LOCAL_LOG_ROOTS` | `logs,/var/log` | Local paths the log tools may inspect |
| `DIAGNOSTICS_MAX_LOG_BYTES` | `2097152` | Maximum trailing log window searched |
| `DIAGNOSTICS_UNIX_HOSTS_JSON` | `{}` | Named SSH targets and their allowed log roots |
| `DIAGNOSTICS_UNIX_HOSTS_CSV` | `config/unix-hosts.csv` | CSV SSH host inventory |
| `DIAGNOSTICS_LOG_DOWNLOAD_DIR` | `logs/collected` | Server-controlled destination for fetched logs |
| `DIAGNOSTICS_SCP_MAX_BYTES` | `10485760` | Maximum trailing bytes copied from one remote log |
| `LOG_LEVEL` | `DEBUG` | Minimum severity written to the log file |
| `LOG_FILE` | `logs/agentic-flow.log` | Absolute path or path relative to `backend/` |
| `LOG_MAX_BYTES` | `10485760` | Rotate the active log after this many bytes |
| `LOG_BACKUP_COUNT` | `5` | Number of rotated log files to retain |
| `LOG_INCLUDE_CONTENT` | `true` | Include prompts, context, drafts, and answers |

The planning and critic stages use lower temperatures in code so their JSON decisions remain
stable. They also use LM Studio's JSON-schema-constrained structured output, followed by Pydantic
validation. The execution and revision stages use the configured creative temperature.

## Diagnostics configuration

The diagnostic agent receives only tools enabled by backend configuration. The UI shows their
current status and access mode when **Software diagnostics** is selected, and `GET /api/tools`
exposes the same status to operators. Access modes are `read-only`,
`operator-templated-write`, and `local-copy`.

### REST API inspection

There are two REST modes:

1. `rest_api_read` supports generic `GET` and `HEAD` against a hostname allowlist.
2. The CSV catalog creates a distinct LangChain tool for every enabled `GET` or `POST` row.

The generic tool never performs POST. Redirects are returned as observations but never followed,
credentials embedded in URLs are rejected, and every destination must match the host allowlist.

```dotenv
DIAGNOSTICS_REST_ALLOWED_HOSTS=localhost,127.0.0.1,orders.internal,*.svc.internal
DIAGNOSTICS_REST_HEADERS_JSON={"Authorization":"Bearer replace-at-deployment"}
DIAGNOSTICS_REST_MAX_RESPONSE_BYTES=1048576
```

An allowlist entry matches only the hostname, not arbitrary lookalike suffixes.
`*.svc.internal` matches `orders.svc.internal` but not `svc.internal`.

#### CSV-defined REST tools

Edit `backend/config/rest-tools.csv`:

```csv
name,description,method,base_url,template_file,timeout_seconds,max_response_bytes,enabled
get_orders_health,Read orders health,GET,https://orders.internal,orders-health.json,15,262144,true
search_incidents,Search incidents,POST,https://diagnostics.internal,incident-search.json,30,1048576,true
```

Each enabled row becomes a separately named tool such as `get_orders_health` or
`search_incidents`. Names must be unique and contain only letters, numbers, `_`, or `-`.
Only `GET` and `POST` are accepted. The base URL cannot contain credentials, query parameters, or
a fragment. Response size and timeout are bounded per row, and redirects are not followed.

The referenced template must remain under `DIAGNOSTICS_REST_TEMPLATE_ROOT`. A template defines
the only path, query, headers, environment-backed secret headers, JSON body, and model-supplied
variables the operation may use:

```json
{
  "path": "/api/v1/incidents/search",
  "query": {},
  "headers": {
    "Accept": "application/json",
    "Content-Type": "application/json"
  },
  "header_env": {
    "Authorization": "DIAGNOSTICS_INCIDENT_API_AUTHORIZATION"
  },
  "body": {
    "service": "{{service}}",
    "environment": "{{environment}}",
    "limit": "{{limit}}"
  },
  "variables": {
    "service": {
      "type": "string",
      "description": "Service identifier",
      "required": true,
      "min_length": 2,
      "max_length": 100
    },
    "environment": {
      "type": "string",
      "required": true,
      "enum": ["staging", "production"]
    },
    "limit": {
      "type": "integer",
      "required": false,
      "default": 50,
      "minimum": 1,
      "maximum": 200
    }
  }
}
```

Variables support `string`, `integer`, `number`, and `boolean`, plus enums, ranges, lengths, and
string patterns. These definitions generate the LangChain/Pydantic input schema, so invalid model
arguments are rejected before an HTTP call. An exact `"{{limit}}"` placeholder preserves the
integer type instead of converting it to text. Path variables are URL-encoded.

`header_env` maps a header name to an environment-variable name. The secret value stays outside
CSV, JSON, tool schemas, prompts, and tool-call logs. A missing environment variable becomes a
recoverable observation. POST tools are labelled `operator-templated-write` because even a
diagnostic POST can have upstream side effects; enable only idempotent or otherwise approved
operations.

### Oracle inspection

Set all three connection values to enable `oracle_select`:

```dotenv
ORACLE_DSN=db.internal.example:1521/APPDB
ORACLE_USER=diagnostic_reader
ORACLE_PASSWORD=replace-at-deployment
ORACLE_MAX_ROWS=200
```

The tool parses Oracle SQL with SQLGlot, accepts exactly one `SELECT` or `WITH` query, rejects
DML, DDL, PL/SQL, multiple statements, and `SELECT INTO`, requires bind values to be supplied
separately, applies a call timeout, and caps returned rows.

> **Required database control:** connect with an Oracle account that has only the minimum
> `SELECT` privileges needed for diagnostics. SQL parsing is defense in depth, not a substitute
> for database authorization; Oracle functions and views can have behavior beyond their visible
> query text.

### Unix hosts, services, and logs

Local inspection is enabled by default and is limited to fixed operations: a system snapshot,
filesystem usage, bounded process listing, bounded tail, literal log search, and systemd
status/journal lookup. There is no arbitrary shell tool. Local log paths must remain under
`DIAGNOSTICS_LOCAL_LOG_ROOTS`.

Remote access uses operator-defined aliases. Every CSV host generates separate host-bound tools,
for example `orders_prod_1_disk_usage`, `orders_prod_1_processes`,
`orders_prod_1_tail_log`, and `orders_prod_1_fetch_log`. The model cannot supply a hostname,
username, key, or arbitrary command. Host-key verification and allowed log roots are mandatory.

Edit `backend/config/unix-hosts.csv`:

```csv
name,host,port,username,client_keys,known_hosts,password_env,log_roots,capabilities,enabled
orders_prod_1,10.20.0.15,22,diagnostic,/secure/keys/diagnostic_ed25519,/secure/ssh/known_hosts,,/var/log/order-api,snapshot;disks;processes;tail;search;fetch;service,true
```

Multiple key paths and log roots use `;` separators. `capabilities` controls exactly which tools
are generated:

| Capability | Generated operation |
| --- | --- |
| `snapshot` | Uptime, load, memory, disk summary, and top processes |
| `disks` | Fixed `df`-style filesystem inspection |
| `processes` | Bounded process list sorted by CPU or memory |
| `tail` | Bounded tail under an allowed log root |
| `search` | Literal search within the trailing configured byte window |
| `fetch` | Bounded SFTP copy into `DIAGNOSTICS_LOG_DOWNLOAD_DIR/<alias>/` |
| `service` | Fixed systemd status and recent journal inspection |

The fetch operation copies at most `DIAGNOSTICS_SCP_MAX_BYTES`, keeps the destination under a
server-controlled directory, and is labelled `local-copy`. It uses AsyncSSH SFTP rather than
invoking an operating-system `scp` command, which preserves size limits and avoids a shell.

`password_env` may name an environment variable, but key authentication with a restricted
diagnostic account is preferred. Give that identity read-only remote filesystem permissions and
only the operating-system permissions needed for service inspection. The older
`DIAGNOSTICS_UNIX_HOSTS_JSON` format remains supported and is merged with the CSV inventory;
duplicate aliases fail startup.

### Model endpoints

The historical `LMSTUDIO_*` variable names are retained for compatibility, but the client uses
the standard OpenAI-compatible `/v1/models` and `/v1/chat/completions` endpoints. A remote HTTPS
service works without code changes:

```dotenv
LMSTUDIO_BASE_URL=https://llm-gateway.example.com/v1
LMSTUDIO_API_KEY=replace-at-deployment
LMSTUDIO_MODEL=your-openai-compatible-model-id
```

HTTPS certificate verification is enabled by the HTTP clients. The provider must support
OpenAI-compatible chat completions, tool calls for diagnostics, and preferably JSON-schema
structured output for planning and review. Configuring an external endpoint means prompts, tool
observations, and final results leave the local machine; apply the provider's data-handling and
credential policies.

## Backend logs

The backend writes exhaustive structured logs to `backend/logs/agentic-flow.log`. Each physical
line is one JSON object, making the file readable with ordinary command-line tools and directly
ingestible by systems such as Loki, Elasticsearch, Vector, or Fluent Bit.

The active file rotates automatically at 10 MB. Five backups are retained by default:

```text
backend/logs/agentic-flow.log
backend/logs/agentic-flow.log.1
backend/logs/agentic-flow.log.2
...
```

Follow all events:

```bash
tail -f backend/logs/agentic-flow.log | jq .
```

Follow one run:

```bash
tail -f backend/logs/agentic-flow.log \
  | jq --arg run_id "PASTE-RUN-ID" 'select(.run_id == $run_id)'
```

Show errors with their complete stack traces:

```bash
jq 'select(.level == "ERROR" or .level == "CRITICAL")' \
  backend/logs/agentic-flow.log
```

Summarize local-model latency and token usage:

```bash
jq 'select(.event == "lmstudio.completion.completed")
    | {
        run_id,
        node: .agent_node,
        duration_ms: .details.duration_ms,
        usage: .details.usage
      }' backend/logs/agentic-flow.log
```

Every record includes:

- UTC timestamp, severity, logger name, event name, and message
- request, run, and current agent-node correlation IDs
- process, thread, source file, source line, and function
- event-specific details such as duration, HTTP status, model, routing decision, and token usage
- exception type, message, and full stack trace when an operation fails

The instrumentation covers application startup/shutdown, every HTTP request, task and health
lookups, run acceptance, each emitted SSE event, client disconnects, every LangGraph node and
state update, quality-gate routing, LM Studio discovery and health, completion parameters,
structured-output recovery, response finish reasons, token usage, LangChain model turns, tool
arguments, bounded tool results, tool durations and failures, and final run results.

> **Privacy:** `LOG_INCLUDE_CONTENT=true` records full prompts, context, system instructions,
> intermediate drafts, critiques, and final answers. This is useful for local development and
> debugging but may store sensitive text on disk. Set it to `false` in `backend/.env` for
> metadata-only logs; character counts, timing, routing, token usage, and errors remain available.
> Model API keys are never logged. Diagnostic tool arguments and results are deliberately logged
> when content logging is enabled, so do not place secrets in URLs, SQL text, log files, or task
> prompts. `DIAGNOSTICS_REST_HEADERS_JSON` is not included in tool-call arguments.

## API

Interactive API documentation is available at
[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

### `GET /api/health`

Always reports whether the application is alive and separately reports whether LM Studio has a
loaded model.

```json
{
  "status": "ok",
  "lmstudio": "connected",
  "model": "qwen3-8b",
  "detail": null
}
```

### `GET /api/tasks`

Returns the task templates used to construct the frontend cards and starter prompts.

### `GET /api/tools`

Returns every diagnostic capability, its category, whether it is currently enabled, its
access mode, and a non-secret configuration summary.

```json
[
  {
    "name": "oracle_select",
    "category": "database",
    "enabled": false,
    "access": "read-only",
    "detail": "Disabled until ORACLE_DSN, ORACLE_USER, and ORACLE_PASSWORD are set."
  }
]
```

### `POST /api/runs`

```json
{
  "task_id": "plan",
  "prompt": "Create a 30-day pilot plan for a support knowledge assistant.",
  "context": "The support team has 12 people and 4,000 existing articles.",
  "constraints": [
    "Keep customer data local",
    "Include measurable exit criteria"
  ]
}
```

The response content type is `text/event-stream` and emits `run`, `step`, `result`, `error`, then
`done`. Validation failures use normal FastAPI JSON errors before streaming begins.

Try it without the UI:

```bash
curl -N http://127.0.0.1:8000/api/runs \
  -H 'Content-Type: application/json' \
  -d '{
    "task_id": "analyze",
    "prompt": "Compare a local AI assistant with a managed service.",
    "context": "",
    "constraints": ["Use a decision table"]
  }'
```

Run an evidence-gathering diagnostic:

```bash
curl -N http://127.0.0.1:8000/api/runs \
  -H 'Content-Type: application/json' \
  -d '{
    "task_id": "diagnose",
    "prompt": "Inspect http://127.0.0.1:8000/api/health and the latest backend application log. Correlate any errors from the last 15 minutes.",
    "context": "The backend log is backend/logs/agentic-flow.log.",
    "constraints": ["Use read-only evidence", "Label unavailable evidence"]
  }'
```

## Commands

```bash
# Run both servers
./scripts/dev.sh

# Run only one side
./scripts/backend.sh
./scripts/frontend.sh

# Backend tests and lint
uv run --project backend pytest
uv run --project backend ruff check backend

# Frontend typecheck and production build
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

## Design decisions

- **LangGraph rather than a single prompt:** individual stages have narrow responsibilities, the
  quality gate is observable, and revision is bounded.
- **LangChain inside the diagnostics node:** tool selection and observation loops use the current
  LangChain agent API without replacing the visible LangGraph lifecycle.
- **OpenAI-compatible client rather than a cloud SDK integration:** LM Studio exposes the same
  chat-completions shape locally, while model discovery removes a common setup failure.
- **SSE over WebSockets:** runs are server-to-client progress streams after one request; they do
  not need a bidirectional socket protocol.
- **Pydantic at every boundary:** malformed requests, plans, and critiques fail explicitly instead
  of silently corrupting later graph state.
- **Structured, correlated logs:** JSONL events connect an HTTP request to its streamed run,
  individual graph nodes, model calls, and diagnostic operations without depending on a hosted
  observability service.
- **Capability-based diagnostic access:** the model can choose only registered tools. Oracle is
  query-only and row-capped; generic REST is allowlisted and read-only; POST destinations and
  payloads come only from operator templates; Unix access uses host-bound, fixed inspection
  operations; and log reads and copies are root-confined and bounded.

## Troubleshooting

### The UI says “LM Studio offline”

Open LM Studio, load a chat model, and start the local server. Confirm it directly:

```bash
curl http://127.0.0.1:1234/v1/models
```

If LM Studio uses a different port, update `LMSTUDIO_BASE_URL` and restart the backend.

### A run fails at the planning or review stage

Use an instruction-tuned model with reliable JSON output. Small base models may ignore the
requested schema. The backend strips Markdown code fences and can recover an embedded JSON object,
but it intentionally rejects responses that still cannot be validated.

### Generation is slow

Each run uses at least five local completions. Choose a smaller or more heavily quantized model,
reduce `AGENT_MAX_TOKENS`, or set `AGENT_MAX_REVISIONS=0` during rapid iteration.

### The frontend cannot reach the backend

The Vite proxy expects the API on `127.0.0.1:8000`. If you change `BACKEND_PORT`, also change the
proxy target in `frontend/vite.config.ts`.

### A diagnostic tool is shown as “Not configured”

Check `curl http://127.0.0.1:8000/api/tools`, update `backend/.env`, and restart the backend.
Oracle requires all three connection settings. REST requires a non-empty allowed-host list.
CSV REST tools require `enabled=true` and a valid JSON template. Remote Unix inspection requires
an enabled host row with `host`, `username`, `known_hosts`, and `log_roots`.

### The model describes checks but does not call tools

Use a chat model with reliable OpenAI-compatible tool calling. Include explicit targets in the
request: the registered SSH alias, allowed URL, configured Oracle view/query goal, or log path.
The agent intentionally reports missing access rather than guessing targets.
