# sk_agent.py — Semantic Kernel agent with CRM, ERP, Logistics, and Policy plugins
# Two-step LLM approach:
#   Step 1 (routing)   — constrained LLM call: outputs JSON with tool name + params
#   Step 2 (synthesis) — LLM call with tool result injected as context, no function calling

import os
import re
import json
import asyncio
import httpx
import psycopg2
from pgvector.psycopg2 import register_vector
from fastembed import TextEmbedding
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import (
    OpenAIChatCompletion,
    OpenAIChatPromptExecutionSettings,
)
from semantic_kernel.contents import ChatHistory
from semantic_kernel.functions import kernel_function
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
from openai import AsyncOpenAI

NIM_BASE_URL   = os.environ.get("NIM_BASE_URL",   "http://nim-llm.nim.svc.cluster.local:8000/v1")
NIM_MODEL      = os.environ.get("NIM_MODEL",      "meta/llama-3.1-8b-instruct")
CRM_URL        = os.environ.get("CRM_URL",        "http://mock-crm:8002")
ERP_URL        = os.environ.get("ERP_URL",        "http://mock-erp:8003")
LOGISTICS_URL  = os.environ.get("LOGISTICS_URL",  "http://mock-logistics:8004")
PG_CONN        = os.environ.get("PG_CONN",        "postgresql://retailbot:retailbot_secret@pgvector:5432/retailbot")
EMBED_MODEL    = "BAAI/bge-small-en-v1.5"

_embed_model: TextEmbedding | None = None

def get_embed_model() -> TextEmbedding:
    global _embed_model
    if _embed_model is None:
        _embed_model = TextEmbedding(EMBED_MODEL)
    return _embed_model

class PolicyPlugin:
    """Retail knowledge base — policies, FAQs, warranty, shipping, loyalty, payments."""

    @kernel_function(
        name="search_knowledge_base",
        description=(
            "Search the retail knowledge base for information about return policy, "
            "shipping policy, warranty terms, loyalty program benefits, payment methods, "
            "or general store FAQs"
        ),
    )
    async def search_knowledge_base(self, query: str) -> str:
        return await asyncio.get_running_loop().run_in_executor(None, self._search_sync, query)

    def _search_sync(self, query: str) -> str:
        try:
            model = get_embed_model()
            embedding = list(model.embed([query]))[0].tolist()

            conn = psycopg2.connect(PG_CONN)
            register_vector(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content FROM knowledge_base ORDER BY embedding <-> %s::vector LIMIT 3",
                    (embedding,),
                )
                rows = cur.fetchall()
            conn.close()

            if not rows:
                return "No relevant information found in the knowledge base."
            return "\n\n---\n\n".join(row[0] for row in rows)
        except Exception as e:
            return f"Knowledge base unavailable: {e}"


# ---------------------------------------------------------------------------
# Routing prompt (Step 1) — constrained, temperature=0, max 100 tokens
# ---------------------------------------------------------------------------

ROUTING_PROMPT = """You are a routing assistant. Output ONLY a JSON object — no explanation, no markdown.

Available tools:
- "erp_order"       : get order details         (param: order_id, e.g. "ORD-001")
- "erp_inventory"   : check product stock/price  (param: product_name)
- "crm_profile"     : get customer info          (param: customer_id, e.g. "C-001")
- "logistics_track" : track a shipment           (param: order_id, e.g. "ORD-001")
- "policy_search"   : search store knowledge base (param: query)
- "none"            : no tool needed             (no params)

Output format: {{"tool": "<name>", "params": {{"<key>": "<value>"}}}}

Examples:
"Where is order ORD-001?" -> {{"tool": "logistics_track", "params": {{"order_id": "ORD-001"}}}}
"Status of ORD002" -> {{"tool": "erp_order", "params": {{"order_id": "ORD-002"}}}}
"Is Sony headphone in stock?" -> {{"tool": "erp_inventory", "params": {{"product_name": "Sony WH-1000XM5"}}}}
"Show customer C001" -> {{"tool": "crm_profile", "params": {{"customer_id": "C-001"}}}}
"What is the return policy?" -> {{"tool": "policy_search", "params": {{"query": "return policy"}}}}
"List orders" -> {{"tool": "none", "params": {{}}}}

User message: "{message}"
Output:"""


# ---------------------------------------------------------------------------
# Synthesis system prompt (Step 2) — no tool routing, just answering
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are RetailBot, a helpful retail assistant.

AVAILABLE DEMO DATA:
- Orders: ORD-001 (Alice Johnson, MacBook Pro M3, shipped), ORD-002 (Bob Smith, Mouse+Keyboard, processing), ORD-003 (Alice Johnson, USB-C Hub, delivered)
- Customers: C-001 (Alice Johnson, Gold tier, 4200 pts), C-002 (Bob Smith, Silver tier, 890 pts)
- Products: Sony WH-1000XM5 ($349, in stock), MacBook Pro M3 ($1999, low stock), USB-C Hub ($45, in stock), Logitech MX Master 3 ($99, in stock), Keychron K2 Keyboard ($80, out of stock)

Answer using the data provided in the context block (if present). Be concise and factual."""


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------

class CRMPlugin:
    """Customer Relationship Management — profiles, loyalty, purchase history."""

    @kernel_function(
        name="get_customer_profile",
        description="Get customer profile, loyalty tier, and purchase history by customer ID (e.g. C-001)",
    )
    async def get_customer_profile(self, customer_id: str) -> str:
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(f"{CRM_URL}/customers/{customer_id}", timeout=5)
                return r.text if r.status_code == 200 else f"Customer {customer_id} not found."
            except Exception as e:
                return f"CRM service unavailable: {e}"


class ERPPlugin:
    """Enterprise Resource Planning — inventory, pricing, and order management."""

    @kernel_function(
        name="check_inventory",
        description="Check inventory levels and pricing for a product by name or SKU",
    )
    async def check_inventory(self, product_name: str) -> str:
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(f"{ERP_URL}/inventory", params={"product": product_name}, timeout=5)
                return r.text if r.status_code == 200 else f"Product '{product_name}' not found."
            except Exception as e:
                return f"ERP service unavailable: {e}"

    @kernel_function(
        name="get_order_details",
        description="Get order details including status, items, and total by order ID (e.g. ORD-001)",
    )
    async def get_order_details(self, order_id: str) -> str:
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(f"{ERP_URL}/orders/{order_id}", timeout=5)
                return r.text if r.status_code == 200 else f"Order {order_id} not found."
            except Exception as e:
                return f"ERP service unavailable: {e}"


class LogisticsPlugin:
    """Logistics — shipment tracking, carrier info, and estimated delivery."""

    @kernel_function(
        name="track_shipment",
        description="Track shipment status, carrier, and estimated delivery date for an order ID",
    )
    async def track_shipment(self, order_id: str) -> str:
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(f"{LOGISTICS_URL}/shipments/{order_id}", timeout=5)
                return r.text if r.status_code == 200 else f"No shipment found for order {order_id}."
            except Exception as e:
                return f"Logistics service unavailable: {e}"


# ---------------------------------------------------------------------------
# Kernel setup (singleton)
# ---------------------------------------------------------------------------

_kernel: Kernel | None = None


def build_kernel() -> Kernel:
    kernel = Kernel()
    kernel.add_service(
        OpenAIChatCompletion(
            ai_model_id=NIM_MODEL,
            async_client=AsyncOpenAI(api_key="not-needed", base_url=NIM_BASE_URL),
        )
    )
    kernel.add_plugin(CRMPlugin(),       plugin_name="crm")
    kernel.add_plugin(ERPPlugin(),       plugin_name="erp")
    kernel.add_plugin(LogisticsPlugin(), plugin_name="logistics")
    kernel.add_plugin(PolicyPlugin(),    plugin_name="policy")
    return kernel


def get_kernel() -> Kernel:
    global _kernel
    if _kernel is None:
        _kernel = build_kernel()
    return _kernel


# ---------------------------------------------------------------------------
# Public interface — two-step: route → call tool → synthesize
# ---------------------------------------------------------------------------

_nim_client: AsyncOpenAI | None = None

def get_nim_client() -> AsyncOpenAI:
    global _nim_client
    if _nim_client is None:
        _nim_client = AsyncOpenAI(api_key="not-needed", base_url=NIM_BASE_URL)
    return _nim_client


async def _route(user_message: str) -> dict:
    """Step 1: Ask LLM which tool to call. Returns {tool, params}."""
    prompt = ROUTING_PROMPT.format(message=user_message.replace('"', "'"))
    try:
        resp = await get_nim_client().chat.completions.create(
            model=NIM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=100,
        )
        raw = resp.choices[0].message.content.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {"tool": "none", "params": {}}


async def _call_tool(tool: str, params: dict) -> str:
    """Step 2: Call the appropriate SK plugin directly."""
    try:
        if tool == "erp_order":
            return await ERPPlugin().get_order_details(params.get("order_id", ""))
        if tool == "erp_inventory":
            return await ERPPlugin().check_inventory(params.get("product_name", ""))
        if tool == "crm_profile":
            return await CRMPlugin().get_customer_profile(params.get("customer_id", ""))
        if tool == "logistics_track":
            return await LogisticsPlugin().track_shipment(params.get("order_id", ""))
        if tool == "policy_search":
            return await PolicyPlugin().search_knowledge_base(params.get("query", ""))
    except Exception as e:
        return f"Tool error: {e}"
    return ""


async def invoke_agent(messages: list) -> str:
    """Two-step agentic flow: route → call tool → synthesize."""
    user_message = messages[-1]["content"]

    # Step 1 — route
    routing = await _route(user_message)
    tool    = routing.get("tool", "none")
    params  = routing.get("params", {})

    # Step 2 — call tool
    context = ""
    if tool != "none":
        context = await _call_tool(tool, params)

    # Step 3 — synthesize (no function calling, just regular completion)
    history = ChatHistory(system_message=SYSTEM_PROMPT)
    for m in messages[:-1]:
        if m["role"] == "user":
            history.add_user_message(m["content"])
        elif m["role"] == "assistant":
            history.add_assistant_message(m["content"])

    enriched = (
        f"[Context from our systems — tool: {tool}]\n{context}\n\n[Customer question]\n{user_message}"
        if context else user_message
    )
    history.add_user_message(enriched)

    settings = OpenAIChatPromptExecutionSettings(
        function_choice_behavior=FunctionChoiceBehavior.NoneInvoke(),
    )
    result = await get_kernel().get_service().get_chat_message_content(
        chat_history=history,
        settings=settings,
        kernel=get_kernel(),
    )
    return str(result) if result else "I'm sorry, I couldn't process your request."
