# Skill sources

Installed 2026-11-14. The `fastapi` skill (official, bundled with FastAPI) was already installed via `uvx library-skills` on 2026-11-03.

## From Mindrally/skills (MIT) — https://github.com/Mindrally/skills

- htmx — https://github.com/Mindrally/skills/tree/main/htmx
- alpine-js — https://github.com/Mindrally/skills/tree/main/alpine-js
- tailwindcss — https://github.com/Mindrally/skills/tree/main/tailwindcss
- postgresql-best-practices — https://github.com/Mindrally/skills/tree/main/postgresql-best-practices
- redis-best-practices — https://github.com/Mindrally/skills/tree/main/redis-best-practices
- docker — https://github.com/Mindrally/skills/tree/main/docker
- sql-best-practices — https://github.com/Mindrally/skills/tree/main/sql-best-practices

Skipped on purpose:

- python-testing (Mindrally) — pytest-focused; this repo runs unittest (`uv run python -m unittest discover -s tests`).
- fastapi-python (Mindrally) — the official `fastapi` skill is already installed and is better.

## Custom (written for this project)

- sqlalchemy-async — SQLAlchemy 2.0 async-only rules, from the project's own lessons.
- bandeh — project rules for any agent working in this repo.

## From Cloudflare (MIT) — https://github.com/cloudflare/security-audit-skill

- security-audit — https://github.com/cloudflare/security-audit-skill/tree/main/skills/security-audit
  Checked 2026-11-14 before installing: SKILL.md read in full (400 lines, defensive source-first workflow, sandboxed execution only, dummy secrets, no live probing, guidance mode by default, full audit only on explicit request, describes fixes but never modifies target source). The two .cjs validators require only fs/path/util, read one JSON file and print validation errors — no network, no exec, no writes, no env access. The .test.cjs files spawn only the local validator on temp files. MIT license.

## From Vercel Labs (MIT) — https://github.com/vercel-labs/skills

- find-skills — https://github.com/vercel-labs/skills/tree/main/skills/find-skills
  Checked 2026-11-14 before installing: single SKILL.md (238 lines), no scripts, no code. Teaches the agent to search skills.sh with the `npx skills` CLI, check the leaderboard first, and verify candidates by install count (prefer 1K+, cautious under 100), source reputation, and GitHub stars before recommending. Note: the skills CLI needs Node 22+; the laptop has Node v24.19.0 via nvm (~/.nvm/versions/node/v24.19.0/bin/node) alongside the default Node 18.19.1. The `bandeh` skill adds a guard: any skill find-skills suggests must be read in full and checked first, never installed with -y.
