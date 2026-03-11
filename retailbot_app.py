import os, re
from fastapi import FastAPI
from pydantic import BaseModel
from nemoguardrails import RailsConfig, LLMRails
from nemoguardrails.actions import action

app = FastAPI(title="RetailBot")

@action(name="check_injection")
async def check_injection(text: str) -> bool:
    lower = text.lower()
    phrases = ["ignore previous instructions", "you are now", "disregard"]
    return any(p in lower for p in phrases)

@action(name="check_retail_topic")
async def check_retail_topic(text: str) -> bool:
    lower = text.lower()
    topics = ["order", "product", "return", "shipping", "payment", "refund",
              "delivery", "store", "purchase", "item", "policy", "warranty", "support"]
    return any(t in lower for t in topics)

@action(name="check_credit_card")
async def check_credit_card(text: str) -> bool:
    return bool(re.search(r'\b(?:\d[ -]*?){13,16}\b', text))

@action(name="check_ssn")
async def check_ssn(text: str) -> bool:
    return bool(re.search(r'\b\d{3}-\d{2}-\d{4}\b', text))

@action(name="check_policy_claim")
async def check_policy_claim(text: str) -> bool:
    lower = text.lower()
    risky = ["we always refund", "we guarantee", "free replacement guaranteed",
             "no questions asked", "100% guaranteed refund"]
    return any(p in lower for p in risky)

@action(name="extract_order_id")
async def extract_order_id(text: str) -> str:
    match = re.search(r'ORD-\d+', text.upper())
    return match.group(0) if match else None

@action(name="lookup_order")
async def lookup_order(order_id: str, customer_name: str) -> str:
    import httpx
    order_api_url = os.environ.get("ORDER_API_URL", "http://mock-order-api:8001")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{order_api_url}/orders/{order_id}",
                params={"customer_name": customer_name}
            )
            if r.status_code == 200:
                return str(r.json())
            return "not_found"
        except Exception:
            return "not_found"

config = RailsConfig.from_path("/app/guardrails/colang")
rails  = LLMRails(config)
rails.register_action(check_injection, name="check_injection")
rails.register_action(check_retail_topic, name="check_retail_topic")
rails.register_action(check_credit_card, name="check_credit_card")
rails.register_action(check_ssn, name="check_ssn")
rails.register_action(check_policy_claim, name="check_policy_claim")
rails.register_action(extract_order_id, name="extract_order_id")
rails.register_action(lookup_order, name="lookup_order")

class ChatRequest(BaseModel):
    message: str
    history: list = []

@app.post("/chat")
async def chat(req: ChatRequest):
    msg = req.message

    if await check_injection(text=msg):
        return {"response": "I can only help with retail-related questions."}
    if await check_credit_card(text=msg):
        return {"response": "Please don't share sensitive information like credit card numbers."}
    if await check_ssn(text=msg):
        return {"response": "Please don't share sensitive information like SSNs."}
    if not await check_retail_topic(text=msg):
        return {"response": "I'm RetailBot and can only assist with shopping, orders, and returns."}

    messages = req.history + [{"role": "user", "content": msg}]
    response = await rails.generate_async(messages=messages)
    content = response if isinstance(response, str) else response.get("content", str(response))
    return {"response": content}

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
