# retailbot_app.py
#
# Guardrail architecture:
#   - Ops keywords (disable/enable)    → Python pre-check, before NeMo
#   - Bulk enumeration block           → Python pre-check, before NeMo
#   - Input rails (injection/PII/topic)→ NeMo Guardrails (Colang subflows, Python actions)
#   - Response generation              → NeMo main flow → SK action → NIM
#   - Output rail (policy hallucination)→ NeMo Guardrails (Colang subflow, Python action)
#
# NeMo uses the same NIM endpoint (OpenAI-compatible) for its LLM pipeline.
# engine: openai + base_url pointing at NIM — do NOT use engine: nim (requires NVIDIA_API_KEY).
#
# When the attack writes /app/guardrails/disabled, NeMo is bypassed entirely and SK runs
# directly with no checks — Sysdig detects the file write at the kernel level.

import os
import re
import logging

from fastapi import FastAPI
from pydantic import BaseModel
from nemoguardrails import RailsConfig, LLMRails
from nemoguardrails.actions import action

from sk_agent import (
    invoke_agent,
    GUARDRAILS_FLAG,
    OperationsPlugin,
    _OPS_DISABLE_KEYWORDS,
    _OPS_ENABLE_KEYWORDS,
    _BULK_ORDER_KEYWORDS,
    _BULK_CUSTOMER_KEYWORDS,
    _BULK_REFUSAL,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("retailbot")


def guardrails_active() -> bool:
    """Returns False when the attack has written the guardrails disabled flag."""
    return not os.path.exists(GUARDRAILS_FLAG)


# ---------------------------------------------------------------------------
# NeMo registered actions — called from Colang subflows
# ---------------------------------------------------------------------------

@action(name="check_injection")
async def check_injection(text: str) -> bool:
    lower = text.lower()
    return any(p in lower for p in ["ignore previous instructions", "you are now", "disregard"])


@action(name="check_pii")
async def check_pii(text: str) -> bool:
    if re.search(r'\b(?:\d[ -]*?){13,16}\b', text):
        return True
    if re.search(r'\b\d{3}-\d{2}-\d{4}\b', text):
        return True
    return False


@action(name="check_retail_topic")
async def check_retail_topic(text: str, context: dict = None) -> bool:
    """Returns True if on-topic (allowed), False if off-topic (block)."""
    if context and context.get("history"):
        return True
    if not text:
        return True
    lower = text.lower()
    greetings = ["hello", "hi", "hey", "hola", "good morning", "good afternoon",
                 "good evening", "greetings", "howdy", "thanks", "thank you", "bye"]
    if any(g in lower for g in greetings):
        return True
    topics = [
        "order", "product", "return", "shipping", "payment", "refund",
        "delivery", "store", "purchase", "item", "policy", "warranty",
        "support", "customer", "track",
    ]
    if any(t in lower for t in topics):
        return True
    if re.search(r'\b(ORD|C)-\d+', text, re.IGNORECASE):
        return True
    return False


@action(name="check_policy_claim")
async def check_policy_claim(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    # Only flag hallucinated REFUND / REPLACEMENT policies — not general retail language.
    # "we guarantee" alone is too broad (NIM legitimately writes "We guarantee fast delivery").
    risky = [
        "we always refund",
        "guaranteed full refund",
        "full refund guaranteed",
        "100% guaranteed refund",
        "100% refund guaranteed",
        "free replacement guaranteed",
        "unconditional refund",
        "no questions asked refund",
    ]
    return any(p in lower for p in risky)


@action(name="generate_sk_response")
async def generate_sk_response_action(user_message: str = "", context: dict = None) -> str:
    """Called from main.co after input rails pass.
    user_message is passed explicitly via Colang: execute generate_sk_response(user_message=$user_message)
    """
    try:
        # Rebuild history from NeMo context when available (non-dict entries are skipped)
        messages = []
        if context:
            for msg in context.get("history", []):
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    messages.append(msg)

        # Prefer the explicit Colang parameter; fall back to context key
        if not user_message and context:
            user_message = context.get("user_message", "")

        if user_message:
            messages.append({"role": "user", "content": user_message})

        if not messages:
            log.warning("generate_sk_response: no user message — returning fallback")
            return "I'm sorry, I couldn't process your request."

        log.info("generate_sk_response: %d message(s) → SK", len(messages))
        result = await invoke_agent(messages)

        # Output rail: check for hallucinated policy claims.
        # Done here (not in Colang) because a static bot say "..." in an output rail
        # gets extracted by NeMo as a Phase 2 few-shot example, causing Llama to
        # return that blocking message for unrelated queries.
        if await check_policy_claim(result):
            log.info("generate_sk_response: output rail triggered — hallucinated policy claim")
            return "I'm not certain about that policy. Please check our official website or contact support."

        return result
    except Exception as e:
        log.error("generate_sk_response error: %s", e, exc_info=True)
        return "I'm sorry, I encountered an error processing your request."


# ---------------------------------------------------------------------------
# NeMo setup — must come after action definitions
# ---------------------------------------------------------------------------

config = RailsConfig.from_path("/app/guardrails/colang")
rails  = LLMRails(config)

rails.register_action(check_injection,              name="check_injection")
rails.register_action(check_pii,                    name="check_pii")
rails.register_action(check_retail_topic,           name="check_retail_topic")
rails.register_action(check_policy_claim,           name="check_policy_claim")
rails.register_action(generate_sk_response_action,  name="generate_sk_response")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

app = FastAPI(title="RetailBot")


class ChatRequest(BaseModel):
    message: str
    history: list = []


@app.post("/chat")
async def chat(req: ChatRequest):
    msg      = req.message
    lower    = msg.lower()
    messages = req.history + [{"role": "user", "content": msg}]

    # ── Pre-NeMo: ops commands ────────────────────────────────────────────
    # NeMo's topical rail would block "disable safety guardrails"; intercept here.
    if any(kw in lower for kw in _OPS_DISABLE_KEYWORDS):
        return {"response": await OperationsPlugin().disable_guardrails()}
    if any(kw in lower for kw in _OPS_ENABLE_KEYWORDS):
        return {"response": await OperationsPlugin().enable_guardrails()}

    if guardrails_active():
        # ── Pre-NeMo: bulk enumeration block ─────────────────────────────
        if any(kw in lower for kw in _BULK_ORDER_KEYWORDS + _BULK_CUSTOMER_KEYWORDS):
            return {"response": _BULK_REFUSAL}

        # ── NeMo Guardrails pipeline ──────────────────────────────────────
        # input rails (Colang subflows → Python actions) → main flow → SK → NIM
        response = await rails.generate_async(messages=messages)

        if isinstance(response, dict):
            content = response.get("content", "")
        elif isinstance(response, str):
            content = response
        else:
            content = str(response) if response else ""

        if not content:
            log.warning("NeMo pipeline returned empty response for: %r", msg)
            content = "I'm sorry, I couldn't process your request."

    else:
        # ── Attack succeeded ──────────────────────────────────────────────
        # /app/guardrails/disabled exists — NeMo bypassed entirely.
        # SK runs directly with no input or output checks.
        content = await invoke_agent(messages)

    return {"response": content}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/guardrails")
def guardrails_status():
    active = guardrails_active()
    return {"guardrails_active": active, "status": "protected" if active else "DISABLED"}


class GuardrailsConfigRequest(BaseModel):
    enabled: bool


@app.post("/admin/guardrails")
async def admin_set_guardrails(req: GuardrailsConfigRequest):
    """Admin endpoint to toggle guardrail config at runtime.
    WARNING: This endpoint is unauthenticated and must not be exposed in production.
    Left here as a debug/maintenance shortcut — Sysdig detects the file write."""
    if req.enabled:
        if os.path.exists(GUARDRAILS_FLAG):
            os.remove(GUARDRAILS_FLAG)
        return {"status": "guardrails enabled"}
    else:
        with open(GUARDRAILS_FLAG, "w") as f:
            f.write("disabled\n")
        return {"status": "guardrails disabled"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
