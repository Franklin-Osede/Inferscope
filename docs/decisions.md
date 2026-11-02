# Decisions

Each entry records what was decided, what else was considered, and why. Anything labelled a hypothesis or assumption has not been measured.

## D-001: Serve with vLLM v0.32.0

- **Decision:** Use `vllm/vllm-openai:v0.32.0`, pinned by digest in `deploy/versions.env`.
- **Alternatives:** v0.31.0, or tracking the latest release.
- **Why:** It was the latest stable release on 2026-11-01, and a digest cannot change underneath a published run.

## D-002: Use Mistral 7B Instruct v0.3

- **Decision:** `mistralai/Mistral-7B-Instruct-v0.3` at revision `c170c708c41dac9275d15a8fff4eca08d52bab71`.
- **Alternatives:** `Qwen/Qwen3-7B-Instruct`, which returned HTTP 401 from Hugging Face and could not be confirmed from an official source.
- **Why:** Hugging Face metadata shows Apache-2.0, not gated, architecture `MistralForCausalLM`. Anyone can reproduce the run without a token.
- **Still to verify:** That it runs on v0.32.0 (M2).

## D-003: Use a zonal cluster with g2-standard-8

- **Decision:** One zonal GKE cluster, a `g2-standard-8` GPU node and an `e2-standard-4` CPU node.
- **Alternatives:** `g2-standard-4`, which is cheaper, or a regional cluster.
- **Why:** 4 vCPUs could become the bottleneck at concurrency 64 and distort a GPU benchmark. A regional cluster adds cost and no value for one GPU.

## D-004: Use closed-loop load with vllm bench serve

- **Decision:** Run with `--request-rate inf --max-concurrency C`, with the client as an in-cluster job on the CPU pool.
- **Alternatives:** A custom load generator, or running the client on the GPU node.
- **Why:** The official tool is reproducible. In v0.32.0 source, requests wait on a semaphore before they start, which gives closed-loop behaviour. Keeping the client off the GPU node stops it competing with vLLM for CPU.
- **Verified:** All flags in this plan exist in the tagged v0.32.0 source.

## D-005: Use 1024 input and 256 output tokens, with scaled request counts

- **Decision:** `--random-input-len 1024 --random-output-len 256 --ignore-eos`, with `max(500, 32 × C)` requests per level: 500, 500, 1024 and 2048.
- **Alternatives:** A flat 500 requests, or 128 output tokens.
- **Why:** At C=64, a flat 500 leaves about 13% of requests in ramp-up or drain. 256 output tokens give more inter-token samples per request. The trade-off is that concurrency 1 takes longer.
- **Hypothesis, not a result:** v0.32.0 defaults to `max_num_seqs=256` on an L4, so a queue at C=64 would have to come from KV cache pressure. A rough estimate is 128 KiB per token and about 7 GiB of KV space, or roughly 45 concurrent requests.

## D-006: Disable prefix caching and vary the seed

- **Decision:** Serve with prefix caching off, using a different documented seed for each repetition and `--random-prefix-len 0`.
- **Alternatives:** Leaving caching on with a fixed seed, or resetting the cache between runs.
- **Why:** Prefix caching defaults to on where supported. A fixed seed would resend identical prompts in each repetition.
- **Still to verify:** The `--no-enable-prefix-caching` flag, expected from how v0.32.0 builds boolean arguments (M2), and that the hit metric stays at zero.

## D-007: Vary max_num_seqs in the experiment

- **Decision:** Compare the default with one lower value, chosen from the baseline and recorded before running.
- **Alternatives:** `gpu_memory_utilization`.
- **Why:** `max_num_seqs` directly controls how many requests run and how many wait, which is the behaviour the core question asks about.

## D-008: Run a self-deployed DCGM exporter

- **Decision:** Deploy `dcgm-exporter` by Helm, with GKE's managed DCGM collection turned off.
- **Alternatives:** Managed DCGM metrics.
- **Why:** Managed metrics go to Cloud Monitoring, and GKE's docs do not say a self-managed Prometheus can scrape them. New clusters enable them by default, and the docs warn against running both.

## D-009: Avoid resources that outlive teardown

- **Decision:** No load balancers or PVCs. Weights go on `emptyDir`, and access is by port-forward.
- **Alternatives:** A persistent model cache, which restarts faster.
- **Why:** PVC disks and load balancers can survive cluster deletion and keep billing.
- **Assumptions:** Preflight assumes $0.10/hr for the GKE fee and $0.03/hr for disks and IPs. The first comes from memory because the pricing page could not be read, and the second is an estimate. Check both before publishing costs.

## D-010: Keep pins in one file, and preflight in standard-library Python

- **Decision:** `deploy/versions.env` holds pins, and `infra/preflight.py` uses only the standard library. `pyproject.toml` and `uv.lock` are added at the repo root.
- **Alternatives:** A Bash preflight, or a new `scripts/` directory.
- **Why:** Python lets pricing and quota logic be unit tested without a cloud account. This adds no new top-level directory.

## D-011: Report unreached saturation honestly

- **Decision:** If saturation does not appear by C=64, report "not reached in the tested range".
- **Alternatives:** Adding levels such as 128.
- **Why:** The approved ladder is 1, 8, 32 and 64. Adding levels would need explicit approval.
