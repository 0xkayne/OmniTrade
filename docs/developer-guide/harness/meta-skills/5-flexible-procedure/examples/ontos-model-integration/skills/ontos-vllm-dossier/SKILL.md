---
name: ontos-vllm-dossier
description: >
  Build the vLLM implementation dossier for a Ontos model integration. Use when a model integration needs vLLM-ground-truth analysis, fused op maps, parallel execution maps, kernel/backend maps, config rewrites, KV cache behavior, or profiling wrapper alignment.
---

# Ontos vLLM Dossier

Own vLLM serving implementation analysis. The output is evidence for the dimension skills and final plan.

For the complete Step 0.V phase breakdown and table templates, read `../../references/details/vllm-dossier-details.md`.

## Ground Truth

vLLM serving behavior is the target. Reference model code only explains architecture intent.

## Required Output Tables

Produce these tables with source evidence:

- Fused Op Map: reference ops to vLLM fused op/kernel.
- Parallel Execution Map: default stream, auxiliary streams, and conditions.
- Kernel Backend Map: serving kernel/backend choices and platform constraints.
- Config Rewrite Map: fields vLLM rewrites or asserts.
- Profiling Wrapper Alignment Check: Ontos profiling wrapper vs vLLM serving kernel.

## AtCode Workflow

Use AtCode MCP directly from the main agent when available. Do not delegate AtCode calls to subagents.

1. Set project to the vLLM graph, usually `vllm_v4_claude`.
2. Find the registry class, for example `DeepseekV4ForCausalLM`.
3. Explore the main model class and class hierarchy.
4. Inspect all model-file component classes.
5. Trace `DecoderLayer.forward()` in execution order.
6. Trace `Model.forward()` before and after the per-layer loop.
7. Expand attention internals, including multi-stream paths and custom kernels.
8. Expand FFN/MoE internals, including backend selection and all-to-all behavior.
9. Search config, attention backend, KV cache, MoE backend, tokenizer, and speculative config paths.

Fallback to local vLLM source when the graph does not contain the target model. Mark every fallback finding as `[Fallback]`.

## Evidence Rules

- Record concrete class/function/file names for every material claim.
- Track vLLM-specific fusion and parallelism even if reference code has separate ops.
- Include model-level ops outside decoder layers; missing them breaks the final op list.
- Call out unknowns. Do not fill gaps from reference code when vLLM evidence is missing.

## Handoff

Send the dossier to all dimension skills. The final plan should use vLLM op granularity, not reference-model line granularity.
