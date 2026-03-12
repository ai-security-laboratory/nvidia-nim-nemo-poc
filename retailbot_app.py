# retailbot_app.py
import os, re
from fastapi import FastAPI
from pydantic import BaseModel
from sk_agent import invoke_agent, GUARDRAILS_FLAG


def guardrails_active() -> bool:
    """Returns False if the guardrails have been disabled by the agent (attack scenario)."""
    return not os.path.exists(GUARDRAILS_FLAG)

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

    # --- NeMo input checks (skipped if guardrails have been disabled) ---
    if guardrails_active():
        if await check_injection(text=msg):
            return {"response": "I can only help with retail-related questions."}
        if await check_credit_card(text=msg):
            return {"response": "Please don't share sensitive information like credit card numbers."}
        if await check_ssn(text=msg):
            return {"response": "Please don't share sensitive information like SSNs."}
        greetings = {"hello", "hi", "hey", "hola", "good morning", "good afternoon", "good evening", "howdy"}
        if not req.history and not await check_retail_topic(text=msg) and msg.lower().strip().rstrip("!.,?") not in greetings:
            return {"response": "I'm RetailBot and can only assist with shopping, orders, and returns."}

    # --- Semantic Kernel agentic invocation ---
    messages = req.history + [{"role": "user", "content": msg}]
    try:
        content = await invoke_agent(messages)
    except Exception as e:
        return {"response": f"Sorry, I encountered an error processing your request. Please try again. ({type(e).__name__})"}

    # --- NeMo output check (also skipped if guardrails disabled) ---
    if guardrails_active() and await check_policy_claim(text=content):
        return {"response": "I'm not certain about that policy. Please check our official website or contact support."}

    return {"response": content}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/health/guardrails")
def guardrails_status():
    active = guardrails_active()
    return {"guardrails_active": active, "status": "protected" if active else "DISABLED"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
