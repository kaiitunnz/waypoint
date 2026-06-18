# Before Committing

On a shared multi-session host the working tree at commit time may not contain
every edit you made earlier in the session. Verify the change is whole and
self-consistent before you commit it.

## Inspect the staged diff

```bash
git status            # untracked/modified overview; note anything unexpected
git diff --cached     # exactly what you are about to commit
```

Read the staged diff as a self-contained unit and confirm it is **complete**:
every symbol, class, function, import, CSS class, or file the change references is
defined or updated within the same diff. A concrete failure this catches: a panel
refactor was committed where the JSX referenced a `.field-grid-row` CSS class and
a single-column grid, but the matching `globals.css` edits had been stranded in
`stash@{0}` and never reached the working tree — the commit was internally
inconsistent and the UI rendered broken.

If the diff references something it does not define, the defining edit is missing
— find it (see stashes below) before committing, do not commit the half.

## Check the stashes

Branch/stash churn from another session — or your own earlier turn — can park
edits in a stash while the tree reverts. Before concluding an edit is "gone",
look:

```bash
git stash list
git stash show -p stash@{0}    # inspect; do NOT pop blindly
```

If a stash holds the other half of your change, apply it deliberately
(`git stash apply`) and re-inspect the staged diff. **Never** `git stash drop` or
`git stash clear` on a multi-session host before reading what the stash contains —
it may belong to another session, or be the missing piece you are looking for.

## Then commit

Once the staged diff is complete and self-consistent, hand off to the host's
normal commit flow (`/make-commits` or the repo's commit conventions). This check
is additive — it does not change how commits are split or messaged.
