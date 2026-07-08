# pdf-to-deeptutor Agent Rules

## Startup

Read `AGENTS.md`, then `HANDOFF.md` (if present). Read other files
only when the task requires them. Do not preload `docs/`, `demos/`,
`scripts/`, `tests/`, or `schemas/`.

## Roles

This project is small and is intended to be maintained by humans and
their AI assistants. Roles are not pre-assigned to specific agents.

## Do Not

1. Do not preload `docs/`, `demos/`, `scripts/`, `tests/`, or
   `schemas/`.
2. Do not read secrets or print secret values.
3. Do not modify files outside this project unless the user explicitly
   asks.
4. Do not overwrite unrelated changes from another contributor.
5. Do not mark work done without running `pytest` or stating why
   verification was not run.
6. Do not commit real scanned materials, real outlines, or generated
   project workspaces — they are gitignored for a reason.

## Project Boundaries

- Project root: this directory.
- Real user data is **never** committed. See `.gitignore`.
- Related resources: none external.

## Handoff

Keep `HANDOFF.md` current. Move old detail to `docs/decisions/` (for
stable design choices) or to git history (for transient work).

## Pipeline stage contracts

The pipeline is split into Stages 0-7. Each stage owns a directory
under `projects/<id>/` and has a defined contract in
`docs/PIPELINE.md`. Changing a stage's contract requires updating the
schema in `schemas/` and adding a decision record in
`docs/decisions/`.
