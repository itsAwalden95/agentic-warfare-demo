# Scaling Out: GPU & CPU on IBM Cloud

The small-scale demo is CPU-only and comfortably fits in a single Code Engine
container. This note maps the path from the demo to the scaled research
platform the client is actually aiming at, and where IBM Cloud GPU/CPU
resources plug in.

## Where compute actually goes

The architecture has two compute tiers with very different profiles:

| Tier                     | Workload                                   | Resource        |
|--------------------------|--------------------------------------------|-----------------|
| **Swarm simulation**     | Thousands of agent decisions per tick, graph state transitions | CPU, scales horizontally |
| **LLM commander / agents** | Foundation-model inference for strategy (hybrid) or per-agent reasoning (full-LLM) | **GPU** (watsonx.ai managed, or self-hosted) |

For the hybrid demo the GPU need is light (watsonx.ai is managed; you pay per
token). GPU becomes central only when you move toward **per-agent LLM
reasoning** or **training RL agents** at scale.

## Option A — Managed inference (recommended first step)

Keep using **watsonx.ai**. IBM runs the GPUs; you scale by concurrency and
token budget. No GPU provisioning. This covers the hybrid design and moderate
full-LLM experiments.

## Option B — GPU VPC virtual servers

For self-hosted inference (e.g. vLLM serving an open model to hundreds of
agents) or RL training, provision GPU profiles in a **VPC**:

```bash
# Example: provision an L40S / H100-class GPU VSI in a VPC
ibmcloud is instance-create cyber-range-gpu \
  <vpc-id> <zone> <gpu-profile> <subnet-id> \
  --image <ubuntu-gpu-image> --keys <ssh-key>
# GPU profiles: gx3 family (NVIDIA L40S), and H100 where available in-region.
```

Typical layout:
- A **GPU node** (or pool) runs a vLLM/TGIS inference server.
- Point the simulation at it by setting `WATSONX_URL` to your own OpenAI-/REST-
  compatible endpoint, or extend `watsonx_client.py` with a second backend.
- Keep the **simulation** on cheaper CPU instances or Code Engine; only
  inference needs the GPU.

## Option C — GPU on IKS/ROKS

Add a **GPU worker pool** to the cluster and schedule inference pods with the
NVIDIA device plugin:

```bash
ibmcloud ks worker-pool create gpu \
  --cluster <cluster> --flavor <gpu-flavor> --size-per-zone 1
```

Run the simulation deployment (this repo) on CPU nodes and a separate inference
Deployment (vLLM) on the GPU pool, wired together by a Service. This is the
natural home for the full research platform: RBAC, network policy to model the
OT/IT boundary for real, autoscaling of the swarm tier, and GPU scheduling for
inference.

## Scaling the simulation tier

The in-memory engine runs one scenario per process. To scale the *research*
(many concurrent scenarios / a sweep over configs):

- Run N simulation pods, each its own scenario (embarrassingly parallel).
- Put a results store (IBM Cloud Databases / COS) behind them for metrics.
- Drive parameter sweeps (swarm sizes, skills, topologies) as a Kubernetes Job
  array or Code Engine jobs — the headless runner (`run_local.py` /
  `cyberrange.engine.run_headless`) is built for exactly this.
