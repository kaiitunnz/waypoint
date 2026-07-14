# Tech-lead — report

The work on **{{branch}}** is green and complete (or a defined subset is). Open the
PR and report back. You do **not** merge — the human is the sole merge authority.

## Push the branch and open the PR

Open the PR with `gh`. First study the repo's PR conventions so yours matches (title
prefix, body structure) — look at recent merged PRs:

```bash
gh pr list --state merged --limit 5 --json title,body
```

Then push and create the PR. Follow the repo's contribution rules — if it requires
a DCO sign-off, commit with `-s`; if PRs are squash-merged with a Conventional
Commit title, make the PR title one (`feat:`/`fix:`/`docs:`/…):

```bash
git -C {{repo_dir}} push -u origin {{branch}}
gh pr create --base {{trunk}} --head {{branch}} \
  --title "<conventional-commit title for {{ticket_title}}>" \
  --body "$(cat <<'BODY'
## Summary
<what changed and why, high level>

## Ticket
{{ticket_id}}: {{ticket_title}}

## Verification
<the checks you ran, and the behavior you exercised>
BODY
)"
```

Write the body as normal paragraphs — do not hand-wrap lines; let the renderer
reflow. Capture the PR URL from `gh pr create`'s output.

## Report done (or partial)

Post the terminal build signal to {{ticket_channel}} with the PR and head commit,
then park idle — the manager takes it from here to the human review gate:

```bash
head=$(git -C {{repo_dir}} rev-parse HEAD)
waypoint board post {{ticket_channel}} "PR open: <pr-url>" \
  --key status --meta kind=done --meta pr=<pr-url> --meta commit=$head
```

- **Full completion** → `kind=done`.
- **Partial completion** → `kind=partial` and put the deferred goals in the post
  text / a `detail=` meta; the manager spawns follow-up tickets only *after* the
  subset merges.

Do **not** reap yourself — you may be asked to revise. Stay parked (idle and alive)
and leave {{branch}} as is; a review round wakes you via {{ticket_channel}} into
`templates/tech-lead/address-review.md`. While parked, do not run git in the shared
tree — the manager may be rebasing or landing your PR. The manager reaps your
subtree after the PR merges.
