# scripts/

仓库的开发辅助脚本。它们不属于 `onefill` 命令，也不进入发布产物，只在本地开发时手动运行。

| 脚本 | 用途 | 运行方式 |
|---|---|---|
| `verify.sh` | 提交前的验证门禁：`lint` · `format` · `arch` · `skills` · `test` · `docs` 六个阶段；补齐磁盘配额所需的环境变量（见 `CLAUDE.md`） | `./scripts/verify.sh` 或 `./scripts/verify.sh lint arch` |
| `validate_skills.py` | 校验 `harness/` 下每个 `SKILL.md` 的 frontmatter。skill 的字段集合是**封闭**的，多一个键就装不上，而加载失败是静默的——所以文档元数据必须挂在 `metadata:` 下（见 `harness/index.md` §6） | `uv run python scripts/validate_skills.py` |
| `chaos_test.py` | 崩溃恢复验证：在一串 dry-run 下单过程中随机 `SIGKILL` 进程，重启后检查 `onefill recover` 能否看到未完成的 Intent，以及 SQLite 状态机与 JSONL 审计日志是否一致 | `uv run python scripts/chaos_test.py --iterations 10` |
| `benchmark.py` | 执行管线基准测试：跑多组 trial 采集各阶段耗时，产出原始 JSON | `uv run python scripts/benchmark.py run --mode dry-run --trials 20` |
| `analyze_benchmark.py` | 读取 benchmark 的原始输出，生成瓶颈分析图表 | `uv run python scripts/analyze_benchmark.py <run-dir>` |

## 输出位置

Benchmark 原始数据默认写到 `$OMNITRADE_BENCHMARK_DIR/<日期>/raw`；未设置该环境变量时用
`/share_data/wangziping/omnitrade-benchmark/<日期>/raw`。**不要写进仓库**——家目录有严格的磁盘配额
（见 `CLAUDE.md` 的 "Disk quota" 一节），原始 trial 数据是体积最大的一类生成物。单次运行可以用
`--output-dir` 覆盖。

`chaos_test.py` 的产物通过 `onefill recover` 观察，不额外落盘。
