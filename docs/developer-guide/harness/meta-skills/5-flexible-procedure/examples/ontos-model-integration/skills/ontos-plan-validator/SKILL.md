---
name: ontos-plan-validator
description: >
  Validate a Ontos model integration plan for completeness against KP constraints, vLLM evidence, op/profiling coverage, source-code shape verification, model-level op coverage, and implementation task dependencies. Use before execution, review, or handoff of any Ontos integration plan.
---

# Ontos Plan Validator

Own plan completeness checks. Treat missing evidence as incomplete, not as a warning to ignore.

For detailed validation gates and failure recovery flow, read `../../references/details/plan-validator-details.md`.

## Required Inputs

- Integration plan draft.
- Intake packet.
- vLLM dossier.
- Dimension reports.
- KP reference directory: `../../references/integration_keypoints/`.

## Validation Gates

1. Op list to profiling plan coverage:
   - Every op in the op list has a profiling entry or an explicit fused-parent entry.
   - Every profiling entry maps back to one or more planned ops.
2. vLLM granularity alignment:
   - Planned ops match vLLM fused kernel boundaries.
   - vLLM serving kernels are not missing from the plan.
3. Standard table format:
   - Op tables use `Op | Profile | Shape | Kernel | Quantization | Prefill | Decode | Framework Base`.
4. Source-code parameter verification:
   - Shapes and condition factors are traced to vLLM/reference source.
   - Conditional factors, einsum dimensions, local/global heads, group counts, and compression ratios are explicit.
5. Model-level op coverage:
   - Per-layer loop pre/post ops are represented in op and profiling tables.
6. KP hit matrix:
   - All applicable KP files were considered.
   - P0/P1 hits have concrete validation commands or checks.
7. Implementation dependency graph:
   - ModelConfig precedes execution plan.
   - Execution plan precedes profiling.
   - Profiling precedes simulator validation.

## Output

Return a validation report:

- `PASS` only if every gate passes.
- `FAIL` with blocking issues and exact plan sections to fix.
- `NEEDS_EVIDENCE` when the plan could be correct but source proof is missing.

Do not mark a plan complete when evidence is indirect or absent.
