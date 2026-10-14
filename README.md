# skill-sync-dsh

DeepSeek Harness skill bundle that watches the upstreams of the skill bundles built from
open-source skill repositories — and rebuilds them when upstream moves.

[中文说明](README.zh.md)

## What it manages

| Bundle | Upstream | Version basis |
|---|---|---|
| `agent-browser-skill-dsh` | `vercel-labs/agent-browser` | tag `vX.Y.Z`, skill bodies in `skill-data/` |
| `vercel-skills-dsh` | `vercel-labs/skills` | tag `vX.Y.Z` (one skill is hand-distilled, hence manual) |
| `superpowers-skill-dsh` | `obra/superpowers` | tag `vX.Y.Z` |
| `mattpocock-skills-dsh` | `mattpocock/skills` | commit `49dd158d…` (see `KNOWN_DEVIATIONS.md`) |
| `open-design-skills-dsh` | `nexu-io/open-design` | tag `open-design-vX.Y.Z` + commit, material fetched per file |

Coverage is data, not code: one entry in `skills/skill-sync/data/registry.json` plus one
recipe module in `skills/skill-sync/scripts/recipes/` manages another bundle.

## Commands

```powershell
$script = "dsh-plugin-build\skill-sync-dsh\skills\skill-sync\scripts\skill_sync.py"

python $script check                      # read-only: what is out of date
python $script check --json               # same, machine-readable
python $script update <package> --dry-run # verify a rebuild without landing it
python $script update --all               # rebuild everything that has a new release
python $script verify --all --profile %USERPROFILE%\.dsh\profiles\desktop
```

`update` never touches the DSH profile. It ends with the tarball path and the exact
`install_bundle` step for a human to run — or, when the package is already installed, the
quit-DSH / `pnpm add "file:<new tgz>"` / restart sequence. That distinction matters:
installing an already-loaded bundle while DSH runs empties it (pnpm cannot rename the
directory a running host holds, and it clears the target before failing), and a plain
`pnpm install` would rebuild the version being replaced instead of the new one.

## How a rebuild stays honest

1. **Pinned upstream** — every snapshot is a codeload tarball at a tag or commit (open-design
   is fetched per file from jsDelivr because its repository is 3.5 GB).
2. **One recipe per upstream** — the recipe reproduces exactly what the shipped bundle
   contains, including local adaptations, which live in `recipes/overlays/<upstream>/` as
   reviewable text rather than strings inside code.
3. **Golden regression** — `tests/test_golden_regression.py` replays each recipe against the
   exact revision the installed bundle came from and compares **every file byte-for-byte**.
   That is what makes the recipes trustworthy; a silent drift cannot pass.
4. **Verify before landing, back up before replacing, roll back on failure** — a failed
   update leaves the bundle byte-identical.
5. **`KNOWN_DEVIATIONS.md`** — where an installed bundle does not match its upstream tag, the
   fact is recorded and pinned to a commit instead of being quietly reproduced.

## Tests

```powershell
$env:PYTHONPATH = "$PWD\format-factory\.pytest-libs"
python -m pytest dsh-plugin-build/skill-sync-dsh/tests -p no:cacheprovider -p no:tmpdir -q
```

See `tests/README.md` for why those flags are not optional on this host.

## Licence

MIT — see [LICENSE](LICENSE). The managed bundles' own licences are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
