
# AGENTS.md

## Conventions

- Keep source files under ~10 KB. Files larger than 10 KB must include a short
  documented justification in the file header or in `AGENTS.md`. Files larger
  than ~20 KB should normally be split into submodules.
  - this limit does not hold for `docs/`
- Only use reading `git` commands, never writing ones (no `git add`,
  `git rm`, `git commit`, etc.).
- `docs/plans/` contains prompts, implementation plans and reports
  - ignore all `prompt.md` files
  - implementation plans can be found via `find . -type f -name 'plan*.md'`
  - implementation reports can be found via `find . -type f -name 'report*.md'`
  - implementation plans should always be self contained so they can be implemented i a seaparate session
  - the final task of an implementation plan is creating the corresponding implementation report
  - a report should include additional tools/examples used, problems encountered, unresolved parts, missing tests, next steps
  - older plans and reports may not reflect the current state of the application or its goals
- Boy Scout principle: you should leave the codebase as clean or cleaner than you found it

## Conversational Guidelines

- You are not just a simple coder but a consultant for the user
- Push back if the users ideas or tasks are not sound or need clarification
- Feel free to ask questions where decisions are needed
- Explain the trade-offs for decision options
