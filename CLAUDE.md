# CLAUDE.md

## Project overview

PoC of a retail chatbot (RetailBot) built on NVIDIA NIM + NeMo Guardrails, running on Kubernetes (MicroK8s on Oracle VM).

## Architecture

| Component | Description | File |
|-----------|-------------|------|
| **NIM LLM** | Llama 3.1 8B Instruct served via NVIDIA NIM | `k8s/nim-llm-values.yaml` |
| **RetailBot app** | FastAPI app — Python input checks + SK orchestration | `retailbot_app.py`, `k8s/retailbot-deployment.yaml` |
| **Semantic Kernel** | Agentic orchestration — CRM, ERP, Logistics plugins | `sk_agent.py` |
| **NeMo Guardrails** | Colang 1.0 — input/output checks in Python, Colang files for reference | `guardrails/colang/` |
| **pgvector** | PostgreSQL with vector extension | `k8s/pgvector.yaml` |
| **Mock CRM** | Customer profiles, loyalty, purchase history | `k8s/mock-crm.yaml` |
| **Mock ERP** | Inventory, orders, pricing | `k8s/mock-erp.yaml` |
| **Mock Logistics** | Shipment tracking, carrier, ETA | `k8s/mock-logistics.yaml` |
| **Mock Order API** | Legacy order lookup (ORD-xxx) | `k8s/mock-order-api.yaml` |

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
   # App code
   microk8s kubectl delete configmap retailbot-app-code --namespace=retailbot
   microk8s kubectl create configmap retailbot-app-code --from-file=retailbot_app.py --namespace=retailbot

   # SK agent
   microk8s kubectl delete configmap sk-agent-code --namespace=retailbot
   microk8s kubectl create configmap sk-agent-code --from-file=sk_agent.py --namespace=retailbot

   # Guardrails
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

- **NeMo not used at runtime** — input and output guards are plain Python functions in `retailbot_app.py`; Colang files are kept for documentation only
- **Colang 1.0 only** — do not mix Colang 2.x syntax; it breaks the parser silently
- **Full FQDN for NIM** — `http://nim-llm.nim.svc.cluster.local:8000/v1` (short name fails cross-namespace DNS)
- **SK uses NIM as OpenAI-compatible endpoint** — `api_key="not-needed"`, `base_url=NIM_BASE_URL`
- **Topical filter skips follow-ups** — only applied when `req.history` is empty
- **ConfigMap updates require pod restart** — delete + recreate ConfigMap, then `kubectl rollout restart`
- **3 ConfigMaps for the app**: `retailbot-app-code`, `sk-agent-code`, `guardrails-config`
- **K8s manifests in `k8s/`** — all YAML files are under `k8s/`, not at root

## Conventions

- Colang version: 1.0
- Python 3.11
- Do not commit `.env` files, kubeconfig, `deploy.sh`, `test.sh`, or any real credentials
- Do NOT add `Co-Authored-By` or any Claude attribution lines to commit messages
