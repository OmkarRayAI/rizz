# Push rizz to GitHub

This file is one-time use — delete it after the first push lands. It documents
the exact sequence to turn `~/Desktop/rizz/` into a public GitHub repo named
`rizz` under your personal account.

## Prereqs (one-time)

- `git` is installed (you have 2.50.1 — confirmed).
- `gh` CLI is installed (`brew install gh`).
- `gh auth login` is already done. Confirm with: `gh auth status`.

## The push, in one block

Open a normal terminal (not Claude Code) and run:

```bash
cd ~/Desktop/rizz

# 1. Initialize the repo with `main` as default.
git init -b main

# 2. Stage everything; .gitignore handles excludes.
git add .

# 3. First commit.
git commit -m "Initial public release of rizz

A Claude Code plugin that auto-decomposes a coding goal into parallel
git worktrees, runs coding agents in each, gates the resulting branches
via a diff-reviewing joiner, and (opt-in) opens pull requests.

Phases shipped:
- Phase 1: Workspace primitive (git worktree + branch + notes).
- Phase 2: Coding agents as DAG leaves with structured AgentResult.
- Phase 3: Topology-aware MetaPlanner + diff-reviewing joiner + auto-PR
  via gh CLI.
- Phase 4: Persistence (SQLite under .rizz/state.db) + CLI.
- Phase 4.5: Repackaged as a Claude Code plugin (.claude-plugin/,
  bin/rizz wrapper, /rizz:ship and /rizz:plan slash commands).

Phase 5 (verifier agent + acceptance criteria + true self-gating) is
not yet shipped; the README is honest about this."

# 4. Create the GitHub repo and push it in one step.
gh repo create rizz \
  --public \
  --description "Auto-decompose a coding goal into parallel branches and ship gated PRs. Claude Code plugin." \
  --source=. \
  --remote=origin \
  --push
```

## Expected output

`gh repo create` prints something like:

```
✓ Created repository <your-username>/rizz on GitHub
✓ Added remote https://github.com/<your-username>/rizz.git
✓ Pushed commits to https://github.com/<your-username>/rizz.git
```

## After the push: 3 follow-ups

### 1. Replace `<owner>` placeholder in README.md

The top-level `README.md` documents install with `<owner>/rizz` as a
placeholder. Substitute your actual GitHub username:

```bash
# Replace <your-username> with your real GitHub login.
sed -i '' 's|<owner>/rizz|<your-username>/rizz|g' README.md
git add README.md
git commit -m "Fix install URLs with concrete owner"
git push
```

### 2. Decide on the PyPI name

`setup.py` has `name="rizz"`. If pypi.org/project/rizz shows it's taken,
fall back to `rizz-engine`:

```bash
sed -i '' 's|name="rizz"|name="rizz-engine"|' setup.py
sed -i '' 's|pip install rizz|pip install rizz-engine|g' README.md
git add setup.py README.md
git commit -m "Use rizz-engine as PyPI package name (rizz was taken)"
git push
```

The import name (`from rizz import Rizz`) and CLI (`rizz`) stay the same
either way. Only the published wheel's name changes.

### 3. Verify the Claude Code plugin install path

This is the integration test we couldn't run from inside the build sandbox:

```
# Inside Claude Code:
/plugin marketplace add <your-username>/rizz
/plugin install rizz@rizz
```

After install, you should see `/rizz:ship` and `/rizz:plan` in `/help`.

If `/plugin marketplace add` doesn't find the plugin, the most likely
cause is the `.claude-plugin/marketplace.json` schema differs from what
we sketched. Check https://code.claude.com/docs/en/discover-plugins.md
for the current required fields and adjust the JSON. The plugin manifest
at `.claude-plugin/plugin.json` is the part I'm confident about.

## What this commit contains (sanity check before you push)

57 files, broken down:

| Path | What |
|---|---|
| `rizz/` (28 files) | The Python engine (workspace, agents, topology, joiner, store, CLI) |
| `examples/` (12 files) | Smoke tests covering Phase 1-4 |
| `skills/{ship,plan}/SKILL.md` | The two slash-command skill definitions |
| `.claude-plugin/{plugin,marketplace}.json` | Plugin and marketplace manifests |
| `bin/rizz` | Executable shell wrapper that delegates to `python -m rizz` |
| `setup.py` | Python package + console_scripts entry point |
| `README.md` | Plugin-first README |
| `LICENSE` | MIT |
| `.gitignore` | Excludes `__pycache__`, `.rizz/`, `.eggs`, `.claude/settings.local.json`, etc. |

Excluded from the commit by `.gitignore`:
- `__pycache__/`, `*.pyc`
- `.rizz/` (per-run worktree state — created when you actually use the engine)
- `.eggs/`, `*.egg-info/`, `build/`, `dist/`
- `.claude/settings.local.json` (your local Claude permission cache)
- `.DS_Store`

## Delete this file after the push

```bash
git rm PUSH_TO_GITHUB.md
git commit -m "Remove one-time push instructions"
git push
```

## What's still in `~/Downloads/LLMEngine_lite-main 3/`?

The original working copy. Safe to delete once you've confirmed the GitHub
repo is good and you're working out of `~/Desktop/rizz/`:

```bash
rm -rf "$HOME/Downloads/LLMEngine_lite-main 3"
```
