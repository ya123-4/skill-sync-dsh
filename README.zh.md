# skill-sync-dsh

DeepSeek Harness 的 skill 插件「上游版本看守」：检查那些由开源 skill 仓库打包的插件是否有新版本，
并按各自当初的适配方式把它们重建为可安装的 `.tgz`。

[English](README.md)

## 它管什么

| 插件 | 上游 | 版本基准 |
|---|---|---|
| `agent-browser-skill-dsh` | `vercel-labs/agent-browser` | tag `vX.Y.Z`，skill 正文在 `skill-data/` |
| `vercel-skills-dsh` | `vercel-labs/skills` | tag `vX.Y.Z`（其中一个 skill 是手工蒸馏，标为 manual） |
| `superpowers-skill-dsh` | `obra/superpowers` | tag `vX.Y.Z` |
| `mattpocock-skills-dsh` | `mattpocock/skills` | commit `49dd158d…`（见 `KNOWN_DEVIATIONS.md`） |
| `open-design-skills-dsh` | `nexu-io/open-design` | tag `open-design-vX.Y.Z` + commit，素材逐文件抓取 |

覆盖面是数据而不是代码：新增一个被管插件 = 在 `skills/skill-sync/data/registry.json` 加一条登记
+ 在 `skills/skill-sync/scripts/recipes/` 加一个配方模块。

## 命令

```powershell
$script = "dsh-plugin-build\skill-sync-dsh\skills\skill-sync\scripts\skill_sync.py"

python $script check                      # 只读：谁有新版本
python $script check --json               # 同上，机器可读
python $script update <包名> --dry-run     # 只重建到校验为止，不落盘
python $script update --all               # 把所有有新版本的插件重建为 tgz
python $script verify --all --profile %USERPROFILE%\.dsh\profiles\desktop
```

`update` **不会碰 DSH profile**：它以 tgz 路径和一条 `install_bundle` 指令结束，安装由人决定。

## 重建为什么可信

1. **上游固定**：每次快照都是某个 tag 或 commit 的 codeload tarball（open-design 因仓库 3.5 GB，
   改为按 commit 从 jsDelivr 逐文件抓取）。
2. **每个上游一个配方**：配方精确复刻已发布插件的产物，包括当初的本地适配；适配以
   `recipes/overlays/<上游>/` 下可评审的文本存在，而不是藏在代码里的字符串。
3. **黄金回归**：`tests/test_golden_regression.py` 用「当前插件内容所对应的那个上游版本」重放配方，
   与磁盘上的插件**逐字节**比对——静默漂移过不了这一关。
4. **先校验后落盘、落盘前备份、失败即回滚**：一次失败的更新让插件保持逐字节不变。
5. **`KNOWN_DEVIATIONS.md`**：已安装内容与上游 tag 不一致的地方，记录事实并固定到具体 commit，
   而不是被悄悄地复制下去。

## 测试

```powershell
$env:PYTHONPATH = "$PWD\format-factory\.pytest-libs"
python -m pytest dsh-plugin-build/skill-sync-dsh/tests -p no:cacheprovider -p no:tmpdir -q
```

为什么那两个参数在本机不可省，见 `tests/README.md`。

## 许可

MIT，见 [LICENSE](LICENSE)；被管插件各自的上游许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
