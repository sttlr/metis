# SPDX-FileCopyrightText: Copyright 2025 Arm Limited and/or its affiliates <open-source-office@arm.com>
# SPDX-License-Identifier: Apache-2.0

import logging
from functools import partial

from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.graph import StateGraph, END

from .types import AskRequest, AskState
from .utils import retrieve_text, synthesize_context


logger = logging.getLogger("metis")


def ask_node_retrieve(state: AskState) -> AskState:
    question = state.get("question", "")
    code = retrieve_text(state["retriever_code"], question)
    docs = retrieve_text(state["retriever_docs"], question)

    context = synthesize_context(code, docs)
    s: AskState = dict(state)
    s["context"] = context
    s["code"] = code or ""
    s["docs"] = docs or ""
    return s


def ask_node_gather_context(
    state: AskState,
    chat_model,
    tools,
    plugin_config,
    max_turns,
) -> AskState:
    question = state.get("question", "")
    llm_with_tools = chat_model.bind_tools(tools)

    prompt = plugin_config.get("general_prompts", {}).get("ask_gather_context", "")
    formatted_prompt = prompt.format(question=question)
    messages = [SystemMessage(content=formatted_prompt)]

    gathered_context = []
    previous_tool_calls = None
    turns = 0

    while True:
        turns += 1
        if turns > max_turns:
            logger.warning(
                f"Max turns ({max_turns}) reached in ask context gathering, breaking loop"
            )
            break
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        if response.tool_calls:
            current_tool_calls = [
                (
                    tool_call.get("name", ""),
                    tool_call.get("args", {}) or tool_call.get("arguments", {}),
                )
                for tool_call in response.tool_calls
            ]
            if previous_tool_calls == current_tool_calls:
                for tool_call in response.tool_calls:
                    tool_call_id = tool_call.get("id", "")
                    error_msg = "Error: Repeated tool call detected. The same tool with the same arguments was requested again. Please vary your approach."
                    messages.append(
                        ToolMessage(content=error_msg, tool_call_id=tool_call_id)
                    )
                continue
            previous_tool_calls = current_tool_calls

            for tool_call in response.tool_calls:
                tool_call_id = tool_call.get("id", "")
                tool_name = tool_call.get("name", "")
                tool_args = (
                    tool_call.get("args", {})
                    if "args" in tool_call
                    else tool_call.get("arguments", {})
                )

                if not isinstance(tool_args, dict):
                    error_msg = (
                        f"Error: Invalid tool arguments format for tool '{tool_name}'"
                    )
                    messages.append(
                        ToolMessage(content=error_msg, tool_call_id=tool_call_id)
                    )
                    continue

                tool_instance = next((t for t in tools if t.name == tool_name), None)
                if tool_instance:
                    try:
                        result = tool_instance.func(**tool_args)
                        gathered_context.append(str(result))
                        messages.append(
                            ToolMessage(content=result, tool_call_id=tool_call_id)
                        )
                    except Exception as e:
                        error_msg = (
                            f"Error: Tool execution failed for '{tool_name}': {str(e)}"
                        )
                        messages.append(
                            ToolMessage(content=error_msg, tool_call_id=tool_call_id)
                        )
                else:
                    messages.append(
                        ToolMessage(
                            content=f"Error: Tool '{tool_name}' not found.",
                            tool_call_id=tool_call_id,
                        )
                    )
        else:
            break

    if response.content and response.content.strip():
        gathered_context.append(str(response.content.strip()))

    accumulated_context = "\n\n".join(gathered_context)
    s = dict(state)
    s["context"] = accumulated_context
    return s


def ask_node_answer(state: AskState, chat_model, plugin_config) -> AskState:
    question = state.get("question", "")
    context = state.get("context", "")
    prompt_template = plugin_config.get("general_prompts", {}).get("ask_answer", "")
    prompt = prompt_template.format(context=context, question=question)
    messages = [SystemMessage(content=prompt)]
    response = chat_model.invoke(messages)
    answer = response.content.strip()
    s = dict(state)
    s["answer"] = answer
    return s


class AskGraph:
    def __init__(
        self,
        llm_provider,
        llama_query_model,
        plugin_config,
        tools,
        max_turns,
        disable_embedding_search,
    ):
        self.llm_provider = llm_provider
        self.llama_query_model = llama_query_model
        self.plugin_config = plugin_config
        self.tools = tools
        self.max_turns = max_turns
        self.disable_embedding_search = disable_embedding_search
        self._chat_model = None
        self._app = None

    @property
    def chat_model(self):
        if self._chat_model is None:
            self._chat_model = self.llm_provider.get_chat_model(
                model=self.llama_query_model
            )
        return self._chat_model

    def _get_app(self):
        if self._app is not None:
            return self._app
        graph = StateGraph(AskState)

        entry_point = "answer"

        if not self.disable_embedding_search:
            graph.add_node("retrieve", partial(ask_node_retrieve))
            entry_point = "retrieve"

        if self.tools:
            graph.add_node(
                "gather_context",
                partial(
                    ask_node_gather_context,
                    chat_model=self.chat_model,
                    tools=self.tools,
                    plugin_config=self.plugin_config,
                    max_turns=self.max_turns,
                ),
            )
            if entry_point == "retrieve":
                graph.add_edge("retrieve", "gather_context")
            else:
                entry_point = "gather_context"

        graph.add_node(
            "answer",
            partial(
                ask_node_answer,
                chat_model=self.chat_model,
                plugin_config=self.plugin_config,
            ),
        )

        if entry_point == "retrieve":
            if self.tools:
                graph.add_edge("gather_context", "answer")
            else:
                graph.add_edge("retrieve", "answer")
        elif entry_point == "gather_context":
            graph.add_edge("gather_context", "answer")

        graph.set_entry_point(entry_point)
        graph.add_edge("answer", END)
        self._app = graph.compile()
        return self._app

    def ask(self, request: AskRequest):
        app = self._get_app()
        out = app.invoke(
            {
                "question": request["question"],
                "retriever_code": request["retriever_code"],
                "retriever_docs": request["retriever_docs"],
            }
        )
        # Return separate code/docs contexts for CLI printing compatibility
        return {
            "code": out.get("code", ""),
            "docs": out.get("docs", ""),
            "context": out.get("context", ""),
            "answer": out.get("answer", ""),
        }
