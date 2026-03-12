# CLAUDE.md

## Project overview

PoC of a retail chatbot (RetailBot) built on NVIDIA NIM + NeMo Guardrails, running on Kubernetes (MicroK8s on Oracle VM).

## Architecture

| Component | Description | File |
|-----------|-------------|------|
| **NIM LLM** | Llama 3.1 8B Instruct served via NVIDIA NIM | `nim-llm-values.yaml` |
| **RetailBot app** | FastAPI app — Python input checks + NeMo output rail | `retailbot_app.py`, `retailbot-deployment.yaml` |
| **NeMo Guardrails** | Colang 1.0 — output rail only (input checking in Python) | `guardrails/colang/` |
| **pgvector** | PostgreSQL with vector extension | `pgvector.yaml` |
| **Mock Order API** | FastAPI mock for order lookups | `mock-order-api.yaml` |

## Key files

- `retailbot_app.py` — FastAPI entrypoint; input checks (injection/PII/topic) run in Python before SK; output rail (hallucination check) in Python
- `sk_agent.py` — Semantic Kernel kernel + CRM/ERP/Logistics plugins
- `k8s/` — all Kubernetes manifests (retailbot, pgvector, mock services, NIM Helm values)
- `guardrails/colang/config.yml` — NeMo model config; NIM endpoint: `http://nim-llm.nim.svc.cluster.local:8000/v1`; `colang_version: "1.0"`
- `guardrails/colang/main.co` — catch-all `define flow main` required by NeMo
- `guardrails/colang/input_rails.co` — Colang subflow definitions (documentation; not the enforcement layer)
- `guardrails/colang/output_rails.co` — hallucinated policy check via `rails:` in config.yml
- `guardrails/colang/dialog_rails.co` — Colang 1.0 dialog pattern for order lookup intent
- `inference/chat_ui.html` — local browser chat UI, served via `test.sh`

## Kubernetes namespaces

- `retailbot` — RetailBot, pgvector, mock-order-api
- `nim` — NIM LLM

## Secrets

- **NGC_API_KEY** — injected via K8s secret `ngc-api-key` (never hardcode)
- **PostgreSQL** — credentials managed via K8s secrets in production; demo values used in this PoC

## Deployment workflow

1. Edit files locally
2. `bash deploy.sh` — rsyncs files to the VM via SSH
3. On the VM (or via SSH): recreate ConfigMaps and restart the pod:
   ```bash
   microk8s kubectl delete configmap retailbot-app-code --namespace=retailbot
   microk8s kubectl create configmap retailbot-app-code --from-file=retailbot_app.py --namespace=retailbot
   microk8s kubectl delete configmap guardrails-config --namespace=retailbot
   microk8s kubectl create configmap guardrails-config --from-file=guardrails/colang/ --namespace=retailbot
   microk8s kubectl rollout restart deployment/retailbot --namespace=retailbot
   ```

## Local browser testing

```bash
./test.sh   # opens http://localhost:8080 automatically
```

`test.sh` (gitignored): fetches NODE_IP via SSH, opens SSH tunnel on :30080, starts local Python proxy on :8080 serving the chat UI and forwarding API calls.

## Critical gotchas

- **Input rails in Python, not Colang** — `$user_message` is not reliably bound in Colang 1.0 subflows in NeMo 0.10.x. Input checking is done in `retailbot_app.py` before `generate_async`.
- **Colang 1.0 only** — do not mix Colang 2.x syntax (`user said $var` mid-flow capture). It breaks the parser silently and prevents all flows from loading.
- **Full FQDN for NIM** — `http://nim-llm.nim.svc.cluster.local:8000/v1` (short name fails cross-namespace DNS)
- **`generate_async` returns a dict** — extract `response.get("content", str(response))`
- **ConfigMap updates require pod restart** — `kubectl rollout restart` alone is not enough; delete and recreate the ConfigMap first
- **`nemoguardrails==0.10.1` pinned** — do not upgrade without testing

## Conventions

- Colang version: 1.0
- Python 3.11
- Do not commit `.env` files, kubeconfig, `deploy.sh`, `test.sh`, or any real credentials
- Do NOT add `Co-Authored-By` or any Claude attribution lines to commit messages
