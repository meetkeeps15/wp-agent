"""
Agentic chat integration using LangGraph, adapted for CopilotKit-style messages.
"""

from typing import List, Any, Optional
import os
import asyncio

from langchain_core.runnables import RunnableConfig
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END, START
from langgraph.graph import MessagesState
from langgraph.types import Command


class AgentState(MessagesState):
    """State of our graph."""
    tools: List[Any]


async def chat_node(state: AgentState, config: Optional[RunnableConfig] = None):
    """
    Standard chat node based on the ReAct design pattern.
    Handles:
    - The model to use (and binds in tools)
    - The system prompt
    - Getting a response from the model
    - Handling tool calls
    """

    # 1. Define the model
    model = ChatOpenAI(model=os.environ.get("OPENAI_MODEL", "gpt-4o"))

    # Define config for the model
    if config is None:
        config = RunnableConfig(recursion_limit=25)

    # 2. Bind the tools to the model
    model_with_tools = model.bind_tools(
        [
            *state.get("tools", []),
        ],
        parallel_tool_calls=False,
    )

    # 3. Define the system message by which the chat model will be run
    system_message = SystemMessage(content=os.environ.get("SYSTEM_PROMPT", "You are a helpful assistant."))

    # 4. Run the model to generate a response
    response = await model_with_tools.ainvoke([
        system_message,
        *state["messages"],
    ], config)

    # 5/6. End the graph and update messages
    # MessagesState expects a list of messages to merge/append.
    return Command(
        goto=END,
        update={
            "messages": [response],
        },
    )


def compile_graph():
    """Compile the LangGraph workflow, optionally with a memory checkpointer."""
    workflow = StateGraph(AgentState)
    workflow.add_node("chat_node", chat_node)
    workflow.set_entry_point("chat_node")
    workflow.add_edge(START, "chat_node")
    workflow.add_edge("chat_node", END)

    is_fast_api = os.environ.get("LANGGRAPH_FAST_API", "false").lower() == "true"
    if is_fast_api:
        from langgraph.checkpoint.memory import MemorySaver
        memory = MemorySaver()
        graph = workflow.compile(checkpointer=memory)
    else:
        graph = workflow.compile()
    return graph


def _convert_messages(messages: List[dict]) -> List[Any]:
    """Convert CopilotKit-style dict messages to LangChain message objects."""
    converted: List[Any] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "user":
            converted.append(HumanMessage(content=content))
        elif role == "assistant":
            converted.append(AIMessage(content=content))
        elif role == "system":
            converted.append(SystemMessage(content=content))
        else:
            # Default to human for unknown roles to avoid failures
            converted.append(HumanMessage(content=content))
    return converted


def _extract_text(ai_message: Any) -> str:
    """Robustly extract text content from an AIMessage or raw response."""
    if isinstance(ai_message, AIMessage):
        if isinstance(ai_message.content, str):
            return ai_message.content
        # content can be a list (e.g., tool calls), try to stringify
        try:
            return " ".join(str(part) for part in ai_message.content if part)
        except Exception:
            return str(ai_message)
    # Fallback if the llm returns a Message or dict-like
    try:
        content = getattr(ai_message, "content", None)
        if isinstance(content, str):
            return content
    except Exception:
        pass
    return str(ai_message)


async def run_agentic_chat(graph, messages: List[dict], tools: Optional[List[Any]] = None, config: Optional[RunnableConfig] = None) -> str:
    """Invoke the compiled graph with provided messages and return the final text."""
    state = {
        "messages": _convert_messages(messages),
        "tools": tools or [],
    }
    result = await graph.ainvoke(state, config=config)

    # result should be a dict-like state with messages list including the AIMessage
    msgs = result.get("messages")
    # If the graph returned a list, pick the last AI message
    try:
        if isinstance(msgs, list):
            # Prefer the last AIMessage; if not found, use last message content
            for m in reversed(msgs):
                from langchain_core.messages import AIMessage
                if isinstance(m, AIMessage):
                    return _extract_text(m)
            # Fallback: use content of the last message
            last = msgs[-1] if msgs else None
            return _extract_text(last) if last is not None else ""
        # Otherwise, extract directly
        return _extract_text(msgs)
    except Exception:
        return str(msgs)