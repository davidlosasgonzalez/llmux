"""Installer for the ``deep-council`` Claude Code skill and MCP registration.

Installation is explicit (only runs on ``fcc-council install-claude-skill``),
never overwrites an existing skill without a ``.bak`` backup, and never edits the
global Claude Code configuration automatically — it only prints the registration
snippet for the user to apply.
"""

import shutil
import sys
from pathlib import Path

SKILL_NAME = "deep-council"

SKILL_MD = """\
---
name: deep-council
description: >-
  Consult FCC Council — several free cloud models that propose, cross-review,
  synthesise and adversarially critique — when the user asks for a second
  opinion, to compare proposals, to evaluate an architecture or plan, to run an
  adversarial review, to reach consensus among models, or to save Claude
  context/calls by offloading a deliberation to free models.
---

# deep-council

Use FCC Council when the user asks to:
- get a second opinion or a sanity check;
- compare several proposals or approaches;
- evaluate an architecture or design;
- review a plan;
- save Claude context or calls;
- deliberately use free models;
- run an adversarial review;
- reach consensus among multiple models.

## How to invoke

Prefer the MCP tool `council_evaluate` (registered separately). If MCP is not
available, run the CLI:

```bash
fcc-council evaluate --output json --depth deep "<question>"
```

Send ONLY the context that is actually needed — never the whole repository, and
never secrets. Use depth `deep` unless the user asks for speed (then `quick`).

## Presenting the result

The tool returns a compact JSON payload. Present to the user:
- the conclusion (`answer`);
- the recommended action (`recommended_action`);
- material disagreements;
- uncertainties;
- which models and providers participated (`models_used`, `providers_used`);
- the number of rounds (`rounds`).

Do NOT run another full Claude Code session inside this skill. Call the MCP tool
or the `fcc-council` CLI core only.
"""


def skill_dir() -> Path:
    return Path.home() / ".claude" / "skills" / SKILL_NAME


def install_skill() -> list[Path]:
    """Create the skill file, backing up any existing one. Returns paths written."""
    target = skill_dir() / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_suffix(".md.bak")
        shutil.copy2(target, backup)
        print(f"Backed up existing skill to {backup}", file=sys.stderr)
    target.write_text(SKILL_MD, encoding="utf-8")
    return [target]


def render_mcp_registration() -> str:
    """Return the MCP registration the user can add to Claude Code."""
    return (
        "claude mcp add fcc-council -- fcc-council serve-mcp\n\n"
        "Or add to your MCP servers JSON config:\n\n"
        "{\n"
        '  "mcpServers": {\n'
        '    "fcc-council": {\n'
        '      "command": "fcc-council",\n'
        '      "args": ["serve-mcp"]\n'
        "    }\n"
        "  }\n"
        "}"
    )
