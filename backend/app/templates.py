from .models import TaskTemplate

TASK_TEMPLATES = [
    TaskTemplate(
        id="diagnose",
        title="Software diagnostics",
        description="Investigate services using live database, API, server, and log evidence.",
        icon="activity",
        prompt_label="What system problem should the agent diagnose?",
        placeholder=(
            "Describe the symptom, affected service, time window, hosts, API endpoints, "
            "database indicators, and what changed…"
        ),
        starter=(
            "Investigate why the order API is returning elevated 5xx responses. Check service "
            "health, recent logs, upstream REST dependencies, and relevant Oracle indicators. "
            "Build an evidence-backed diagnosis and remediation plan."
        ),
        output_hint=(
            "An evidence-backed incident diagnosis with observations, likely causes, confidence, "
            "and safe remediation steps."
        ),
    ),
    TaskTemplate(
        id="write",
        title="Write & refine",
        description="Create polished content for a specific audience and outcome.",
        icon="pen-line",
        prompt_label="What should the agent write?",
        placeholder="Describe the content, audience, tone, and desired outcome…",
        starter=(
            "Write a concise launch announcement for a new developer tool that turns natural "
            "language into reliable workflow automations."
        ),
        output_hint="A publication-ready draft with a clear structure and voice.",
    ),
    TaskTemplate(
        id="analyze",
        title="Analyze",
        description="Break down a decision, document, or situation into actionable findings.",
        icon="chart-no-axes-combined",
        prompt_label="What should the agent analyze?",
        placeholder="Paste the material or describe the decision you need help with…",
        starter=(
            "Analyze the trade-offs of building an internal AI assistant versus buying a managed "
            "platform for a 60-person engineering organization."
        ),
        output_hint="Evidence, trade-offs, risks, and a practical recommendation.",
    ),
    TaskTemplate(
        id="plan",
        title="Make a plan",
        description="Turn an objective into sequenced, measurable execution steps.",
        icon="map",
        prompt_label="What outcome are you planning for?",
        placeholder="Describe the goal, timeline, people, and constraints…",
        starter=(
            "Create a 30-day plan to pilot a local AI knowledge assistant with the support team."
        ),
        output_hint="A phased plan with milestones, owners, risks, and success criteria.",
    ),
    TaskTemplate(
        id="code",
        title="Technical copilot",
        description="Design, explain, review, or troubleshoot a software solution.",
        icon="braces",
        prompt_label="What technical task should the agent tackle?",
        placeholder="Share the requirement, code, error, or architecture question…",
        starter=(
            "Design a fault-tolerant webhook ingestion service that handles retries, duplicate "
            "events, and per-tenant rate limits."
        ),
        output_hint="A technically precise answer with examples and explicit assumptions.",
    ),
    TaskTemplate(
        id="brainstorm",
        title="Brainstorm",
        description="Generate, cluster, and rank ideas instead of returning a flat list.",
        icon="sparkles",
        prompt_label="What should the agent brainstorm?",
        placeholder="Describe the challenge, audience, and boundaries…",
        starter=(
            "Brainstorm high-value agent workflows for an operations team, then rank them by "
            "impact and implementation effort."
        ),
        output_hint="Distinct ideas grouped by theme and ranked with rationale.",
    ),
    TaskTemplate(
        id="summarize",
        title="Summarize",
        description="Distill long material into the signal your audience needs.",
        icon="align-left",
        prompt_label="What should the agent summarize?",
        placeholder="Paste text or describe the material and intended audience…",
        starter=(
            "Summarize the following meeting notes for executives. Highlight decisions, risks, "
            "owners, and next actions: "
        ),
        output_hint="A concise, audience-aware summary with decisions and actions.",
    ),
]

TASKS_BY_ID = {task.id: task for task in TASK_TEMPLATES}
