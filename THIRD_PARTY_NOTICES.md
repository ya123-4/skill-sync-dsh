# Third-Party Notices

`skill-sync-dsh` ships **no third-party skill content**. It contains a registry, recipes and
tests that rebuild five other bundles, and it downloads upstream material at run time rather
than redistributing it.

The upstream projects it knows about, and the licences of the material it rebuilds:

| Upstream repository | Licence | What is taken from it |
|---|---|---|
| [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser) | Apache-2.0 | Nine skill bodies from `skill-data/` |
| [vercel-labs/skills](https://github.com/vercel-labs/skills) | MIT | `find-skills` and `pr-labeling` |
| [obra/superpowers](https://github.com/obra/superpowers) | MIT | Every skill under `skills/` |
| [mattpocock/skills](https://github.com/mattpocock/skills) | MIT | The skill set listed in `.claude-plugin/plugin.json` |
| [nexu-io/open-design](https://github.com/nexu-io/open-design) | Apache-2.0 | A curated slice: design templates, scenario and atom workflows, craft references, design systems |

Each rebuilt bundle carries the full licence text and its own notices; this file only records
what the manager reaches for. Upstream material is fetched from
`codeload.github.com` (tags and commits) and `cdn.jsdelivr.net` (open-design), never
redistributed from this package.
