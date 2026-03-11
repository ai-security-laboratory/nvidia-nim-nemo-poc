# CLAUDE.md

## Project overview

PoC of a retail chatbot (RetailBot) built on NVIDIA NIM + NeMo Guardrails, running on Kubernetes (MicroK8s on Oracle VM).

## Architecture

| Component | Description | File |
|-----------|-------------|------|
| **NIM LLM** | Llama 3.1 8B Instruct served via NVIDIA NIM | `nim-llm-values.yaml` |
| **RetailBot app** | FastAPI app wrapping NeMo Guardrails | `retailbot_app.py`, `retailbot-deployment.yaml` |
| **NeMo Guardrails** | Colang 2.x rails (input/output/dialog) | `guardrails/colang/` |
| **pgvector** | PostgreSQL with vector extension | `pgvector.yaml` |
| **Mock Order API** | FastAPI mock for order lookups | `mock-order-api.yaml` |

## Key files

- `retailbot_app.py` — FastAPI entrypoint, loads guardrails from `/app/guardrails/colang`
- `guardrails/colang/config.yml` — NeMo model config (NIM endpoint: `http://nim-llama:8000/v1`)
- `guardrails/colang/main.co` — activates input/output/dialog rails
- `guardrails/colang/input_rails.co` — prompt injection, PII, topic checks
- `guardrails/colang/output_rails.co` — hallucinated policy check
- `guardrails/colang/dialog_rails.co` — identity verification before order lookup

## Kubernetes namespace

All resources deploy to the `retailbot` namespace.

## Secrets

- **NGC_API_KEY** — injected via K8s secret `ngc-api-key` (never hardcode)
- **PostgreSQL** — credentials managed via K8s secrets in production; demo values used in this PoC

## Deployment notes

- NIM deployed via Helm using `nim-llm-values.yaml`
- RetailBot uses an initContainer to install Python deps at runtime (no custom image)
- Guardrails config mounted as a ConfigMap
- pgvector uses `emptyDir` (not persistent) — replace with PVC for production
- RetailBot exposed via `NodePort 30080`

## Conventions

- Colang version: 2.x
- Python 3.11
- Do not commit `.env` files, kubeconfig, or any real credentials
