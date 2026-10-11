---
name: run-phase
description: Execute one build phase from docs/PHASES.md end-to-end (plan, implement, verify, review, commit). Use when the user types /run-phase <id>, e.g. /run-phase B3.
disable-model-invocation: true
---
Run phase **$ARGUMENTS** of docs/PHASES.md.

1. Read the phase section in docs/PHASES.md and every SPEC.md section it references. Read the relevant config/ files. Do not read unrelated parts of the codebase.
2. Write a short plan (files to create or change, tests to write, how you will verify each "Done when" item). If anything in the phase conflicts with the spec, say so and pick the spec.
3. Implement. Write tests alongside the code. Run the narrowest test after each meaningful change.
4. Verify every "Done when" item and collect evidence (command + output, or screenshot paths).
5. Use the spec-reviewer subagent on the diff (and the ui-critic subagent for frontend phases). Fix blocking gaps and re-run the checks.
6. Run `make check`. It must pass.
7. Commit: `git add -A && git commit -m "phase $ARGUMENTS: <one-line summary>"`.
8. Report: what was built (5 bullets maximum), the evidence, anything deferred and why (also appended to docs/DECISIONS.md).
