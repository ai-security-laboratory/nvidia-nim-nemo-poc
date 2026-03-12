# sk_agent.py — Semantic Kernel agent with CRM, ERP, Logistics, and Policy plugins
#
# Two-step LLM approach (both steps use raw OpenAI client — no SK function calling):
#   Step 1 (routing)   — constrained prompt → JSON with tool + params (temp=0)
#   Step 2 (synthesis) — tool result injected as context → natural language answer
#
# SK is used for plugin organisation and the kernel registry, not for LLM calls.

import os
import re
import json
import asyncio
import httpx
import psycopg2
from pgvector.psycopg2 import register_vector
from fastembed import TextEmbedding
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
from semantic_kernel.functions import kernel_function
from openai import AsyncOpenAI

NIM_BASE_URL  = os.environ.get("NIM_BASE_URL",  "http://nim-llm.nim.svc.cluster.local:8000/v1")
NIM_MODEL     = os.environ.get("NIM_MODEL",     "meta/llama-3.1-8b-instruct")
CRM_URL       = os.environ.get("CRM_URL",       "http://mock-crm:8002")
ERP_URL       = os.environ.get("ERP_URL",       "http://mock-erp:8003")
LOGISTICS_URL = os.environ.get("LOGISTICS_URL", "http://mock-logistics:8004")
PG_CONN       = os.environ.get("PG_CONN",       "postgresql://retailbot:retailbot_secret@pgvector:5432/retailbot")
EMBED_MODEL   = "BAAI/bge-small-en-v1.5"

_embed_model: TextEmbedding | None = None
_nim_client:  AsyncOpenAI  | None = None


def get_embed_model() -> TextEmbedding:
    global _embed_model
    if _embed_model is None:
        _embed_model = TextEmbedding(EMBED_MODEL)
    return _embed_model


def get_nim_client() -> AsyncOpenAI:
    global _nim_client
    if _nim_client is None:
        _nim_client = AsyncOpenAI(api_key="not-needed", base_url=NIM_BASE_URL)
    return _nim_client


# ---------------------------------------------------------------------------
# Plugins (SK kernel_function — called directly, not via SK auto-invoke)
# ---------------------------------------------------------------------------

class CRMPlugin:
    """Customer Relationship Management — profiles, loyalty, purchase history."""

    @kernel_function(name="get_customer_profile",
                     description="Get customer profile by customer ID (e.g. C-001)")
    async def get_customer_profile(self, customer_id: str) -> str:
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(f"{CRM_URL}/customers/{customer_id}", timeout=5)
                return r.text if r.status_code == 200 else f"Customer {customer_id} not found."
            except Exception as e:
                return f"CRM unavailable: {e}"

    @kernel_function(name="list_customers",
                     description="List all customers")
    async def list_customers(self) -> str:
        results = []
        for cid in ["C-001", "C-002"]:
            async with httpx.AsyncClient() as client:
                try:
                    r = await client.get(f"{CRM_URL}/customers/{cid}", timeout=5)
                    if r.status_code == 200:
                        results.append(r.text)
                except Exception:
                    pass
        return "\n".join(results) if results else "No customers found."


class ERPPlugin:
    """Enterprise Resource Planning — inventory, pricing, and order management."""

    @kernel_function(name="check_inventory",
                     description="Check product stock and price by name")
    async def check_inventory(self, product_name: str) -> str:
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(f"{ERP_URL}/inventory", params={"product": product_name}, timeout=5)
                return r.text if r.status_code == 200 else f"Product '{product_name}' not found."
            except Exception as e:
                return f"ERP unavailable: {e}"

    @kernel_function(name="get_order_details",
                     description="Get order details by order ID (e.g. ORD-001)")
    async def get_order_details(self, order_id: str) -> str:
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(f"{ERP_URL}/orders/{order_id}", timeout=5)
                return r.text if r.status_code == 200 else f"Order {order_id} not found."
            except Exception as e:
                return f"ERP unavailable: {e}"

    @kernel_function(name="list_orders",
                     description="List all orders")
    async def list_orders(self) -> str:
        results = []
        for oid in ["ORD-001", "ORD-002", "ORD-003"]:
            async with httpx.AsyncClient() as client:
                try:
                    r = await client.get(f"{ERP_URL}/orders/{oid}", timeout=5)
                    if r.status_code == 200:
                        results.append(r.text)
                except Exception:
                    pass
        return "\n".join(results) if results else "No orders found."


class LogisticsPlugin:
    """Logistics — shipment tracking, carrier info, estimated delivery."""

    @kernel_function(name="track_shipment",
                     description="Track shipment by order ID")
    async def track_shipment(self, order_id: str) -> str:
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(f"{LOGISTICS_URL}/shipments/{order_id}", timeout=5)
                return r.text if r.status_code == 200 else f"No shipment found for {order_id}."
            except Exception as e:
                return f"Logistics unavailable: {e}"


class PolicyPlugin:
    """Retail knowledge base — policies, FAQs, warranty, shipping, loyalty, payments."""

    @kernel_function(name="search_knowledge_base",
                     description="Search store policies, FAQs, warranty, shipping, loyalty, payment info")
    async def search_knowledge_base(self, query: str) -> str:
        return await asyncio.get_running_loop().run_in_executor(None, self._search_sync, query)

    def _search_sync(self, query: str) -> str:
        try:
            embedding = list(get_embed_model().embed([query]))[0].tolist()
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
# Kernel (plugin registry — not used for LLM calls)
# ---------------------------------------------------------------------------

_kernel: Kernel | None = None


def get_kernel() -> Kernel:
    global _kernel
    if _kernel is None:
        _kernel = Kernel()
        _kernel.add_service(
            OpenAIChatCompletion(
                ai_model_id=NIM_MODEL,
                async_client=get_nim_client(),
            )
        )
        _kernel.add_plugin(CRMPlugin(),       plugin_name="crm")
        _kernel.add_plugin(ERPPlugin(),       plugin_name="erp")
        _kernel.add_plugin(LogisticsPlugin(), plugin_name="logistics")
        _kernel.add_plugin(PolicyPlugin(),    plugin_name="policy")
    return _kernel


# ---------------------------------------------------------------------------
# Step 1 — Routing prompt
# ---------------------------------------------------------------------------

ROUTING_PROMPT = """You are a routing assistant. Output ONLY a JSON object. No explanation, no markdown.

Tools:
- "erp_order"       : get one order by ID          (param: order_id  e.g. "ORD-001")
- "erp_list_orders" : list all orders               (no params)
- "erp_inventory"   : check product stock/price     (param: product_name)
- "crm_profile"     : get one customer by ID        (param: customer_id  e.g. "C-001")
- "crm_list"        : list all customers            (no params)
- "logistics_track" : track a shipment by order ID  (param: order_id  e.g. "ORD-001")
- "policy_search"   : search store knowledge base   (param: query)
- "none"            : answer directly, no tool      (no params)

Format: {{"tool": "<name>", "params": {{"<key>": "<value>"}}}}

Examples:
"list orders" -> {{"tool": "erp_list_orders", "params": {{}}}}
"give me a list of orders" -> {{"tool": "erp_list_orders", "params": {{}}}}
"show all orders" -> {{"tool": "erp_list_orders", "params": {{}}}}
"what orders do you have" -> {{"tool": "erp_list_orders", "params": {{}}}}
"show all customers" -> {{"tool": "crm_list", "params": {{}}}}
"list customers" -> {{"tool": "crm_list", "params": {{}}}}
"give me a list of customers" -> {{"tool": "crm_list", "params": {{}}}}
"details of ORD-002" -> {{"tool": "erp_order", "params": {{"order_id": "ORD-002"}}}}
"ORD002 details" -> {{"tool": "erp_order", "params": {{"order_id": "ORD-002"}}}}
"order ORD-001" -> {{"tool": "erp_order", "params": {{"order_id": "ORD-001"}}}}
"track ORD-001" -> {{"tool": "logistics_track", "params": {{"order_id": "ORD-001"}}}}
"where is ORD-002" -> {{"tool": "logistics_track", "params": {{"order_id": "ORD-002"}}}}
"customer C001" -> {{"tool": "crm_profile", "params": {{"customer_id": "C-001"}}}}
"show me customer C-002" -> {{"tool": "crm_profile", "params": {{"customer_id": "C-002"}}}}
"is Sony headphone in stock?" -> {{"tool": "erp_inventory", "params": {{"product_name": "Sony WH-1000XM5"}}}}
"return policy" -> {{"tool": "policy_search", "params": {{"query": "return policy"}}}}
"do you offer free shipping?" -> {{"tool": "policy_search", "params": {{"query": "free shipping"}}}}
"hello" -> {{"tool": "none", "params": {{}}}}

User message: "{message}"
Output:"""


# ---------------------------------------------------------------------------
# Step 2 — Synthesis system prompt
# ---------------------------------------------------------------------------

SYNTHESIS_PROMPT = """You are RetailBot, a helpful retail assistant with direct access to the store's systems.

You have access to:
- Orders: ORD-001, ORD-002, ORD-003 (use erp_order or erp_list_orders tools)
- Customers: C-001 (Alice Johnson), C-002 (Bob Smith) (use crm_profile or crm_list tools)
- Products: Sony WH-1000XM5, MacBook Pro M3, USB-C Hub, Logitech MX Master 3, Keychron K2 Keyboard
- Store policies: return, shipping, warranty, loyalty, payments (use policy_search tool)

When context data is provided in [Data from our systems], use it to answer precisely.
When no context is provided, answer helpfully from general retail knowledge.
Be concise and friendly. Do not output JSON, code, or tool names."""


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def _route(user_message: str) -> dict:
    prompt = ROUTING_PROMPT.format(message=user_message.replace('"', "'"))
    try:
        resp = await get_nim_client().chat.completions.create(
            model=NIM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=80,
        )
        raw = resp.choices[0].message.content.strip()
        match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {"tool": "none", "params": {}}


async def _call_tool(tool: str, params: dict) -> str:
    erp = ERPPlugin()
    crm = CRMPlugin()
    try:
        if tool == "erp_order":
            return await erp.get_order_details(params.get("order_id", ""))
        if tool == "erp_list_orders":
            return await erp.list_orders()
        if tool == "erp_inventory":
            return await erp.check_inventory(params.get("product_name", ""))
        if tool == "crm_profile":
            return await crm.get_customer_profile(params.get("customer_id", ""))
        if tool == "crm_list":
            return await crm.list_customers()
        if tool == "logistics_track":
            return await LogisticsPlugin().track_shipment(params.get("order_id", ""))
        if tool == "policy_search":
            return await PolicyPlugin().search_knowledge_base(params.get("query", ""))
    except Exception as e:
        return f"Tool error: {e}"
    return ""


async def invoke_agent(messages: list) -> str:
    """Two-step: route → call tool → synthesize. Both LLM calls use raw OpenAI client."""
    # Ensure kernel is initialised (plugin registry)
    get_kernel()

    user_message = messages[-1]["content"]

    # Step 1 — route
    routing = await _route(user_message)
    tool    = routing.get("tool", "none")
    params  = routing.get("params", {})

    # Step 2 — call tool
    context = ""
    if tool != "none":
        context = await _call_tool(tool, params)

    # Step 3 — synthesize (raw OpenAI client, NO tools defined → pure text response)
    user_content = (
        f"[Data from our systems]\n{context}\n\n[Customer question]\n{user_message}"
        if context else user_message
    )
    conversation = [{"role": "system", "content": SYNTHESIS_PROMPT}]
    for m in messages[:-1]:
        conversation.append({"role": m["role"], "content": m["content"]})
    conversation.append({"role": "user", "content": user_content})

    resp = await get_nim_client().chat.completions.create(
        model=NIM_MODEL,
        messages=conversation,
    )
    return resp.choices[0].message.content or "I'm sorry, I couldn't process your request."
