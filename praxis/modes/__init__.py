"""praxis.modes — runtime-agnostic tool-capability bundles."""

from .base import Mode


def load_mode(name: str) -> Mode:
    """Convenience alias for Mode.load(name)."""
    return Mode.load(name)


__all__ = ["Mode", "load_mode"]
