# Detailed Section Index

Use this index when a split skill needs the full decision tree or template that used to live in the legacy monolithic skill. Prefer the pre-extracted `details/*.md` files below. Use `../../legacy/model-integrator/SKILL.md` or `../../legacy/kp-manager/SKILL.md` only for audit/backfill.

## Active Skill To Legacy Sections

| Active skill | Detail file | Legacy sections extracted |
|---|---|---|
| `ontos-model-integrator` | `details/orchestrator-details.md` | `核心原则`, `执行流程`, `维度间依赖顺序 (DAG)` |
| `ontos-model-intake` | `details/intake-details.md` | `Step -1`, `Step 0`, `Step 0.5`, `Step 0.X` |
| `ontos-vllm-dossier` | `details/vllm-dossier-details.md` | `Step 0.V` through `Dossier 如何影响维度 1~8` |
| `ontos-identity-config` | `details/identity-config-details.md` | `维度 1: 模型身份注册` |
| `ontos-attention-kv` | `details/attention-kv-details.md` | `维度 2`, `维度 4`, `未知创新架构处理协议` |
| `ontos-compute-dimensions` | `details/compute-dimensions-details.md` | `维度 3`, `维度 5`, `维度 6`, `维度 7`, `未知创新架构处理协议` |
| `ontos-profiling-plan` | `details/profiling-plan-details.md` | `维度 8`, `Step 8` plan template through section 8.1 |
| `ontos-plan-validator` | `details/plan-validator-details.md` | `Subagent` and plan validation sections 8.2 through 8.7 |
| `ontos-kp-manager` | `details/kp-manager-details.md` | legacy `kp-manager` schema, workflow, content boundaries |

## Loading Guidance

- Read the split skill first.
- Read `../../references/kp-routing.md` when KP ownership matters.
- Read the matching `details/*.md` file only when the split skill needs detailed formulas, command snippets, output templates, or a decision tree not repeated in the split skill.
- Keep new updates in active split skills and KP files. Do not treat legacy files as the primary edit target unless the user explicitly asks to update the preserved copy.
