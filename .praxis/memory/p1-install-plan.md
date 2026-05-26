# P-1 Install Plan

## pyproject.toml extras structure
```
[project.optional-dependencies]
local = ["openai>=1.0"]              # Ollama, vLLM, llama.cpp
cloud = ["openai>=1.0"]              # OpenAI, Gemini, OpenRouter, Groq (same dep)
analyze = ["coverage", "radon", "pylint", "pip-audit"]  # code analysis tools
dev = ["pytest>=8.0"]
all = ["praxis[local]", "praxis[analyze]", "praxis[dev]"]  # everything
```

## install.sh design
1. Check Python >= 3.10 (exit with message if not)
2. Check git installed (required for FileManager)
3. pip install -e . (core)
4. Print setup checklist: which optional extras to install, which env vars to set
5. Check for gh CLI (optional, note if missing)
6. Create .praxis/memory/ and .praxis/queue/ dirs if absent
7. Copy .env.example if no .env exists
8. Must work on Ubuntu 24 and WSL2 (both have bash, python3, pip)

## .env.example
Document every env var with comments explaining purpose and where to get value.
Group by: Auth, Runtime, Integrations.
