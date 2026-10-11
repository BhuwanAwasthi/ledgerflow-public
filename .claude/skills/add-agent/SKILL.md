---
name: add-agent
description: Add a new configurable agent to the LedgerFlow pipeline (code + YAML + workflow stage + tests). Use when asked to add, split or replace an agent or an agent action.
---
To add agent `<id>` with action `<action>`:

1. Create `backend/app/agents/<id>.py` with a class implementing `Agent.run(action, ctx) -> AgentResult`. Keep LLM calls inside `ctx.llm.structured(...)`; return 1-5 plain-English `reasoning` lines.
2. Create `config/agents/<id>.yaml` following the existing files: id, name, description, kind, enabled, handler, actions{<action>: {outcomes, prompt?}}, model (if llm/hybrid), params, retries, timeout_s.
3. If it uses the LLM, add `config/prompts/<id>_<action>.md` (Jinja2) and a Pydantic output model. Add FakeLLM fixtures for the scenarios it touches.
4. Add a stage to `config/workflow.yaml` and map **every** outcome (including `skipped` if the agent can be disabled). Re-point the previous stage's outcome to it.
5. Run the config validation test, then the scenario tests. The Agent Studio list picks up the new agent automatically; confirm it renders.
6. Document the agent in one row of the agents table in docs/SPEC.md §5.
