# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues:

- Repository: `paulpai0412/wferp`
- URL: <https://github.com/paulpai0412/wferp>
- Source skill setup: <https://github.com/mattpocock/skills/tree/main/skills/engineering/setup-matt-pocock-skills>

Use the `gh` CLI for issue operations when a skill says to publish to or fetch from the issue tracker.

## Conventions

- **Create an issue**: `gh issue create --repo paulpai0412/wferp --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --repo paulpai0412/wferp --comments`, filtering comments by `jq` and also fetching labels when needed.
- **List issues**: `gh issue list --repo paulpai0412/wferp --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --repo paulpai0412/wferp --body "..."`.
- **Apply / remove labels**: `gh issue edit <number> --repo paulpai0412/wferp --add-label "..."` / `--remove-label "..."`.
- **Close**: `gh issue close <number> --repo paulpai0412/wferp --comment "..."`.

When running inside this clone, `gh` can also infer the repository from `git remote -v`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue in `paulpai0412/wferp`.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --repo paulpai0412/wferp --comments`.
