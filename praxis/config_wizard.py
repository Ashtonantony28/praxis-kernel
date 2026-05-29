"""Interactive configuration manager for Praxis.

Allows changing per-agent model assignments, max turns, cost cap,
runtime, and effort presets without editing files manually.

Writes:
  - <workspace_root>/.env        (PRAXIS_RUNTIME, PRAXIS_MAX_SESSION_COST,
                                   PRAXIS_MAX_TURNS, PRAXIS_EFFORT_PRESET)
  - <workspace_root>/convergence.yaml  (agents: section only)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, str] = {
    "orchestrator": "claude-opus-4-7",
    "builder":      "claude-sonnet-4-6",
    "reviewer":     "claude-haiku-4-5",
    "scout":        "claude-haiku-4-5",
    "scribe":       "claude-haiku-4-5",
    "max_turns":    "80",
    "cost_cap":     "2.00",
    "runtime":      "claude",
    "effort_preset": "medium",
}

ROLES = ["orchestrator", "builder", "reviewer", "scout", "scribe"]

MODEL_CHOICES = [
    ("claude-opus-4-7",   "strongest reasoning, slowest, highest cost"),
    ("claude-sonnet-4-6", "balanced, recommended for most tasks"),
    ("claude-haiku-4-5",  "fastest, cheapest, good for simple tasks"),
    ("gemini-2.5-flash",  "free tier, OpenAI-compatible"),
    ("llama3.1:8b",       "local via Ollama, completely free"),
]

PRESETS: dict[str, dict[str, str]] = {
    "minimal": {
        "orchestrator": "claude-haiku-4-5",
        "builder":      "claude-haiku-4-5",
        "reviewer":     "claude-haiku-4-5",
        "scout":        "claude-haiku-4-5",
        "scribe":       "claude-haiku-4-5",
        "max_turns":    "20",
        "cost_cap":     "1.00",
    },
    "low": {
        "orchestrator": "claude-sonnet-4-6",
        "builder":      "claude-sonnet-4-6",
        "reviewer":     "claude-haiku-4-5",
        "scout":        "claude-haiku-4-5",
        "scribe":       "claude-haiku-4-5",
        "max_turns":    "40",
        "cost_cap":     "2.00",
    },
    "medium": {
        "orchestrator": "claude-sonnet-4-6",
        "builder":      "claude-sonnet-4-6",
        "reviewer":     "claude-sonnet-4-6",
        "scout":        "claude-sonnet-4-6",
        "scribe":       "claude-sonnet-4-6",
        "max_turns":    "80",
        "cost_cap":     "5.00",
    },
    "high": {
        "orchestrator": "claude-opus-4-7",
        "builder":      "claude-opus-4-7",
        "reviewer":     "claude-sonnet-4-6",
        "scout":        "claude-sonnet-4-6",
        "scribe":       "claude-sonnet-4-6",
        "max_turns":    "120",
        "cost_cap":     "10.00",
    },
    "max": {
        "orchestrator": "claude-opus-4-7",
        "builder":      "claude-opus-4-7",
        "reviewer":     "claude-opus-4-7",
        "scout":        "claude-opus-4-7",
        "scribe":       "claude-opus-4-7",
        "max_turns":    "200",
        "cost_cap":     "20.00",
    },
}

PRESET_DESCRIPTIONS = {
    "minimal": ("Minimal", "Haiku everywhere, 20 turns, $1.00 cap",
                 "simple queries, wiki lookups, status checks"),
    "low":     ("Low",     "Haiku scouts, Sonnet builder, 40 turns, $2.00 cap",
                 "routine tasks, dependency audits, summaries"),
    "medium":  ("Medium",  "Sonnet everywhere, 80 turns, $5.00 cap",
                 "most development work, code review, planning"),
    "high":    ("High",    "Sonnet scouts, Opus builder, 120 turns, $10.00 cap",
                 "complex refactors, architecture decisions"),
    "max":     ("Max",     "Opus everywhere, 200 turns, $20.00 cap",
                 "hardest problems, security audits, major design"),
}

PRESET_ORDER = ["minimal", "low", "medium", "high", "max"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_env(env_file: Path) -> dict[str, str]:
    """Parse key=value lines from an env file. Ignores comments and blank lines."""
    result: dict[str, str] = {}
    if not env_file.exists():
        return result
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _update_env(env_file: Path, updates: dict[str, str]) -> None:
    """Update specific keys in .env. Add if absent, replace if present."""
    existing_lines: list[str] = []
    if env_file.exists():
        existing_lines = env_file.read_text(encoding="utf-8").splitlines()

    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # Append any keys not yet in file
    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}")

    env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _safe_input(prompt: str, _input: Callable | None) -> str:
    """Call _input if provided, otherwise builtins input()."""
    fn = _input if _input is not None else input
    return fn(prompt)


def _model_label(model: str) -> str:
    """Return a short quality label for a model name."""
    if "opus" in model.lower():
        return "strongest, slowest"
    if "sonnet" in model.lower():
        return "balanced"
    if "haiku" in model.lower():
        return "fastest, cheapest"
    return ""


def _read_convergence_agents(yaml_file: Path) -> dict[str, str]:
    """Read agents: section from convergence.yaml without importing PyYAML."""
    if not yaml_file.exists():
        return {}
    text = yaml_file.read_text(encoding="utf-8")
    # Find the agents: block
    match = re.search(r"(?m)^agents:\s*\n((?:[ \t]+\S.*\n?)*)", text)
    if not match:
        return {}
    agents: dict[str, str] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped and ":" in stripped:
            k, _, v = stripped.partition(":")
            agents[k.strip()] = v.strip()
    return agents


def _write_convergence_agents(yaml_file: Path, agents: dict[str, str]) -> None:
    """Add or replace the agents: section in convergence.yaml.

    Strategy:
    1. Read existing text.
    2. Build the new agents block as a string.
    3. If an agents: section exists, replace it; otherwise append.
    4. Write back — never touches runtimes: or task_types:.
    """
    # Build the new agents block
    lines = ["agents:"]
    for role in ROLES:
        if role in agents:
            lines.append(f"  {role}: {agents[role]}")
    new_block = "\n".join(lines) + "\n"

    if yaml_file.exists():
        text = yaml_file.read_text(encoding="utf-8")
    else:
        text = ""

    # Match an existing agents: block (key at column 0 + indented lines that follow)
    pattern = re.compile(r"(?m)^agents:\s*\n(?:[ \t]+.*\n?)*")
    if pattern.search(text):
        text = pattern.sub(new_block, text)
    else:
        # Append with a blank separator if the file doesn't end with newline
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n" + new_block

    yaml_file.write_text(text, encoding="utf-8")


def _load_current_config(
    workspace_root: Path,
    env_file: Path,
) -> dict[str, str]:
    """Load effective configuration by merging defaults, convergence.yaml, and .env."""
    config = dict(DEFAULTS)  # start with hardcoded defaults

    # Layer in convergence.yaml agents section
    yaml_file = workspace_root / "convergence.yaml"
    agents_from_yaml = _read_convergence_agents(yaml_file)
    for role in ROLES:
        if role in agents_from_yaml:
            config[role] = agents_from_yaml[role]

    # Layer in .env values
    env_vals = _read_env(env_file)
    if "PRAXIS_RUNTIME" in env_vals:
        config["runtime"] = env_vals["PRAXIS_RUNTIME"]
    if "PRAXIS_MAX_SESSION_COST" in env_vals:
        config["cost_cap"] = env_vals["PRAXIS_MAX_SESSION_COST"]
    if "PRAXIS_MAX_TURNS" in env_vals:
        config["max_turns"] = env_vals["PRAXIS_MAX_TURNS"]
    if "PRAXIS_EFFORT_PRESET" in env_vals:
        config["effort_preset"] = env_vals["PRAXIS_EFFORT_PRESET"]
    if "PRAXIS_MODEL" in env_vals:
        # PRAXIS_MODEL overrides the orchestrator model
        config["orchestrator"] = env_vals["PRAXIS_MODEL"]
    config["default_mode"] = env_vals.get("PRAXIS_DEFAULT_MODE", "build")

    return config


# ---------------------------------------------------------------------------
# Sub-menus
# ---------------------------------------------------------------------------

def _menu_model(role: str, current: str, _input: Callable | None) -> str:
    """Prompt user to pick a model for a given role. Returns new model string."""
    print(f"\nSelect model for {role}:")
    for i, (model, desc) in enumerate(MODEL_CHOICES, start=1):
        print(f"  ({i}) {model:<22} — {desc}")
    print(f"  ({len(MODEL_CHOICES) + 1}) custom              — type your own model string")
    print(f"  (Enter) keep current  [{current}]")

    choice = _safe_input(f"Choice [1-{len(MODEL_CHOICES) + 1}]: ", _input).strip()
    if not choice:
        return current

    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(MODEL_CHOICES):
            return MODEL_CHOICES[idx - 1][0]
        if idx == len(MODEL_CHOICES) + 1:
            custom = _safe_input("  Enter model string: ", _input).strip()
            return custom if custom else current
    print("  Invalid choice, keeping current.")
    return current


def _menu_max_turns(current: str, _input: Callable | None) -> str:
    """Prompt user for max turns. Returns validated string."""
    while True:
        raw = _safe_input(
            f"Max turns per cycle [10-200, current: {current}]: ", _input
        ).strip()
        if not raw:
            return current
        if raw.isdigit():
            val = int(raw)
            if 10 <= val <= 200:
                return str(val)
        print("  Error: please enter an integer between 10 and 200.")


def _menu_cost_cap(current: str, _input: Callable | None) -> str:
    """Prompt user for cost cap. Returns validated string."""
    while True:
        raw = _safe_input(
            f"Cost cap per session in USD [0=no cap, current: ${current}]: ", _input
        ).strip()
        if not raw:
            return current
        try:
            val = float(raw)
            if val >= 0:
                return f"{val:.2f}"
        except ValueError:
            pass
        print("  Error: please enter a decimal number >= 0.")


def _menu_runtime(current: str, _input: Callable | None) -> str:
    """Prompt user to select runtime. Returns new runtime string."""
    print("\nSelect runtime:")
    print("  (1) claude — subscription OAuth, flat cost")
    print("  (2) cloud  — OpenAI-compatible cloud (Gemini, OpenRouter, Groq)")
    print("  (3) local  — local Ollama or vLLM, free")
    print(f"  (Enter) keep current [{current}]")

    choice = _safe_input("Choice [1-3]: ", _input).strip()
    mapping = {"1": "claude", "2": "cloud", "3": "local"}
    if not choice:
        return current
    if choice in mapping:
        return mapping[choice]
    print("  Invalid choice, keeping current.")
    return current


def _menu_preset(config: dict[str, str], _input: Callable | None) -> dict[str, str]:
    """Show effort preset menu. Returns updated config dict (in-place-style)."""
    print("\nSelect effort level:")
    for i, preset_name in enumerate(PRESET_ORDER, start=1):
        label, summary, use_for = PRESET_DESCRIPTIONS[preset_name]
        print(f"  ({i}) {label:<8} — {summary}")
        print(f"      Use for: {use_for}")
    print(f"  ({len(PRESET_ORDER) + 1}) Custom  — set each parameter individually (existing menu)")
    print()

    choice = _safe_input(f"Choice [1-{len(PRESET_ORDER) + 1}]: ", _input).strip()
    if not choice:
        return config

    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(PRESET_ORDER):
            preset_name = PRESET_ORDER[idx - 1]
            preset = PRESETS[preset_name]
            label = PRESET_DESCRIPTIONS[preset_name][0]
            # Show diff
            print(f"\n  Applying {label} preset:")
            for key, new_val in preset.items():
                old_val = config.get(key, DEFAULTS.get(key, ""))
                if key == "cost_cap":
                    old_display = f"${old_val}"
                    new_display = f"${new_val}"
                else:
                    old_display = old_val
                    new_display = new_val
                if old_val == new_val:
                    print(f"    {key:<14}: {old_display} (unchanged)")
                else:
                    print(f"    {key:<14}: {old_display} -> {new_display}")

            confirm = _safe_input("\n  Confirm? [Y/n]: ", _input).strip().lower()
            if confirm == "n":
                print("  Preset not applied.")
                return config

            # Apply preset
            updated = dict(config)
            for key, val in preset.items():
                updated[key] = val
            updated["effort_preset"] = preset_name
            return updated

        if idx == len(PRESET_ORDER) + 1:
            # Custom — return to main menu without changes
            return config

    print("  Invalid choice, no preset applied.")
    return config


def _menu_default_mode(current: str, _input) -> str:
    """Prompt user to select default mode."""
    print(f"\nDefault mode (current: {current})")
    print("  (1) build  — full tool access (default)")
    print("  (2) plan   — read-only planning mode")
    choice = _safe_input("Select [1-2]: ", _input).strip()
    if choice == "2":
        return "plan"
    elif choice == "1":
        return "build"
    else:
        print("  Invalid choice; keeping current.")
        return current


# ---------------------------------------------------------------------------
# Main menu display
# ---------------------------------------------------------------------------

def _print_main_menu(config: dict[str, str]) -> None:
    """Print the main configuration menu with current values."""
    print()
    print("========================================")
    print("  Praxis Configuration")
    print("========================================")
    print("Current configuration:")

    roles_labels = [
        ("orchestrator", "Orchestrator model"),
        ("builder",      "Builder model     "),
        ("reviewer",     "Reviewer model    "),
        ("scout",        "Scout model       "),
        ("scribe",       "Scribe model      "),
    ]

    for i, (role, label) in enumerate(roles_labels, start=1):
        model = config[role]
        qual = _model_label(model)
        suffix = f" ({qual})" if qual else ""
        print(f"  [{i}] {label}: {model}{suffix}")

    print(f"  [6] Max turns per cycle: {config['max_turns']}")
    print(f"  [7] Cost cap per session: ${config['cost_cap']}")

    runtime = config["runtime"]
    if runtime == "claude":
        runtime_display = "claude (oauth)"
    else:
        runtime_display = runtime
    print(f"  [8] Runtime: {runtime_display}")
    print(f"  [9] Effort preset: {config['effort_preset']}")
    print(f"  [10] Default mode: {config['default_mode']}")
    print("  [11] Done")
    print()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_config_wizard(
    workspace_root: "Path | str",
    *,
    env_file: "Path | None" = None,
    _input: "Callable | None" = None,
    _env_mode: str = "merge",
) -> None:
    """Run the interactive Praxis configuration wizard.

    Args:
        workspace_root: Path (or str) of the repo root.
        env_file: Path for .env output (defaults to workspace_root/.env).
        _input: callable used instead of builtins input() — allows testing.
        _env_mode: "overwrite" or "merge" (kept for API compatibility; the
                   wizard always uses _update_env which is merge-and-update).
    """
    workspace_root = Path(workspace_root)
    if env_file is None:
        env_file = workspace_root / ".env"

    yaml_file = workspace_root / "convergence.yaml"

    # Load current effective config
    config = _load_current_config(workspace_root, env_file)

    while True:
        _print_main_menu(config)
        choice = _safe_input("Select option [1-11]: ", _input).strip()

        # Model items 1–5
        if choice in ("1", "2", "3", "4", "5"):
            role = ROLES[int(choice) - 1]
            config[role] = _menu_model(role, config[role], _input)

        elif choice == "6":
            config["max_turns"] = _menu_max_turns(config["max_turns"], _input)

        elif choice == "7":
            config["cost_cap"] = _menu_cost_cap(config["cost_cap"], _input)

        elif choice == "8":
            config["runtime"] = _menu_runtime(config["runtime"], _input)

        elif choice == "9":
            config = _menu_preset(config, _input)

        elif choice == "10":
            config["default_mode"] = _menu_default_mode(config["default_mode"], _input)

        elif choice == "11":
            # Save and exit
            _save_config(workspace_root, env_file, yaml_file, config)
            break

        else:
            print("  Invalid option. Please enter a number between 1 and 11.")


def _save_config(
    workspace_root: Path,
    env_file: Path,
    yaml_file: Path,
    config: dict[str, str],
) -> None:
    """Persist config to .env and convergence.yaml, then print summary."""
    # 1. Write .env
    env_updates = {
        "PRAXIS_RUNTIME":           config["runtime"],
        "PRAXIS_MAX_SESSION_COST":  config["cost_cap"],
        "PRAXIS_MAX_TURNS":         config["max_turns"],
        "PRAXIS_EFFORT_PRESET":     config["effort_preset"],
        "PRAXIS_DEFAULT_MODE":      config["default_mode"],
    }
    try:
        _update_env(env_file, env_updates)
    except Exception as exc:
        print(f"Warning: could not write {env_file}: {exc}")

    # 2. Write convergence.yaml agents section
    agents = {role: config[role] for role in ROLES}
    try:
        _write_convergence_agents(yaml_file, agents)
    except Exception as exc:
        print(f"Warning: could not update {yaml_file}: {exc}")

    # 3. Print summary
    print()
    print("Configuration saved. Changes take effect on next praxis invocation.")
    print()
    print("Effective configuration:")

    col_w = 22  # column width for source tag alignment
    for role in ROLES:
        label = f"{role} model"
        model_val = config[role]
        print(f"  {label:<20} : {model_val:<{col_w}} [convergence.yaml]")

    print(f"  {'max turns':<20} : {config['max_turns']:<{col_w}} [.env]")
    print(f"  {'cost cap':<20} : ${config['cost_cap']:<{col_w - 1}} [.env]")
    print(f"  {'runtime':<20} : {config['runtime']:<{col_w}} [.env]")
    print(f"  {'effort preset':<20} : {config['effort_preset']:<{col_w}} [.env]")
    print()
