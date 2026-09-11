---
name: ontos-kp-manager
description: >
  Manage Ontos model-integration keypoints across the split plugin skills. Use for capturing new KPs, validating KP schema, querying/exporting KP docs, or optimizing all Ontos split skills from the complete KP corpus. Trigger on "记录关键点", "keypoint", "gotcha", "约束条件", "整合 KP", "KP protocol", "optimize model integrator skills", or "update split skills from KP".
---

# Ontos KP Manager

Manage KP lifecycle for the split plugin. The key change from the legacy `kp-manager` is that KP integration updates all affected split skills, not one monolithic `model-integrator`.

For full KP schema details and historical workflow language, read `../../references/details/kp-manager-details.md`.

## Source Of Truth

- KP files live in `../../references/integration_keypoints/KP-XXXX-*.md`.
- The README in that directory is the human index.
- Split-skill routing is summarized in `../../references/kp-routing.md`; read it before batch integration or plugin-wide optimization.
- Split skills consume KP files by ID, dimension, trigger condition, and generalized tag.
- Legacy skills under `../../legacy/` are read-only audit copies unless the user explicitly asks to update them.

## KP Capture Flow

1. Confirm the user wants to record the keypoint unless they explicitly requested recording.
2. Check duplicates:
   - Same dimension.
   - Same or overlapping generalized tag.
   - Same or overlapping trigger condition.
3. Fill required schema fields:
   - ID, date, dimension, severity, status, trigger condition, generalized tag, discoverer.
   - Problem, root cause, constraint, violation impact, executable evaluation method.
   - Code locations and standard solution.
4. Create `../../references/integration_keypoints/KP-NNNN-<slug>.md`.
5. Update `../../references/integration_keypoints/README.md`.

## Split-Skill Optimization Flow

When integrating one or more KPs, update every affected active split skill:

- `identity` or config-related KP -> `ontos-identity-config`, and sometimes `ontos-plan-validator`.
- `attention` KP -> `ontos-attention-kv`, `ontos-profiling-plan` when op/kernel/profiling behavior changes.
- `kv_cache` KP -> `ontos-attention-kv`, and `ontos-plan-validator` when memory/KV checks become plan gates.
- `ffn`, MoE, residual, quantization, or decode KP -> `ontos-compute-dimensions`, and `ontos-profiling-plan` when ops/profile rows change.
- `profiling`, op completeness, trace, timing semantics, or E2E-scope KP -> `ontos-profiling-plan` and `ontos-plan-validator`.
- Cross-cutting orchestration or phase-dependency KP -> `ontos-model-integrator`.

For existing KPs, start from `../../references/kp-routing.md`, then confirm the route against the KP's current dimension, trigger condition, and generalized tag.

For each affected skill:

1. Add or update only routing/index/checklist text needed for that skill to discover the KP.
2. Do not copy the full KP definition into the skill.
3. If the KP creates a new architecture branch, add a concise branch/checklist item to the owning dimension skill.
4. If the KP creates a new validation gate, add it to `ontos-plan-validator`.
5. If the KP changes op/profiling shape, granularity, or timing semantics, add it to `ontos-profiling-plan`.

## Batch Integration

For a KP range:

1. Read every KP file in the range.
2. Classify each as reusable constraint, one-off bug, deprecated, or duplicate.
3. Integrate reusable constraints into the affected split skills.
4. Report exclusions with reasons.

## Query And Export

- Query by KP ID, dimension, generalized tag, code path, or keyword.
- Export constraints to `../../references/integration_keypoints/constraints.json` when requested.
- Summaries should group by split skill ownership as well as by original dimension.

## Quality Rules

- KP content remains single-source in KP files.
- Active split skills should stay concise and point to KP IDs.
- Update legacy copies only when the user explicitly asks; otherwise preserve them as historical snapshots.
