# KP Routing For Split Skills

Use this file when `ontos-kp-manager` integrates new or existing keypoints into the active split skills.

## Routing Rules

- Identity/config KPs route to `ontos-identity-config`.
- Attention and KV cache KPs route to `ontos-attention-kv`.
- FFN, MoE, quantization, residual, and decode-acceleration KPs route to `ontos-compute-dimensions`.
- Profiling, op-list, trace, timing, and plan-completeness KPs route to `ontos-profiling-plan` and usually `ontos-plan-validator`.
- Cross-cutting orchestration KPs route to `ontos-model-integrator`.
- Every KP remains single-source in `integration_keypoints/`; split skills should reference IDs and routing summaries, not copy full definitions.

## Current KP Ownership

| KP Range / IDs | Primary Skill | Secondary Skill(s) |
|---|---|---|
| KP-0001 to KP-0005 | `ontos-attention-kv` | `ontos-plan-validator` for memory/prefix-cache gates |
| KP-0006 to KP-0012 | `ontos-attention-kv` | `ontos-profiling-plan` for backend/wrapper work |
| KP-0013 | `ontos-attention-kv` | `ontos-plan-validator` |
| KP-0014 | `ontos-compute-dimensions` | `ontos-attention-kv` for MTP attention path |
| KP-0015 | `ontos-attention-kv` | `ontos-plan-validator` |
| KP-0016 | `ontos-compute-dimensions` | `ontos-plan-validator` |
| KP-0017 | `ontos-attention-kv` | `ontos-plan-validator` |
| KP-0018 | `ontos-compute-dimensions` | `ontos-profiling-plan` |
| KP-0019 | `ontos-attention-kv` | `ontos-profiling-plan` |
| KP-0020 to KP-0022 | `ontos-attention-kv` | `ontos-profiling-plan`, `ontos-plan-validator` |
| KP-0023 | `ontos-profiling-plan` | `ontos-plan-validator` |
| KP-0024 | `ontos-compute-dimensions` | `ontos-profiling-plan` |
| KP-0025 | `ontos-compute-dimensions` | `ontos-profiling-plan` |
| KP-0026 | `ontos-attention-kv` | `ontos-profiling-plan` |
| KP-0027 to KP-0028 | `ontos-profiling-plan` | `ontos-attention-kv`, `ontos-compute-dimensions`, `ontos-plan-validator` |
| KP-0029 to KP-0031 | `ontos-compute-dimensions` | `ontos-profiling-plan` |
| KP-0032 | `ontos-profiling-plan` | `ontos-compute-dimensions` |
| KP-0033 | `ontos-identity-config` | `ontos-attention-kv`, `ontos-plan-validator` |
| KP-0034 to KP-0035 | `ontos-identity-config` | `ontos-profiling-plan`, `ontos-plan-validator` |
| KP-0036 | `ontos-compute-dimensions` | `ontos-identity-config` |
| KP-0037 | `ontos-profiling-plan` | `ontos-compute-dimensions` |
| KP-0038 to KP-0039 | `ontos-profiling-plan` | `ontos-attention-kv` |
| KP-0040 | `ontos-compute-dimensions` | `ontos-profiling-plan`, `ontos-plan-validator` |
| KP-0041 | `ontos-identity-config` | `ontos-plan-validator` |
| KP-0042 | `ontos-attention-kv` | `ontos-model-intake` for feature-vector extraction |
| KP-0043 to KP-0044 | `ontos-profiling-plan` | `ontos-plan-validator` |
| KP-0045 | `ontos-attention-kv` | `ontos-identity-config` for naming maps |
| KP-0046 | `ontos-identity-config` | `ontos-attention-kv`, `ontos-plan-validator` |
| KP-0047 to KP-0051 | `ontos-plan-validator` | `ontos-profiling-plan` |
| KP-0052 to KP-0056 | `ontos-profiling-plan` | `ontos-plan-validator` |
| KP-0057 | `ontos-identity-config` | `ontos-plan-validator` |

## Updating This Map

When adding a KP:

1. Route it by dimension first.
2. Add secondary skills if it changes validation, profiling, op shape, timing semantics, or orchestration.
3. Update the owning split skill with a short KP ID reference or checklist item.
4. Update `ontos-kp-manager` only when the routing policy itself changes.

