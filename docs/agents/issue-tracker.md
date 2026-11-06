# Issue tracker: Beads

Issues for this repo live in Beads, a dependency-aware tracker driven by the `bd` CLI. Run `bd init` once per clone if `.beads/` is missing. Use `bd <command> --help` for flags.

## Conventions

- One ticket = one bead. A spec/feature = a parent bead (`--type epic`); its tickets are children (`--parent <id>`).
- Triage state is a label on the bead (see `triage-labels.md`): `bd update <id> --add-label <label>` (or `bd label add <id> <label>`).
- Blocking between tickets uses dependencies: `bd dep add <ticket> --blocked-by <blocker>`.
- Conversation history goes in comments. Multi-line text goes through stdin: `printf '...\n' | bd comment <id> --stdin`.
- Track a piece of work in one tracker only.

## When a skill says "publish to the issue tracker"

`bd create "<title>" -d "<body>" [--parent <id>] [-l <labels>] [--acceptance "<criteria>"] [--deps blocked-by:<id>]`

For a long body, use `--body-file -` and pipe the text in. Add `--silent` to get only the new ID.

## When a skill says "fetch the relevant ticket"

`bd show <id>` (add `--json` for scripting). Comments: `bd comments <id>`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is an epic bead with one **child** bead per ticket.

- **Map**: `bd create "<effort> map" --type epic -d "<Notes / Decisions-so-far / Fog>"`. Update later with `bd update <map> --append-notes "..."`.
- **Child ticket**: `bd create "<title>" --parent <map> -l <type> -d "<question>"`, where `<type>` is one of `research`/`prototype`/`grilling`/`task`, recorded as a label.
- **Blocking**: `bd dep add <ticket> --blocked-by <blocker>`. A ticket is unblocked when every blocker is closed.
- **Frontier**: `bd ready --parent <map> --plain` lists open, unblocked, unclaimed tickets under the map. Lowest ID wins.
- **Claim**: `bd update <id> --claim` before any work. A claimed ticket leaves `bd ready`.
- **Resolve**: `printf '## Answer\n...\n' | bd comment <id> --stdin`, then `bd close <id> --reason "<gist>"`, then append a context pointer (gist + `<id>`) to the map with `bd update <map> --append-notes "..."`.
