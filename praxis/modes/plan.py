"""Built-in plan mode — read-only tool bundle."""
from .base import Mode

MODE = Mode(
    name="plan",
    denied_tools=frozenset({
        # Core write/execute tools
        "Write",
        "Edit",
        "Bash",
        "NotebookEdit",
        # Integration write actions (tool name format used in Praxis tool dispatch)
        "notion.create_page",
        "notion.update_page",
        "notion.append_block",
        "linear.create_issue",
        "linear.update_issue",
        "linear.add_comment",
        "email.draft_email",
        "calendar.propose_event",
        "wiki.ingest",
        "slack.stage_message",
        "slack.post_approval_request",
    }),
    prompt_suffix=(
        "\n\n[PLAN MODE] You are in plan mode. "
        "Do NOT execute Write, Edit, Bash, NotebookEdit, or any write integration actions. "
        "Describe what you would do as a numbered plan with file paths and changes — "
        "present the plan, do not implement it."
    ),
    requires_confirmation=True,
)
