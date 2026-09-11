---
name: ontos-model-integrator
description: >
  Orchestrate a new LLM model integration into Ontos serving simulation using the split Ontos plugin skills. Use whenever the user asks to integrate/add/adapt a model to Ontos, generate an integration plan, coordinate vLLM-grounded model analysis, or replace the old monolithic model-integrator workflow. Trigger on "integrate new model", "add model", "集成新模型", "添加模型", "模型适配", "Ontos model integration".
---

# Ontos Model Integrator

Coordinate the split workflow. Do not redo every analysis inside this skill. Route each phase to the narrower skill that owns it, then assemble the evidence into a single plan.

## Source Layout

- Active split skills live in this plugin's `skills/` directory.
- KP source of truth lives at `../../references/integration_keypoints/`.
- Detailed orchestrator reference lives at `../../references/details/orchestrator-details.md`.
- Detailed section routing lives at `../../references/detailed-section-index.md`.
- Legacy copies of the original monolithic skills live at `../../legacy/model-integrator/` and `../../legacy/kp-manager/`.

Use the split skills for new work. Read the targeted `../../references/details/*.md` file when a phase needs old detailed formulas, decision trees, or templates.

## Workflow

1. Intake and feature extraction: use `ontos-model-intake`.
2. vLLM implementation dossier: use `ontos-vllm-dossier`.
3. Dimension analysis:
   - Identity and config: use `ontos-identity-config`.
   - Attention and KV cache: use `ontos-attention-kv`.
   - FFN, MoE, quantization, residual, and decode acceleration: use `ontos-compute-dimensions`.
   - Profiling and execution plan requirements: use `ontos-profiling-plan`.
4. Plan assembly and validation: use `ontos-plan-validator`.
5. New or changed KP handling: use `ontos-kp-manager`.

## Integration Contract

Every integration plan must include:

- Input inventory: model id, config path, reference model path, optional kernel/docs, vLLM registry class.
- Feature vector from intake.
- vLLM implementation dossier with fused op, parallel execution, kernel backend, config rewrite, and profiling wrapper alignment maps.
- Dimension reports for identity/config, attention/KV, compute dimensions, and profiling.
- KP hit matrix covering all available KP files, including non-hit entries when useful for audit.
- Op list and profiling plan with a traceable source for every op shape and kernel.
- Validation report from `ontos-plan-validator`.

## Guardrails

- Treat vLLM serving behavior as ground truth. Reference `model.py` explains architecture intent; it does not define the simulator target by itself.
- Keep KP content in KP files. Skills may index and route KP IDs, but should not duplicate full KP definitions.
- If a phase discovers a new constraint, pause that phase long enough to run `ontos-kp-manager` capture/update before downstream phases depend on stale assumptions.
- If the model has novel architecture, generate a research task for the owning dimension before generating implementation tasks.
