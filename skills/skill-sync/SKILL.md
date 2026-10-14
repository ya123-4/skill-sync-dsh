---
name: skill-sync
description: Check whether skill bundles built from open-source skill repositories have a newer upstream release, and rebuild them from upstream into installable tarballs. Use when the user asks to check skill plugin versions, asks whether a skill bundle is out of date, or wants a bundle updated from its upstream repository.
---

# skill-sync

Ten packages are managed from one registry. Five are rebuilt bundles in
`dsh-plugin-build/`: each is assembled from an open-source skill repository by a
recipe, and `skill-sync` knows how it is assembled and what its installed bytes came
from. The other five follow whatever publishes them: four npm plugins (reported only —
`update` prints the `pnpm add` command and writes nothing) and archify, whose release
asset `snapshot` mode replaces wholesale.

It refuses to guess about anything else: coverage comes from the registry, so a new
package is managed by adding one entry. Every entry declares `kind` (where the
upstream is queried: `github-tags`, `github-releases`, `npm`) and `mode` (how it is
updated: `rebuild`, `snapshot`, `manual`).

## When to use it

- "check whether my skill plugins have updates" / "检查一下 skill 插件的版本"
- "update the superpowers bundle" / "把 superpowers 更新到最新"
- after an upstream release the user wants reflected locally

## Commands

Run from the workspace root (the tool finds the workspace by looking for `dsh-plugin-build/`,
and `--workspace DIR` overrides that):

```powershell
python dsh-plugin-build\skill-sync-dsh\skills\skill-sync\scripts\skill_sync.py check
python dsh-plugin-build\skill-sync-dsh\skills\skill-sync\scripts\skill_sync.py check --json
python dsh-plugin-build\skill-sync-dsh\skills\skill-sync\scripts\skill_sync.py update <package> --dry-run
python dsh-plugin-build\skill-sync-dsh\skills\skill-sync\scripts\skill_sync.py update <package> [--accept-structure-change]
python dsh-plugin-build\skill-sync-dsh\skills\skill-sync\scripts\skill_sync.py update --all
python dsh-plugin-build\skill-sync-dsh\skills\skill-sync\scripts\skill_sync.py verify --all --profile C:\Users\<you>\.dsh\profiles\desktop
```

**`check`** is read-only and needs one or two GitHub API calls per bundle. Each row is
`up to date`, `update available (local -> remote)`, or `unknown (reason)`. Rows for bundles
that ship a *manual* skill (hand-written, not copied from upstream) carry a note: that part
can never be updated automatically. If **no** bundle could be compared at all (rate limit,
offline with a cold cache), `check` exits `3` instead of `0` — a report that concluded
nothing is not a pass.

**`update`** downloads the pinned upstream snapshot, runs that bundle's recipe into a
staging directory, verifies the result, and only then lands it: the old `skills/` and
`package.json` go to `<bundle>/.skill-sync/backup/<timestamp>/` first, so any failure rolls
back to the byte. On success it bumps the bundle version, writes
`<bundle>/.skill-sync-state.json`, and runs `npm pack` into `dsh-plugin-build/dist/`.
`--dry-run` stops after verification; `--rebuild` rebuilds without a new release;
`--keep-staging` keeps the staging directory for inspection.

**`verify`** is offline. It checks that the bundle on disk still matches its state file,
that no skill name collides with another bundle in the profile, and that the tarball in
`dist/` matches the tree on disk.

## The install gate

`skill-sync` never touches the DSH profile. After a successful `update` the report ends with
the exact next step, and which one it is depends on whether the package is already installed:

```
next step (this tool never touches the profile):
  install_bundle  <absolute path to the new .tgz>          # not installed yet
```

but for a package that is **already** in the profile:

```
  <package> is ALREADY installed, so it must be replaced while DSH is not running:
  the host holds a handle on an installed bundle, and pnpm fails with ERR_PNPM_EPERM
  after emptying the package directory first.
    1) quit DSH
    2) cd <profile> ; & node pnpm.cjs add "file:<new .tgz>" --store-dir <store>
    3) start DSH again
```

Take that second path seriously — installing an already-loaded bundle while DSH runs
has twice emptied a real package here (the tarball appears under the *new* version, so a
plain `pnpm install` would faithfully rebuild the version being replaced; the command
re-points the manifest, which is also why `install_bundle` must not be used to upgrade).
An existing tarball for the same version is moved to `dist/.previous/` before packing, so
the path the profile currently points at still exists until the new one is installed.

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| `0` | success (including "nothing to do") | read the report |
| `2` | self-check failed: workspace, registry or state file unusable | fix the reported path and re-run |
| `3` | upstream unreachable: network, rate limit, missing snapshot | set `GITHUB_TOKEN`, or re-run later; `check --offline` uses the cache |
| `4` | the skill set changed | read the listed additions/removals; re-run with `--accept-structure-change` only after agreeing to them |
| `5` | verification failed | the bundle was not touched; the staging directory is kept |
| `6` | packing failed | the bundle was rolled back from its backup |
| `7` | landing failed | the bundle was rolled back; nothing is half-written |

## What the tool will not do

- touch the profile, uninstall anything, or restart anything;
- invent an upstream for a bundle the registry does not describe;
- rewrite a manual skill (such as `skills-cli`) — it only reports when the upstream one
  appears to have moved on;
- widen a deviation: when a bundle's installed bytes do not match its upstream tag, the
  difference is recorded in `KNOWN_DEVIATIONS.md` and pinned by a commit, not smoothed over.
