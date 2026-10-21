# Field Sales — Claude Code instructions

## Workflow rules
- At the start of every task, read ARCHITECTURE.md first. Use it to decide which files to open; open only those.
- Do not use subagents.
- Keep tool output small: pipe tests/logs through tail/grep, read file ranges instead of whole files.
- At the end of any task that changes structure, data flow, modules, external services, or reveals a gotcha, update ARCHITECTURE.md:
  - Edit affected sections in place; do not append a changelog.
  - Keep it under 300 lines.
  - Reference files/functions, never paste code.
- If the task changes nothing architectural, do not touch ARCHITECTURE.md.

## Workflow rules (apply to every task automatically)
- Before starting any development task, read ARCHITECTURE.md and use it to find the relevant files. Open only what the task needs.
- For quick questions that don't change code, skip reading ARCHITECTURE.md unless needed.
- Read file ranges or grep for functions instead of reading whole files.
- Keep test/log output short: pipe through tail/grep, use quiet reporters.
- Do not use subagents.
- When a development task is finished:
  - If it changed structure, data flow, modules, external services, or revealed a gotcha, update ARCHITECTURE.md: edit sections in place, no changelog, keep it under 300 lines, reference files/functions instead of pasting code.
  - If nothing architectural changed, leave ARCHITECTURE.md alone.
  - Reply with a short summary: what changed, files touched, whether ARCHITECTURE.md was updated.
- If a session grows long mid-task, when I say "checkpoint": update ARCHITECTURE.md, write PROGRESS.md (goal, done, remaining, files touched, open issues; max 40 lines), and reply "ready". In a new session, if PROGRESS.md exists, read it and continue; delete it when the task is complete.
- After finishing each development task, append ONE line to ~/.claude/telegram-report/field_sales/session-notes.md: "- <what was built/fixed> (<main files>)". Max 120 chars. Do not read the file back.
