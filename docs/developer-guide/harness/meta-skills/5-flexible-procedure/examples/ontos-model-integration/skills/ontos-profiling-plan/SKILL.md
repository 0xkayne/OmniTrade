---
name: ontos-profiling-plan
description: >
  Create the op list, execution-plan implications, profiling scan matrix, and wrapper alignment analysis for Ontos model integration. Use when converting dimension analysis and vLLM dossier evidence into attention/mlp/collectives profiling work, op granularity, fusion, parallelism, and profiling validation.
---

# Ontos Profiling Plan

Own the bridge from architecture analysis to measurable simulator inputs.

For full profiling subsystem guidance, op-list examples, and plan section templates, read `../../references/details/profiling-plan-details.md`.

## KP Focus

Read profiling and plan KPs from `../../references/integration_keypoints/`, especially KP-0006, KP-0007, KP-0008, KP-0018, KP-0020, KP-0021, KP-0023, KP-0027, KP-0028, KP-0032, KP-0034, KP-0035, KP-0037, KP-0039, KP-0043, KP-0044, KP-0047 through KP-0057.

## Op List Rules

1. Align op granularity to vLLM fused kernel boundaries.
2. Preserve repeated op occurrences; do not collapse by op name when counts matter.
3. Include model-level ops before/after the per-layer loop.
4. Track conditional and per-layer variant ops as separate profile entries when performance differs.
5. Source every shape from code evidence, not high-level intuition.
6. Use the standard table columns:
   `Op | Profile | Shape | Kernel | Quantization | Prefill | Decode | Framework Base`.

## Profiling Matrix

Group profile rows by:

- `attention/`: sequence-level kernels; prefill/decode split is usually required.
- `mlp/`: token-level GEMM, MoE, compressor, indexer, HC, and elementwise/fused token ops.
- `collectives/`: all-reduce, all-to-all, send/recv, and new communication patterns.

For each row include:

- Kernel/wrapper.
- Parameter space.
- Independent data groups by dtype/kernel/backend.
- Trigger condition.
- vLLM alignment status.

## Execution Semantics

- Use `ParallelOpGroup` or equivalent max semantics for independent multi-stream groups.
- Use a single profile entry for heterogeneous fused kernels rather than summing subcomponents.
- Distinguish per-rank self time from critical-path wall time for tensor-parallel profiling.
- Treat non-profiler vLLM benchmark as E2E ground truth; use torch profiler traces only for attribution.

## Output

Return:

- Per-layer op list.
- Model-level op list.
- Fusion and parallelism map.
- Profiling plan by subsystem.
- Wrapper alignment risks.
- Simulator extension requirements.
