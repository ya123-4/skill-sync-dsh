# Known deviations

This file records places where a bundle installed today does **not** correspond to
the upstream tag its version number claims. Each entry is a fact about history:
it cannot be undone, so the recipe and its golden regression encode it instead,
and the deviation stays visible rather than silently spreading into future
rebuilds.

Every deviation here has been confirmed by the user; a new one may only be added
the same way.

## mattpocock-skills-dsh 1.3.1

**Fact.** The installed bundle does not match upstream tag `v1.3.1`. It matches
commit `49dd158d1076134a641b33efb035946536778336` (2026-10-09T10:56:49Z), which sits
45 commits ahead of that tag on `main`.

**Evidence.** Replayed against the tag, the recipe reproduces 64 of 79 files
byte-for-byte and 15 files differ — every one of them in the direction of upstream
fixes that landed after the tag (`ask-matt` reading a skill before asserting,
`handoff` temp-dir resolution, `tdd` / `teach` / `wayfinder` / `grilling` wording,
`wizard/template.sh`, `setup-matt-pocock-skills`). Replayed against
`49dd158d1076`, all 79 files match exactly.

**Why it happened.** The bundle was built on 2026-10-10 from a checkout of `main`;
its version number was taken from `1.3.1` in the repository metadata, and no
commit was recorded anywhere.

**Consequence.** The registry entry carries
`"baselineCommit": "49dd158d1076134a641b33efb035946536778336"`, and the golden
regression replays the recipe against that commit instead of the tag. Version
checks still compare `1.3.1` against the newest upstream tag; the commit is what
makes the installed bytes reproducible.

**Resolution path.** When upstream publishes `v1.4.0` or later, the update path
rebuilds from that tag with the recipe and this deviation ends: the bundle will
correspond to a tag again, and `baselineCommit` can be dropped once the rebuild is
verified byte-for-byte.
