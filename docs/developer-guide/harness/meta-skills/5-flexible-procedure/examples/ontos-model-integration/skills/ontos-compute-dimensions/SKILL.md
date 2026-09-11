---
name: ontos-compute-dimensions
description: >
  Analyze non-attention compute dimensions for Ontos model integration: FFN, MoE, expert routing, all-to-all, quantization, residual/HC/mHC, MTP/speculative/decode acceleration, and their KP constraints.
---

# Ontos Compute Dimensions

Own FFN/MoE, quantization, residual/special features, and decode acceleration.

For full compute-dimension decision trees and formulas, read `../../references/details/compute-dimensions-details.md`.

## KP Focus

Read matching KP files from `../../references/integration_keypoints/`:

- FFN/MoE: KP-0016, KP-0018, KP-0024, KP-0029, KP-0030, KP-0031, KP-0036, KP-0037.
- Quantization: KP-0024, KP-0040, plus vLLM config rewrite KPs when dtype is rewritten.
- Residual/special: KP-0025.
- Decode acceleration: KP-0014 and any MTP/speculative KPs added later.
- Plan/profiling cross-checks: KP-0047 through KP-0057 when compute ops enter the op list.

## FFN/MoE Checklist

1. Classify dense FFN, MoE, MoE with shared experts, fine-grained MoE, expert-choice, or novel.
2. Capture routed/shared expert counts, top-k, scoring function, group/topk-group fields, and inherited routing fields.
3. Map vLLM backend choice: FusedMoE, MegaMoE, DeepEP high-throughput/low-latency, or other.
4. Separate TP, EP, all-to-all, and platform constraints.
5. Compute expert and shared expert parameter contributions separately.

## Quantization Checklist

1. Separate weight quantization, activation quantization, and QAT simulation quantization.
2. Identify per-component dtype overrides; do not collapse heterogeneous precision to one global dtype.
3. Require separate profiling groups for different GEMM kernels even when shapes match.
4. Align with vLLM quantization rewrites and serving kernels.

## Residual And Decode Checklist

1. For HC/mHC, track `hc_mult`, four per-layer HC ops, activation multiplier, and model-level HC head ops.
2. Treat mHC Sinkhorn constraints as training/weight semantics unless serving has a distinct runtime op.
3. For MTP, verify whether it is a full block, which attention path it uses, and how it affects memory and per-step compute.
4. For speculative decoding, separate draft model analysis from target model integration.

## Output

Return:

- FFN/MoE classification and backend requirements.
- Quantization map by component and kernel.
- Residual/special/decode feature report.
- KP hits and validation commands.
- Op/profiling implications to pass to `ontos-profiling-plan`.
