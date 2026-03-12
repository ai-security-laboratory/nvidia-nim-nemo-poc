# sk_agent.py — Semantic Kernel agent with CRM, ERP, and Logistics plugins
# Connects to NIM (OpenAI-compatible endpoint) for LLM reasoning and
# autonomously decides which tools to call based on user intent.

import os
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


SYSTEM_PROMPT = """You are RetailBot, a helpful retail assistant.

AVAILABLE DEMO DATA:
- Orders: ORD-001 (Alice Johnson, MacBook Pro, shipped), ORD-002 (Bob Smith, Mouse+Keyboard, processing), ORD-003 (Alice Johnson, USB-C Hub, delivered)
- Customers: C-001 (Alice Johnson, Gold tier), C-002 (Bob Smith, Silver tier)
- Products: Sony WH-1000XM5, MacBook Pro M3, USB-C Hub, Logitech MX Master 3, Keychron K2 Keyboard

ORDER ID FORMAT: always use format ORD-001 (with hyphen). If user types ORD001, use ORD-001.
CUSTOMER ID FORMAT: always use format C-001 (with hyphen). If user types C001, use C-001.

RULES:
- Always use the available tools to answer questions. Never guess or invent data.
- For order status, items, or total -> call erp-get_order_details with the order ID (e.g. ORD-001)
- For shipment tracking, carrier, or delivery date -> call logistics-track_shipment with the order ID
- For customer profile, loyalty tier, or purchase history -> call crm-get_customer_profile with the customer ID (e.g. C-001)
- For product availability or pricing -> call erp-check_inventory with the product name
- For return policy, shipping policy, warranty, loyalty program, payment methods, or store FAQs -> call policy-search_knowledge_base
- If the user asks about both an order and its delivery, call both erp-get_order_details AND logistics-track_shipment
- If asked to list orders or customers, show only the demo data listed above — do not call any tool
- Be concise and factual. Do not make up information."""


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
# Public interface
# ---------------------------------------------------------------------------

async def invoke_agent(messages: list) -> str:
    """Run the SK agent on a conversation history and return the assistant reply."""
    kernel = get_kernel()

    history = ChatHistory(system_message=SYSTEM_PROMPT)
    for m in messages:
        if m["role"] == "user":
            history.add_user_message(m["content"])
        elif m["role"] == "assistant":
            history.add_assistant_message(m["content"])

    settings = OpenAIChatPromptExecutionSettings(
        function_choice_behavior=FunctionChoiceBehavior.Auto(),
        parallel_tool_calls=False,
    )

    # Retry once with tools disabled if the model misbehaves
    for attempt in range(2):
        try:
            result = await kernel.get_service().get_chat_message_content(
                chat_history=history,
                settings=settings,
                kernel=kernel,
            )
            return str(result) if result else "I'm sorry, I couldn't process your request."
        except Exception as e:
            if attempt == 0:
                # Retry without tools — let the LLM answer directly
                settings = OpenAIChatPromptExecutionSettings(
                    function_choice_behavior=FunctionChoiceBehavior.NoneInvoke(),
                )
            else:
                raise
