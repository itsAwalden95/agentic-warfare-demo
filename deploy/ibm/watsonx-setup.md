# Enabling the IBM watsonx.ai LLM Commander Layer

The demo runs fully without an LLM (a transparent heuristic strategist drives
each swarm's commander). Supplying watsonx.ai credentials upgrades the
**commander layer** so an LLM reasons about the battle state each cycle and
emits the strategy directive the swarm follows. This is the "hybrid" design:
fast simulated swarms, LLM-driven high-level strategy.

## 1. Provision watsonx.ai

1. In the [IBM Cloud catalog](https://cloud.ibm.com/catalog), create a
   **watsonx.ai** / **Watson Machine Learning** service instance.
2. Open [watsonx.ai](https://dataplatform.cloud.ibm.com/wx/) and create (or
   pick) a **project**. Copy its **Project ID**
   (Manage → General → Project ID).
3. Create an IAM **API key**: Manage → Access (IAM) → API keys → Create.

## 2. Choose a model

Any watsonx foundation model that supports text generation works. Good
commander models (instruction-following, low latency):

| Model ID                                  | Notes                              |
|-------------------------------------------|------------------------------------|
| `meta-llama/llama-3-3-70b-instruct`       | Strong reasoning (default)         |
| `mistralai/mistral-large`                 | Strong, fast                       |
| `ibm/granite-3-8b-instruct`               | IBM Granite, cheapest/fastest      |

## 3. Supply credentials

The container reads these environment variables (never hard-code them):

```bash
WATSONX_API_KEY=<your IAM api key>
WATSONX_URL=https://us-south.ml.cloud.ibm.com      # match your region
WATSONX_PROJECT_ID=<your project id>
WATSONX_MODEL_ID=meta-llama/llama-3-3-70b-instruct
```

- **Code Engine:** `code-engine-deploy.sh` picks these up and stores the secret
  for you.
- **IKS/ROKS:** create the `watsonx-creds` secret (see `iks-deployment.yaml`).
- **Local:** `export` them (or put them in `docker-compose.yml`) before running.

Install the optional SDK for the richer client path:

```bash
pip install ibm-watsonx-ai==1.1.25
```

If the SDK is absent the client falls back to the watsonx REST API over HTTPS.

## 4. Verify

`GET /api/health` returns `{"llm_active": true}` when credentials are present.
In the dashboard the commander badge flips from **heuristic** (amber) to
**watsonx LLM** (green), and each commander card shows the LLM's one-line
rationale for its current strategy.

## Cost & reliability notes

- The LLM is called only at the **commander cadence** (default every 8 ticks),
  not per-agent-per-tick, so a full simulation makes on the order of ~30 model
  calls, not thousands. This keeps the demo cheap and responsive.
- Every call has a short timeout and a hard fallback to the heuristic
  strategist, so a slow or throttled model never stalls the simulation.
