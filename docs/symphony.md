# Symphony automation for r1-hermes

This repository is wired to a GitHub Projects v2 queue for parallel Symphony/Codex agents.

- Project: https://github.com/users/kandotrun/projects/2
- Repo: https://github.com/kandotrun/r1-hermes
- Workflow config: `.symphony/WORKFLOW_R1_HERMES_GITHUB_PROJECTS.md`
- Dashboard port: `127.0.0.1:4041`
- Active states: `Todo`, `In Progress`
- Terminal state: `Done`
- Concurrency: 3 agents

## Start

Run from the Symphony Elixir checkout:

```bash
cd /tmp/hermes-repos/symphony/elixir
export GITHUB_TOKEN="$(gh auth token)"
# The Codex app-server must inherit the provider credentials used by the workflow
# (`OPENAI_API_KEY` and `OPENAI_BASE_URL` for Kan's Omniroute setup).
# Source your private chmod-600 env file here if they are not already set.
test -n "${OPENAI_API_KEY:-}" || { echo "OPENAI_API_KEY is required" >&2; exit 1; }
test -n "${OPENAI_BASE_URL:-}" || { echo "OPENAI_BASE_URL is required" >&2; exit 1; }
mise exec -- ./bin/symphony \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails \
  --port 4041 \
  /home/kan/r1-hermes/.symphony/WORKFLOW_R1_HERMES_GITHUB_PROJECTS.md
```

## Monitor

```bash
curl -sS http://127.0.0.1:4041/api/v1/state | python -m json.tool
```

## Queue rules

- Add implementation issues to the Project with `Status = Todo`.
- Symphony moves items to `In Progress`, opens/merges PRs, then moves them to `Done`.
- Keep secrets out of issue bodies, PR bodies, logs, and committed workflow files.
- If a real Rabbit R1 capture is needed, attach only a sanitized sample with tokens removed.
