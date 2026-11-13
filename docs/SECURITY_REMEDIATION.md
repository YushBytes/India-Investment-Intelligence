# Security remediation record

An audit trail for the one history rewrite this repository has had. Kept
because a rewritten history is, by design, a history that no longer shows
what happened, and because the rewrite did **not** fully close the exposure
it was performed to close.

## 2026-11-13: personal cash balance removed from history

### What happened

During phase 2, the maintainer's actual available cash balance was used as
the worked example for the INR money model. It was carried over from a
preserved local state file and spread to three files:

- `indiacore/config.india.example.json` (`budget_inr`, `max_order_inr`)
- `indiacore/money.py` (module docstring examples)
- `tests/test_indiacore_money.py` (test values, the same figure in paise, and
  a derived half-value used in a floor boundary case)

It is personal financial information and it was published when this
repository was made public on 2026-11-13 at 14:01 UTC. It appeared in no
commit message.

### Exposure window

Public from 14:01 UTC until the first fix at 16:15 UTC, roughly two hours.
GitHub traffic for that period recorded 0 clones, 0 unique cloners, 0 views
and 0 forks. GitHub excludes the repository owner's own activity from those
figures and reports them with a lag, so this is strong evidence of no third
party access rather than proof of it.

### What was done

**Step 1, forward fix.** Every occurrence was replaced with a round,
self-evidently fictional `10000.00`. Expectations derived from the old figure
were recomputed rather than renamed, since changing a budget moves them: the
whole-share counts in the sizing test and the floor boundary case both
changed. The real exchange closes in that test were kept, because market data
is not personal; only the budget was.

**Step 2, history rewrite.** `git filter-repo` with a blob callback keyed on
the three exact blob ids, swapping each for its corrected content. Keying on
blob ids rather than on a text pattern meant no other file in the history
could be touched, and swapping whole blobs rather than substituting text left
every commit internally consistent: a text substitution would have left phase
2's tests asserting share counts computed from the old budget, producing a
history whose tests fail.

Rehearsed twice on throwaway clones before being applied, and applied with
`--force-with-lease`, never a plain force.

### Commit hash map

| | Before | After |
| --- | --- | --- |
| Upstream base | `3d0f4ef` | `3d0f4ef` (unchanged) |
| Phase 2 | `df3eb2d` | `137c19e` |
| Phase 3 | `c391136` | `a2f3cc2` |
| README rewrite | `3974b87` | `61cb248` |
| Roadmap and workflow | `b9ba0ec` | `1a2095e` |
| The forward fix | `5ef8d51` | pruned: the rewrite made it empty |

Preserved: every commit message verbatim, every author and date, the upstream
base commit's hash and therefore its attribution, all 148 files, and the MIT
licence notice. Tests green at every commit after the rewrite: 473 at phase 2,
792 at phase 3, 795 at the README commit, 798 at the tip.

Links to the pre-rewrite hashes, including any in earlier project reports,
are dead references to a history that no longer exists on any branch.

### What the rewrite did not achieve

**The old objects are still retrievable from GitHub.** Verified
unauthenticated immediately after the force-push: the superseded commits and
the superseded blob both answer HTTP 200 when requested by their id. A force
push removes the references, not the objects; GitHub keeps unreferenced
objects in its own storage until it garbage-collects, on no published
schedule.

The superseded commit ids are also discoverable, not secret. The repository
activity endpoint is readable without authentication and lists the before and
after hash of every push, including the force push, from which the superseded
history can be walked.

So after both steps the position is: clean on every branch and in every
reachable commit, and still retrievable by anyone who looks up a superseded
hash. A support request has been raised with GitHub asking them to garbage
collect the unreferenced objects. This file will record the outcome.

The repository has no forks, so no fork network is holding the objects alive,
which is the condition under which such a request can fully resolve the
exposure.

### If this happens again

1. Fix the current files and push, so the highest visibility surface is clean
   within minutes.
2. Measure the real exposure before deciding how far to go: traffic, forks,
   and whether the value is in a commit message.
3. Scan every blob in the history, not the working tree, and not only for the
   exact string: a paise form, a derived value or a rounded variant leaks the
   same fact.
4. Rehearse the rewrite on a throwaway clone and run the tests at every
   rewritten commit before touching the public repository.
5. Back up with `git bundle create <file> --all` and verify by cloning from
   the bundle, not just by `git bundle verify`.
6. Treat the credential or the figure as already disclosed. Rotating a key, or
   accepting that a number is known, is safer than assuming a rewrite was
   timely enough.
