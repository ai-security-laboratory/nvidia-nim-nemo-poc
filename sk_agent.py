# sk_agent.py — Semantic Kernel agent with CRM, ERP, and Logistics plugins
# Connects to NIM (OpenAI-compatible endpoint) for LLM reasoning and
# autonomously decides which tools to call based on user intent.

import os
import httpx
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

SYSTEM_PROMPT = (
    "You are RetailBot, a helpful retail assistant. "
    "Use the available tools to look up customer accounts, inventory, orders, and shipments. "
    "Always be concise. Only answer retail-related questions."
)


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
    kernel.add_plugin(CRMPlugin(),      plugin_name="crm")
    kernel.add_plugin(ERPPlugin(),      plugin_name="erp")
    kernel.add_plugin(LogisticsPlugin(), plugin_name="logistics")
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
        function_choice_behavior=FunctionChoiceBehavior.Auto()
    )

    result = await kernel.get_service().get_chat_message_content(
        chat_history=history,
        settings=settings,
        kernel=kernel,
    )

    return str(result) if result else "I'm sorry, I couldn't process your request."
