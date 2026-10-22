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
        # Integration write actions ("tool:action", matched by Mode.permits)
        "notion:create_page",
        "notion:update_page",
        "notion:append_block",
        "linear:create_issue",
        "linear:update_issue",
        "linear:add_comment",
        "Email:draft_email",
        "Calendar:propose_event",
        "Wiki:ingest",
        "Slack:notify",
        "Slack:stage_message",
        "Slack:post_approval_request",
        "playwright:screenshot",
    }),
    prompt_suffix=(
        "\n\n[PLAN MODE] You are in plan mode. "
        "Do NOT execute Write, Edit, Bash, NotebookEdit, or any write integration actions. "
        "Describe what you would do as a numbered plan with file paths and changes — "
        "present the plan, do not implement it."
    ),
    requires_confirmation=True,
)
