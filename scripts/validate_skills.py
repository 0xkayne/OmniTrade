#!/usr/bin/env python3
"""Validate every SKILL.md under docs/developer-guide/harness/.

Why this exists: a skill's frontmatter schema is **closed** — `name`,
`description`, `license`, `allowed-tools`, `metadata`, `compatibility`, and
nothing else. One extra top-level key and the skill silently fails to load.
That failure mode is invisible: the file looks fine, the body is fine, and the
loader just skips it.

The five doc-metadata fields (`status` / `authority` / `owner` / `updated` /
`applies_to`) therefore go under `metadata:`, whose contents are not validated.
`harness/meta-skills/4-fixed-procedure` §6 and `5-flexible-procedure` §6 both
document this; this script is the executable form of that rule.

Usage:
    python3 scripts/validate_skills.py            # all skills under harness/
    python3 scripts/validate_skills.py <dir>...   # specific skill directories

Exit code is 0 when every skill validates, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: uv run python scripts/validate_skills.py")

ALLOWED_PROPERTIES = {"name", "description", "license", "allowed-tools", "metadata", "compatibility"}
REQUIRED_PROPERTIES = {"name", "description"}
NAME_RE = re.compile(r"^[a-z0-9-]+$")
MAX_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1024
MAX_COMPATIBILITY_LEN = 500

# Only the harness's own skills. `meta-skills/` is the frozen template library:
# its example skills belong to the project they were authored in and are not
# ours to lint. Both layouts occur — process skills sit under `skills/<name>/`,
# companion skills at `harness/<name>/`.
SKILL_GLOBS = (
    "docs/developer-guide/harness/*/SKILL.md",
    "docs/developer-guide/harness/skills/*/SKILL.md",
)


def validate(skill_dir: Path) -> list[str]:
    """Return a list of problems; empty means the skill is valid."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"{skill_md}: not found"]

    content = skill_md.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return [f"{skill_md}: no YAML frontmatter found"]

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return [f"{skill_md}: invalid frontmatter format"]

    frontmatter = yaml.safe_load(match.group(1))
    if not isinstance(frontmatter, dict):
        return [f"{skill_md}: frontmatter must be a YAML mapping"]

    problems: list[str] = []

    unexpected = set(frontmatter) - ALLOWED_PROPERTIES
    if unexpected:
        problems.append(
            f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected))}. "
            f"Allowed properties are: {', '.join(sorted(ALLOWED_PROPERTIES))}"
        )

    missing = REQUIRED_PROPERTIES - set(frontmatter)
    if missing:
        problems.append(f"Missing required key(s): {', '.join(sorted(missing))}")

    name = frontmatter.get("name")
    if name is not None:
        if not isinstance(name, str):
            problems.append("'name' must be a string")
        elif not NAME_RE.match(name) or name.startswith("-") or name.endswith("-") or "--" in name:
            problems.append(f"'name' must be kebab-case: {name!r}")
        elif len(name) > MAX_NAME_LEN:
            problems.append(f"'name' exceeds {MAX_NAME_LEN} characters")

    description = frontmatter.get("description")
    if description is not None:
        if not isinstance(description, str):
            problems.append("'description' must be a string")
        elif "<" in description or ">" in description:
            problems.append("'description' must not contain angle brackets")
        elif len(description) > MAX_DESCRIPTION_LEN:
            problems.append(f"'description' exceeds {MAX_DESCRIPTION_LEN} characters")

    compatibility = frontmatter.get("compatibility")
    if compatibility is not None:
        if not isinstance(compatibility, str):
            problems.append("'compatibility' must be a string")
        elif len(compatibility) > MAX_COMPATIBILITY_LEN:
            problems.append(f"'compatibility' exceeds {MAX_COMPATIBILITY_LEN} characters")

    # The skill directory name is how a project skill is discovered; a mismatch
    # means `/name` and the description-triggered name disagree.
    if isinstance(name, str) and name != skill_dir.name:
        problems.append(f"'name' is {name!r} but the directory is {skill_dir.name!r}")

    return [f"{skill_md.name}: {p}" if len(problems) > 1 else p for p in problems] or []


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        targets = [Path(a) for a in argv[1:]]
    else:
        root = Path(__file__).resolve().parent.parent
        targets = sorted({p.parent for g in SKILL_GLOBS for p in root.glob(g)})

    if not targets:
        print("No skills found.", file=sys.stderr)
        return 1

    failures = 0
    for skill_dir in targets:
        problems = validate(skill_dir)
        if problems:
            failures += 1
            print(f"✗ {skill_dir.name}")
            for problem in problems:
                print(f"    {problem}")
        else:
            print(f"✓ {skill_dir.name}")

    print()
    if failures:
        print(f"✗ {failures}/{len(targets)} skill(s) failed validation")
        return 1
    print(f"✓ 全部通过：{len(targets)} 个 skill")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
