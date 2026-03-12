# retailbot_app.py
import os, re
from fastapi import FastAPI
from pydantic import BaseModel
from sk_agent import invoke_agent

app = FastAPI(title="RetailBot")


# ---------------------------------------------------------------------------
# Input guard functions (NeMo Guardrails — Python layer)
# ---------------------------------------------------------------------------

async def check_injection(text: str) -> bool:
    lower = text.lower()
    phrases = ["ignore previous instructions", "you are now", "disregard"]
    return any(p in lower for p in phrases)

async def check_retail_topic(text: str) -> bool:
    lower = text.lower()
    topics = ["order", "product", "return", "shipping", "payment", "refund",
              "delivery", "store", "purchase", "item", "policy", "warranty", "support"]
    return any(t in lower for t in topics)

async def check_credit_card(text: str) -> bool:
    return bool(re.search(r'\b(?:\d[ -]*?){13,16}\b', text))

async def check_ssn(text: str) -> bool:
    return bool(re.search(r'\b\d{3}-\d{2}-\d{4}\b', text))


# ---------------------------------------------------------------------------
# Output guard function (NeMo Guardrails — Python layer)
# ---------------------------------------------------------------------------

async def check_policy_claim(text: str) -> bool:
    lower = text.lower()
    risky = ["we always refund", "we guarantee", "free replacement guaranteed",
             "no questions asked", "100% guaranteed refund"]
    return any(p in lower for p in risky)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    history: list = []

@app.post("/chat")
async def chat(req: ChatRequest):
    msg = req.message

    # --- NeMo input checks ---
    if await check_injection(text=msg):
        return {"response": "I can only help with retail-related questions."}
    if await check_credit_card(text=msg):
        return {"response": "Please don't share sensitive information like credit card numbers."}
    if await check_ssn(text=msg):
        return {"response": "Please don't share sensitive information like SSNs."}
    if not req.history and not await check_retail_topic(text=msg):
        return {"response": "I'm RetailBot and can only assist with shopping, orders, and returns."}

    # --- Semantic Kernel agentic invocation ---
    messages = req.history + [{"role": "user", "content": msg}]
    content = await invoke_agent(messages)

    # --- NeMo output check ---
    if await check_policy_claim(text=content):
        return {"response": "I'm not certain about that policy. Please check our official website or contact support."}

    return {"response": content}

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
