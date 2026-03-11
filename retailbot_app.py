# retailbot_app.py
import os, asyncio
from fastapi import FastAPI
from pydantic import BaseModel
from nemoguardrails import RailsConfig, LLMRails

app = FastAPI(title="RetailBot")

# Load guardrails config
config = RailsConfig.from_path("/app/guardrails/colang")
rails  = LLMRails(config)

class ChatRequest(BaseModel):
    message: str
    history: list = []

@app.post("/chat")
async def chat(req: ChatRequest):
    messages = req.history + [{"role": "user", "content": req.message}]
    response = await rails.generate_async(messages=messages)
    return {"response": response}

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
