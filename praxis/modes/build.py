"""Built-in build mode — full tool access (default)."""
from .base import Mode

MODE = Mode(
    name="build",
    # No denied_tools, no allowed_tools restriction — full access
    prompt_suffix="",
    requires_confirmation=False,
)
