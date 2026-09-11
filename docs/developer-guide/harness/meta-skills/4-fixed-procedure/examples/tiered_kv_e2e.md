---
name: tiered-kv-e2e
description: 判定并执行 Ontos 多级 KV 存储的 vLLM 实测、硬件 profiling、Ontos simulation 与结果分析，复用历史 ground truth 并选择最小重跑范围。
whenToUse: 当请求涉及 tiered KV、多级存储、offloading、profiling、simulation、E2E 实验、历史结果复用或正确性分析时使用。
---

# Tiered KV E2E

## 目标与响应契约

本 skill 服务于 `/softhome/wangziping/code/ontos` 当前多级 KV 存储分支（rebase 后为 `feat/tiered-kv`，基于 vLLM **0.26**）。它的核心职责不是把三个阶段机械地全部重跑，而是根据用户本次改动、当前 git diff、配置引用和已有 artifact，判定最小且充分的重跑集合。

对每个涉及本流程的用户请求，先做只读 preflight，再在回答或执行计划中明确写出 `E2E 重跑判定`，至少包含以下四行：

| 阶段 | 结论 | 范围 | 理由/复用依据 |
|---|---|---|---|
| vLLM 实测 | rerun / reuse / N/A | model + mode + workload | serve/bench/client/dataset/hardware 是否变化 |
| profiling | rerun / reuse / N/A | compute/PCIe/filesystem + model/TP | profile contract、硬件和 collector 是否变化 |
| simulation | rerun / reuse / N/A | 对应 `*sim.yaml` 子集 | 仿真代码、配置、trace、profile、predictor cache 是否变化 |
| analysis | rerun / reuse / N/A | 全量或受影响模型/mode | source artifact 是否变化 |

用户已经明确要求执行时，不因耗时再次询问确认；先报告判定，再按判定执行。若依赖缺失或 contract 不兼容，不能以“旧实验曾经成功”为理由强行仿真，必须报告具体路径和恢复命令。

历史输出视为只读 ground truth，除非它属于已经查过版本、明确归入 `vllm026` 或 `legacy_vllm023` 的 run。不要覆盖 canonical run、删除旧 raw run 或用旧文件名掩盖新 schema。新实验使用新 UTC run id 或新的输出目录，并在最终报告中记录配置、git revision、环境、profile metadata hash 和实际命令。

## 固定路径与环境

> **环境基线（2026-08 起）**：执行一律通过 **`./run-docker.sh` 起的 docker 容器**，容器 = base `vllm/vllm-openai:v0.26.0` + `docker/vllm/0.26.0/requirements/kv-connectors.txt`（含 tiered KV 依赖）+ `docker/requirements/ontos.txt`，即 vLLM **0.26**，与团队一致。不再依赖本地 `.venv` 或 `...-vllm023` venv。容器内仓库挂载在 `/workspace`，`python` 即容器环境。

- 代码仓库：`/softhome/wangziping/code/ontos`（分支 `feat/tiered-kv`；origin = `ontos.git`）
- 实验根：`/share/wangziping/outputs/tiered-kv-e2e`
- **当前 canonical 根（vLLM 0.26）**：`<experiment-root>/vllm026/`
  - 顶层配置：`<experiment-root>/vllm026/config/`
  - profiling 配置：`<experiment-root>/vllm026/profiling/configs/`
  - profiling artifact：`<experiment-root>/vllm026/profiling/artifacts/`
  - profiling manifest：`<experiment-root>/vllm026/profiling/manifests/`
  - vLLM 实测（含 trace）：`<experiment-root>/vllm026/vllm_results/`（按 `llama-8b`/`llama-70b` → `gpu_cpu`/`gpu_only`/`sharegpt`/`tiered` 两层分类；原始 run 记录在 `runs/` 下）
  - Ontos 仿真：`<experiment-root>/vllm026/simulation_results/`
  - 分析：`<experiment-root>/vllm026/analysis/`
- 共享数据集（不版本化，0.23/0.26 同一套 workload JSONL）：`<experiment-root>/data/datasets/`
- 历史 vLLM 0.23 冻结：`<experiment-root>/legacy_vllm023/`（注意其内部仍混入少量早期 0.26 数据，如 `legacy_vllm023/vllm026/`、`reruns/vllm026-tiered-sim-*`，勿整体当作 0.23）。旧顶层 `config/`、`profiling/`、`pipeline_results/`、`analysis/` 已于 2026-08-27 迁移归入 `vllm026/` 的 canonical 布局。

多级存储真实 vLLM 现在使用 **vLLM 0.26.0**（容器内）。真实 vLLM 不读取 Ontos profile；Ontos simulation 才读取 profile CSV 和相邻 metadata。两者不能互相替代。

**版本断层提醒**：本分支已从 vLLM 0.23 迁移到 0.26。按重跑决策表，“更换 vLLM 版本 → vLLM rerun + profiling rerun”。因此 `legacy_vllm023/` 及其中的 profile 名（如 `pcie_h100_tp1_vllm023_...`、`filesystem_*_v5.csv`）是 **0.23 时代 ground truth，对 0.26 已过期**；0.26 的 profiling 需按新 collector 重新采集，且 artifact 名应带 `_vllm026`/`_v5` 化以与 0.23 区分。

## 三阶段数据流

```text
serve.yaml + bench.yaml + client.yaml + dataset + vLLM 0.26
    -> vLLM metrics / request trace / observability

compute config + PCIe collector + filesystem collector
    -> profile CSV + raw CSV + metadata

request trace + *sim.yaml + validated profile artifacts
    -> Ontos raw run + request metrics + KV transfer/offloading metrics

vllm026/vllm_results + vllm026/simulation_results + vllm026/profiling/artifacts
    -> vllm026/analysis/runs/<new-run-id>/{tables,figures,report}
```

配置引用链必须按此顺序检查：

```text
*pipeline.yaml -> *bench.yaml -> *serve.yaml and *client.yaml -> dataset
*sim.yaml -> server_config (*serve.yaml), trace_root, profile/timing JSON, predictor cache
analysis_config.yaml -> vllm026/vllm_results (vllm_pipeline_results) and vllm026/simulation_results (ontos_pipeline_results) and vllm026/profiling/artifacts
```

Profiling 与 simulation 的边界是硬约束：`ontos/profiling`（rebase 前为 `vidur/profiling`）只能负责硬件采集和 artifact 写出；仿真启动时独立验证并读取 artifact。不要为了让仿真通过而在 runtime 中绕过 metadata、范围、TP、block size 或 schema 校验。

## 执行入口（docker）

三个阶段都通过容器跑。仓库根下执行（`run-docker.sh` 会把 `"$@"` 传给容器内 `bash`，`--workdir` 是 `/workspace`）：

```bash
cd /softhome/wangziping/code/ontos
# 一句式：./run-docker.sh -c '<命令>'
# 交互式：./run-docker.sh   （进入容器 /workspace 的 bash）
./run-docker.sh -c 'python -m automation.pipeline vllm --config /workspace/<cfg>.yaml'
```

要点：
- 容器内 `python` 即 vLLM 0.26 + ontos deps；不要再依赖仓库里的 `.venv/bin`（容器里没有）。
- 自动化脚本若内部用 `$(pwd)/.venv/bin` 前置 PATH 或裸 `python`，在容器内会指向容器 python：以容器内的 `python` 为准，`.venv` 包装脚本可能失效，优先直接用 `python -m automation.pipeline ...`。
- `run-docker.sh` 默认镜像 `ontos:vllm-${VLLM_VERSION}`（缺省 `ontos:vllm-0.26.0`）；若本地只 pull 了基础镜像，先用 `./build-docker.sh`（或设 `IMAGE_NAME=vllm/vllm-openai:v0.26.0`）。GPU 及相关 mount（`/data_gpu`、`/share`、`/softhome`，以及 tiered KV spill 用的 `/data/wangziping/kv-runtime/tiered-kv-e2e`）由 `run-docker.sh` 处理。
- **env 变量要写进 `-c` 里，不能写在宿主**。`run-docker.sh` 只 `--env` 转发固定项（`USER/LOGNAME/NVIDIA_*/NCCL_*/NVSHMEM_*/GLOO_*`），不转发宿主任意 env。凡脚本要读的 `CONFIG/BACKGROUND/DRY_RUN/LOG_DIR/LOG_FILE/CUDA_VISIBLE_DEVICES` 以及 `source *.env` 的 profiling 配置，都要放在 `-c` 的命令串里（在容器内设置/`source`）。宿主 `CONFIG=... ./run-docker.sh -c '...'` 是**无效写法**，会回退到脚本内置默认配置。
- 跑多级存储（gpu-cpu/tiered/sharegpt）如遇 v0.26 offload mmap 累积，`df -h /dev/shm` 且清理本用户过期 `vllm_offload_*.mmap`；让 sweep 自动清理需在 bench `env` 设 `VLLM_SWEEP_MMAP_CLEANUP: "1"`（仅独占单 GPU 串行时安全）。

**容器挂载要点（两条重要事实）**：
- `/workspace` 是宿主仓库目录的 **bind mount**（`run-docker.sh` 里 `--volume "${HOST_WORKDIR}:${CONTAINER_WORKDIR}"`，`HOST_WORKDIR=仓库目录`），**不是拷贝**。在宿主机改代码即时同步到容器 `/workspace`；执行中遇到 bug 直接改宿主机代码即可生效，无需重建镜像或手动拷进容器。Dockerfile 只把 requirements 装进镜像，真正的项目代码是挂载进去的。
- `/share` 与 `/share_data` 是**同一个 NFS 导出** `10.97.128.244:/share` 在宿主机挂的两个路径；容器只挂 `--volume /share:/share`。因为 `/share_data/...` 与 `/share/...` 是同一份数据（inode 相同），容器通过 `/share/wangziping/outputs/tiered-kv-e2e` **已经能访问**所有配置与数据集，无需再把数据复制到别处。旧 vLLM 0.23 数据保留在原处（`legacy_vllm023/`），不要动它；旧顶层目录已迁入 `vllm026/`。
- **tiered/gpu-cpu 的 KV spill 用节点本地盘** `/data/wangziping/kv-runtime/tiered-kv-e2e`（`/dev/md1p1` 本地 RAID，`run-docker.sh` 已把该子路径 rw 挂进容器）。**filesystem profile 也是在这块盘上采的**（`metadata.storage_identity.mount_path`），所以 serve 的 `secondary_tiers[*].root_dir` 必须指 `/data/...`，否则真实文件性能与 profile 不符。不要改到 `/share`（NFS，网络+共享，KV 小随机访问会慢且与 profile 不一致）。

## Canonical 实验矩阵

模型只有两类：Llama 3.1 8B（TP=1、GPU 0）和 Llama 3.1 70B（TP=4、GPU 0-3）。共同的服务参数通常是 BF16 KV、`block_size=16`、FlashInfer、piecewise CUDAGraph、`max_model_len=8192`、`gpu_memory_utilization=0.9`、prefix caching、`max_num_batched_tokens=8192`、`max_num_seqs=256`，但每次仍以 YAML 为准。

### KV replay workload

所有记录是 32 output tokens、block size 16 的 custom JSONL；vLLM client 必须保持 `skip_chat_template: true`、`disable_shuffle: true`、`request_trace: true`，不要为了生产式压力测试设置 `max_concurrency`，优先 sweep QPS。

| mode | storage | 每模型 contexts | 每模型 QPS cases | 对应 pipeline/sim |
|---|---|---|---:|---|
| `gpu-only` | GPU only | `reuse_p2048_n032`, `hot_cold_p4096_n256` | 4（2 contexts x 2 QPS） | `vllm-kvreplay-*-gpu-only-{pipeline,bench,serve,client}.yaml` / `ontos-kvreplay-*-gpu-only-sim.yaml` |
| `gpu-cpu` | GPU + CPU | `reuse_p2048_n032`, `gpu_pressure_p4096_n128`, `hot_cold_p4096_n256` | 6 | `...-gpu-cpu-...` |
| `tiered` | GPU + CPU + filesystem | 上述三类 + `tier_pressure_p2048_n320`, `tier_pressure_p4096_n256`, `tier_pressure_p7168_n192`, `large_pressure_p4096_n512` | 14 | `...-tiered-...` |
| `sharegpt` | 当前配置也启用 GPU + CPU + filesystem | 一个 `2k` context，public ShareGPT 派生 JSONL，1000 prompts | 2 | `vllm-sharegpt-*` / `ontos-sharegpt-*-sim.yaml` |

历史 canonical analysis 覆盖是每模型 `gpu-only=4`、`gpu-cpu=6`、`tiered=14`、`sharegpt=2`，共 26 cases/model、52 cases，总配对请求 30624（这是 0.23 时代的结论）。这些指标针对 **0.26 需要重采 vLLM + 重跑 simulation 后重新认定**；不要拿 0.23 的 52/52 valid 当作 0.26 的通过证据。

数据文件在（共享，不版本化）：

```text
<experiment-root>/data/datasets/kv_replay/llama3-8b_*.jsonl
<experiment-root>/data/datasets/kv_replay/llama3-70b_*.jsonl
<experiment-root>/data/datasets/sharegpt/ShareGPT_V3_unfiltered_cleaned_split.llama3-8b.custom_{1k,2k,4k}.jsonl
<experiment-root>/data/datasets/sharegpt/ShareGPT_V3_unfiltered_cleaned_split.llama3-70b.custom_{1k,2k,4k}.jsonl
```

### 存储语义

- GPU-only simulation 使用 `kv_offloading_type=none`，没有外部 KV transfer。
- GPU+CPU 使用 `cpu_offloading`，CPU 容量、LRU、prompt-only 策略和双向 PCIe link 必须与真实 serve 配置对应。
- tiered/sharegpt 使用 `tiering_offloading`：GPU -> CPU -> filesystem，回读为 filesystem -> CPU -> GPU；filesystem routes 使用 replica-scoped directional queue。
- 真实 vLLM JSON 中 secondary type 是 `fs`；Ontos timing JSON 中 backend type 是 `filesystem`，不要混写。
- `block_size=16` 是 token block；filesystem profile 的 `block_bytes` 是逻辑文件任务大小，8B 当前 contract 为 2 MiB，70B 为 5 MiB。

## Stage 1: vLLM 实测

入口（docker）：

```bash
cd /softhome/wangziping/code/ontos
./run-docker.sh -c 'python -m automation.pipeline vllm \
  --config /workspace/vllm026/config/llama-8b/vllm-kvreplay-llama-8b-gpu-cpu-pipeline.yaml'
```

执行前先用 `--dry-run` 检查 pipeline -> bench -> serve/client 的解析；真实运行会由 pipeline 管理 `vllm bench sweep serve` 生命周期。历史成功配置的 `orchestration.results_root` 和 run id 必须保留，不要将新结果写回旧 run；**新 0.26 结果写到 `<exp>/vllm026/`** 下。

**推荐用 `automation/scripts/bench/` 下的脚本（封装同一 `automation.pipeline vllm --config` 入口，处理 frozen-worktree）**，在容器内跑：

```bash
cd /softhome/wangziping/code/ontos
./run-docker.sh -c 'cd /workspace && \
  CONFIG=/workspace/vllm026/config/llama-8b/vllm-kvreplay-llama-8b-gpu-cpu-pipeline.yaml \
  BACKGROUND=false \
  ./automation/scripts/bench/bench_single_model.sh'
```

> **重要：env 要写进 `-c` 里**。`run-docker.sh` 不会把宿主机任意环境变量转发进容器（只 `--env` 转发 `USER/LOGNAME/NVIDIA_*/NCCL_*` 等固定项），所以 `CONFIG=... ./run-docker.sh -c '...'` 这种写在宿主机上的变量**到不了容器**，脚本会回退到内置默认（如 `single_model_pipeline.yaml`）跑错配置。`CONFIG/BACKGROUND/DRY_RUN/LOG_DIR/LOG_FILE/CUDA_VISIBLE_DEVICES` 都要写在 `-c` 的字符串里。

要点：
- 容器内 `PATH` 已含容器 `python`；脚本里若依赖仓库 `.venv/bin` 会退化到容器 python（即 0.26），可接受。
- 脚本只吃 `CONFIG`（以及 `BACKGROUND/LOG_DIR/LOG_FILE/DRY_RUN`）环境变量，**不支持 `--set`**。若要把 `orchestration.results_root` 指向新的隔离输出目录，先改配置文件里的 `orchestration.results_root`，再通过 `CONFIG` 指向该配置文件。
- 跑多级存储（gpu-cpu/tiered/sharegpt）如遇 v0.26 offload mmap 清理需求，导出 `VLLM_SWEEP_MMAP_CLEANUP=1`（仅独占单 GPU sweep）；注意 bench YAML 的 `env.VLLM_SWEEP_MMAP_CLEANUP` 会覆盖进程环境变量。

**/dev/shm 空间检查与清理（v0.26 多级存储前置条件）**：tiered/gpu-cpu 的 CPU-KV tier 按 `cpu_bytes_to_use` 在 `/dev/shm` 创建 `vllm_offload_<engine-id>.mmap`；若 `VLLM_SWEEP_MMAP_CLEANUP=0` 会累积填满，报 `insufficient /dev/shm space`。跑前 `df -h /dev/shm` 需剩余 ≥ `cpu_bytes_to_use`；已满则删本用户过期 `vllm_offload_*.mmap`，不要动其他用户的 `joblib_*` 等。

真实 vLLM 实测的可复用边界：

- 只改 `ontos/kv_cache/**`、Ontos scheduler/controller/metrics、仿真 builder 或分析代码：通常 reuse vLLM，复用原始 trace 和 vLLM metrics。
- 改 `*serve.yaml` 的模型、TP/DP/PP、KV dtype、block size、GPU memory、prefix caching、offloading JSON、filesystem root/thread 参数：rerun 受影响的 vLLM mode。
- 改 `*client.yaml` 的 dataset、context、prompt 数、QPS、seed、trace 开关，或修改 dataset 内容/顺序：rerun受影响 model/mode；旧 trace 不能与新输入混配。
- 改 `*bench.yaml`、`*pipeline.yaml` 的 serve/client 引用、results root、observability、timeout 或实际运行参数：至少验证 trace/metrics 是否仍满足 pairing；若运行语义改变则 rerun。
- 更换 vLLM 版本、模型权重、GPU、驱动、TP topology 或硬件：vLLM rerun，且通常同时需要对应 profiling rerun。**0.23 → 0.26 即属此类**。

历史 pipeline run id（0.23 时代，用作定位、不要覆盖；0.26 新 run 在 `vllm026/vllm_results/`（vLLM）与 `vllm026/simulation_results/`（仿真）下重建）：

```text
8B gpu-only:  <experiment-root>/legacy_vllm023/... (vllm-0-23-0 pipeline run)
...
```

输出重点是 `vllm026/vllm_results/request_traces/`、`vllm026/vllm_results/metrics/vllm/` 或每个 pipeline run 下的 metrics/raw，以及 tiered/sharegpt 的 observability 和 filesystem snapshots。trace 至少要能校验到 arrival、prefill、decode、prompt token fingerprint；不要只保留 percentile 汇总。

## Stage 2: profiling

Profiling 按 model + TP + hardware/storage contract 复用，不按 QPS 或 workload 重复采集。0.23 的 profile artifact 对 0.26 已过期（vLLM 版本变化 → rerun profiling）。

### Compute

8B：`<exp>/vllm026/profiling/configs/llama3_8b_compute_tp1.yaml`；70B：`llama3_70b_compute_tp4.yaml`。覆盖 attention 和 MLP；70B 覆盖 TP=4 all-reduce。

```bash
cd /softhome/wangziping/code/ontos
./run-docker.sh -c 'cd /workspace && \
  CUDA_VISIBLE_DEVICES=0 \
  CONFIG=/workspace/vllm026/profiling/configs/llama3_8b_compute_tp1.yaml \
  ./automation/scripts/profiling/profile_single_model.sh'

./run-docker.sh -c 'cd /workspace && \
  CUDA_VISIBLE_DEVICES=0,1,2,3 \
  CONFIG=/workspace/vllm026/profiling/configs/llama3_70b_compute_tp4.yaml \
  ./automation/scripts/profiling/profile_single_model.sh'
```

compute profile 需要重跑的典型原因：模型/TP/DP/PP、attention backend、KV dtype、block size、max sequence/batch/chunk、CUDAGraph capture contract、GPU/驱动/vLLM 版本或 profiler 实现改变。若只是 storage runtime 改动，不重跑 compute。

### GPU <-> CPU PCIe

8B TP=1 / 70B TP=4：配置在 `vllm026/profiling/configs/*_cpu_pcie_tp{1,4}.env`；artifact name 带 `_vllm026`（0.23 为 `pcie_h100_tp1_vllm023_...`）。契约：per-worker job、custom sizes 覆盖范围、metadata 的 expected TP / size count / grid / direction-state matrix / vLLM version 必须与 simulation timing JSON 一致。

```bash
cd /softhome/wangziping/code/ontos
./run-docker.sh -c 'cd /workspace && \
  source /workspace/vllm026/profiling/configs/llama3_8b_cpu_pcie_tp1.env && \
  ./ontos/profiling/tiered_kv/profile_cpu_pcie.sh'

./run-docker.sh -c 'cd /workspace && \
  source /workspace/vllm026/profiling/configs/llama3_70b_cpu_pcie_tp4.env && \
  ./ontos/profiling/tiered_kv/profile_cpu_pcie.sh'
```

PCIe profile 需要重跑的典型原因：GPU ordered list/TP、PCIe topology、pinned-memory layout、vLLM version、collector grid/trial/schema 或 hardware 改变。

### CPU <-> filesystem

8B 用 `llama3_8b_filesystem_2mib.env`，70B 用 `llama3_70b_filesystem_5mib.env`；16 read + 16 write workers、O_DIRECT、同一 `/data` filesystem、采集目录预先创建且为空。

```bash
cd /softhome/wangziping/code/ontos
./run-docker.sh -c 'cd /workspace && \
  source /workspace/vllm026/profiling/configs/llama3_8b_filesystem_2mib.env && \
  ./ontos/profiling/tiered_kv/profile_filesystem.sh'

./run-docker.sh -c 'cd /workspace && \
  source /workspace/vllm026/profiling/configs/llama3_70b_filesystem_5mib.env && \
  ./ontos/profiling/tiered_kv/profile_filesystem.sh'
```

filesystem profile 需要重跑的典型原因：mount/device/RAID/filesystem、I/O mode、block bytes、read/write worker count、vLLM version、collector schema/grid/trials 或 profile implementation 改变。

**0.23 → 0.26 的 profile 契约**：0.23 的 `pcie_*_vllm023_*` 与 `filesystem_*_v5.csv` 是 0.23 时代产物，Ontos 在 0.26 下应换用按当前 collector 重采的 0.26 artifact；`sim_profile_8b/70b` 符号链接树检查时要跟随链接，不能把不跟随 symlink 的 glob 当作缺失。

## Stage 3: Ontos simulation

八个入口（`vllm026/config/{llama-8b,llama-70b}/ontos-{kvreplay,sharegpt}-*-sim.yaml`）。历史 matrix runner：

```bash
cd /softhome/wangziping/code/ontos
./run-docker.sh -c 'python -m automation.pipeline.trace_matrix \
  --config /workspace/vllm026/config/llama-8b/ontos-kvreplay-llama-8b-tiered-sim.yaml --dry-run'

./run-docker.sh -c 'python -m automation.pipeline.trace_matrix \
  --config /workspace/vllm026/config/llama-8b/ontos-kvreplay-llama-8b-tiered-sim.yaml'
```

也可用 `python -m automation.pipeline simulate --config ...`。先 dry-run 确认每个 workload 的 trace file、raw output、metrics JSON 和实际 command 再执行。每个 simulation config 的 `qps_list` 是 `[4, 8]`，不会重新生成 arrival trace。

Simulation rerun 判定：

- 改 `ontos/kv_cache/tiered_storage/**`、adapter/controller/runtime/scheduler、KV metrics 或相关 simulation code：重跑受影响 mode；通常 reuse vLLM 和 profiling。
- 改 `*sim.yaml` 的 storage topology、capacity、placement、link/timing、predictor cache、trace root、workload contexts、QPS、metrics：重跑受影响 model/mode；若 profile contract 字段改变，先 profiling preflight。
- 改 `automation/pipeline/trace_matrix.py`、simulation builder、naming、metrics post-processing：重跑 simulation，并重新分析。
- 改 profile CSV/metadata、profile path、compute artifact 或 predictor input：重跑依赖它的 simulation；predictor cache 只有在 model、TP、profile identity、predictor settings 和 offloading/cache mode 全部一致时才可复用；必要时用新 cache directory。
- 改 workload dataset、serve parameters 或真实 trace：对应 vLLM + simulation 都要重跑。

输出重点：

```text
vllm026/simulation_results/simulator_outputs/<model>/<mode>/.../request_metrics.csv
vllm026/simulation_results/metrics/ontos/<model>/<mode>/*.json
.../plots/kv_transfer_metrics.json
.../plots/kv_offloading_metrics.json
```

启动仿真之前，必须验证：trace 文件存在且输入 fingerprint/请求顺序符合 vLLM；`expected_tp_size`、profile size grid、filesystem block bytes、worker counts 和 metadata hash 合约通过。缺任一硬 contract 就停止，不要让 predictor 外推或 clamp。

## Stage 4: analysis

分析项目是只读 source analyzer，结果写新目录（容器内）：

```bash
cd /softhome/wangziping/code/ontos
./run-docker.sh -c 'cd /workspace && python -m unittest discover -s tests -v && python run_analysis.py --run-id <new-utc-or-descriptive-id>'
```

默认读取 `vllm026/analysis_config.yaml` 的：

```text
source_paths.vllm_pipeline_results: vllm026/vllm_results
source_paths.ontos_pipeline_results: vllm026/simulation_results
source_paths.profiling_artifacts: vllm026/profiling/artifacts
models: llama-8b, llama-70b
modes: gpu-only, gpu-cpu, tiered, sharegpt
percentiles: p50, p95, p99
```

不要覆盖 `legacy_vllm023` 或历史 `analysis/runs/final-20260806-v6`。分析生成 `case_manifest.csv`、`issue_summary.csv`、`paired_requests.csv`、`endpoint_metrics.csv`、`kv_metrics.csv`、`predictor_evidence.csv`、`predictor_summary.csv`、`profiling_inventory.csv`、figures 和 Markdown report。

配对与判定：

- 以 trace 顺序、prefill 长度和 prompt-token SHA-256 fingerprint 配对；不能只按文件名或 block hash 整数配对。
- 内容 hash 精确匹配优先；只有 arrival/测量列不同而逻辑输入相同时，才使用逻辑输入匹配并标记诊断。
- 检查请求数、prefill/decode token、arrival 顺序、cached tokens 不超过 prefill；invalid pairing 从性能汇总排除但保留 issue。
- TTFT、TPOT、E2E、吞吐报告 signed/absolute/relative error；p99 小样本要谨慎解释。
- cache hit rate 优先按 token 口径；external connector counters 不自动等同 filesystem hit。
- vLLM filesystem 物理空间、文件头/对齐开销和 Ontos logical transfer 不同层级，必须单独标注。
- 没有统一 E2E 性能 hard pass threshold；profile schema/metadata/TP/grid/range/drift 和输入配对是硬门槛。

Tiered 功能语义验收至少检查 GPU->CPU、CPU->filesystem、filesystem->CPU、CPU->GPU 四个方向存在合理的非零完成证据，并检查 transfer-completion wakeup 后 scheduler event 的证据（对专门 direct smoke test 还要检查 `KVTransferWakeupEvent` 链）。

## 重跑决策表

先对 `git diff --name-only`、用户本次描述、被引用 YAML 和 artifact existence 做集合判定：

| 变更/事件 | vLLM | profiling | simulation | analysis |
|---|---|---|---|---|
| 仅 `ontos/kv_cache/tiered_storage/**`、Ontos scheduler/controller/metrics | reuse | reuse | affected modes | yes |
| 仅 `ontos/profiling/**` collector 或 profiling config | reuse | affected artifact/model/TP | all modes consuming artifact | yes |
| `*serve.yaml` 的 offload/storage/model/TP/KV/block/hardware 参数 | affected modes | if hardware/block/thread contract changed | affected modes after trace | yes |
| `*client.yaml`、dataset 内容/顺序、QPS/context/prompt/seed/trace switch | affected modes | reuse | same affected modes | yes |
| `*bench.yaml`/`*pipeline.yaml` 改引用、运行参数、observability 或 output root | validate; rerun if trace semantics change | reuse unless hardware contract | affected traces | yes |
| `*sim.yaml`、`trace_matrix.py`、Ontos builder/naming/metrics | reuse | reuse unless profile contract changed | affected configs | yes |
| profile CSV/metadata 或 profile path changed | reuse | collect only if artifact must be regenerated | all consumers | yes |
| model weights, vLLM version, GPU/driver/PCIe/FS identity changed | affected models/modes | affected compute/transfer artifacts | all dependent configs | yes |
| **vLLM 0.23 → 0.26（本分支迁移）** | **all** | **compute + PCIe + filesystem** | **all dependent** | yes |
| only `analysis/**` 或 `analysis_config.yaml` | reuse | reuse | reuse | yes |
| only tests/docs/comments | reuse | reuse | reuse | no, unless behavior/config was indirectly changed |

“affected modes” 要具体展开：8B 与 70B 独立；PCIe TP1/TP4 独立；filesystem 2 MiB/5 MiB 独立；gpu-only 不因 CPU/filesystem profile 变化而重跑；gpu-cpu 不因 filesystem profile 变化而重跑；tiered 和 sharegpt 通常共享对应模型的 PCIe + filesystem profile；**0.23→0.26 则所有都受影响**。

## 典型执行顺序与安全检查

1. 读取 `AGENTS.md`、`git status`、当前 diff，确认用户改动不是本任务之外的 dirty worktree。
2. 解析本次变更影响的 model/mode/workload，输出 `E2E 重跑判定`。
3. 对需要执行的阶段运行 dry-run/preflight：路径、环境（容器是否可起）、GPU 空闲、profile metadata、trace fingerprint、输出目录。
4. 若 profiling 必须重采，先停止 vLLM 和其他 GPU workload；PCIe collector 拒绝 busy GPU，filesystem collector 需要干净 mount/root。
5. 先运行最小受影响 simulation subset；确认 metrics/raw 产物完整后再扩展全矩阵。
6. 为 analysis 生成新的 `--run-id`，保存 report、issues 和命令日志。
7. 最终汇报：实际执行的阶段和子集、复用的旧结果路径、artifact hashes/contract、结果目录、失败或剩余风险。

## 常见错误

- 把 `run_direct_e2e_experiment.py` 的 deterministic microbenchmark 当作真实 vLLM serving ground truth；它是 adapter/runtime 语义 smoke test。
- 把 vLLM 导出的 trace 当成 Ontos CSV trace，或反过来；两者格式和用途不同。
- 只编辑生成 YAML 却继续执行旧 manifest/旧 command；profile directory 或 timing JSON 可能仍指向旧路径。
- 用旧 schema、不同 block bytes、不同 TP 或不同 filesystem identity 的 profile；runtime 应拒绝，agent 也应在 preflight 阶段拒绝。
- 把 CPU/filesystem 逻辑容量、物理文件空间、completed bytes 和 token cache hit rate 混成一个单位。
- 看到多个 Ontos raw candidate 就随意删除；analysis 的 canonicalization 会记录 `multiple_canonical_raw_runs` / `noncanonical_raw_candidate`，应保留诊断。
- 修改多级存储仿真代码后重跑昂贵 vLLM，只会增加成本且不能证明仿真修复；只有 trace/真实服务语义变化才触发 Stage 1。
- **把 0.23 的 profile/trace/vLLM 结果当作 0.26 的 ground truth**；0.23→0.26 属 vLLM 版本变化，会连带重跑。
- **把 `legacy_vllm023/` 里混装的 0.23 与少量 0.26（`legacy_vllm023/vllm026/`、`reruns/vllm026-tiered-sim-*`）当成 pures 0.23 或 pures 0.26**；它们需要逐 run 查版本后再归类，不要整体引用。0.26 的 canonical 数据一律在 `vllm026/` 下。

## 专用小型 smoke test

仓库内 `experiments/kv_multilevel_storage/run_direct_e2e_experiment.py` 是进程内 direct-config 语义验证，默认 8B，G0/G1/G2/G3 组；G3 要求四条 link 都有非零 completed transfer，并检查 transfer wakeup 到 same-time scheduling event。它不能替代 `vllm026/` 的真实 vLLM + profiling + simulation 对照。需要快速验证 adapter/runtime 时可单独运行，但在 `E2E 重跑判定` 中标为基础 correctness smoke，而不是 Stage 1 ground truth。
