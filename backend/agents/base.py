"""Agent 基类 — LangGraph StateGraph 封装 + RAG 自动注入 + Tool 调用"""

import json
from typing import TypedDict, Annotated, Callable
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

from backend.core.llm import get_llm
from backend.core.tools import TOOL_DEFINITIONS, TOOL_EXECUTORS
from backend.rag import get_rag_pipeline


# --- Agent State ---
class AgentState(TypedDict):
    messages: Annotated[list[dict], add_messages]
    user_input: str
    stage: str
    rag_context: str
    rag_results: list[dict]
    generation_output: str
    tool_results: list[dict]
    next_stage: str
    error: str


class BaseAgent:
    """LangGraph Agent 基类 — 所有专用 Agent 继承此类"""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[str] | None = None,
        use_rag: bool = True,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.tool_names = tools or []
        self.use_rag = use_rag
        self.llm = get_llm()
        self.rag = get_rag_pipeline()
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph StateGraph for this agent."""
        workflow = StateGraph(AgentState)

        # Add nodes
        workflow.add_node("rag_retrieve", self._rag_retrieve_node)
        workflow.add_node("generate", self._generate_node)
        workflow.add_node("tool_execute", self._tool_execute_node)

        # Set entry
        workflow.set_entry_point("rag_retrieve")

        # Add edges
        workflow.add_edge("rag_retrieve", "generate")
        workflow.add_conditional_edges(
            "generate",
            self._should_use_tools,
            {"tool_execute": "tool_execute", END: END},
        )
        workflow.add_edge("tool_execute", "generate")

        return workflow.compile()

    # --- Nodes ---
    def _rag_retrieve_node(self, state: AgentState) -> dict:
        """Node 1: RAG retrieval — inject context into state."""
        if not self.use_rag:
            return {"rag_context": "", "rag_results": []}

        query = state.get("user_input", "")
        if not query:
            return {"rag_context": "", "rag_results": []}

        result = self.rag.retrieve(
            query=query,
            top_k=5,
            use_query_transform=True,
            use_rerank=True,
            use_self_rag=True,
            use_graph=True,
        )

        return {
            "rag_context": result.get("context_string", ""),
            "rag_results": result.get("documents", []),
        }

    def _generate_node(self, state: AgentState) -> dict:
        """Node 2: Generate response with LLM."""
        from backend.core.prompts import NO_PREAMBLE

        # Build system prompt with RAG context
        rag_ctx = state.get("rag_context", "")
        sys_prompt = self.system_prompt

        # Substitute RAG placeholder
        if "{rag_context}" in sys_prompt:
            sys_prompt = sys_prompt.replace("{rag_context}", rag_ctx or "无相关上下文")
        if "{user_input}" in sys_prompt:
            sys_prompt = sys_prompt.replace("{user_input}", state.get("user_input", ""))

        # Append anti-preamble directive to all agents
        sys_prompt += NO_PREAMBLE

        messages = [{"role": "system", "content": sys_prompt}]

        # Add conversation history
        history = state.get("messages", [])
        messages.extend(history[-10:])  # Keep last 10 messages

        # Include tool results if any
        tool_results = state.get("tool_results", [])
        if tool_results:
            for tr in tool_results[-3:]:
                messages.append({
                    "role": "user",
                    "content": f"Tool result: {tr.get('output', '')}",
                })

        if self.tool_names:
            response = self.llm.chat_sync(
                messages=messages,
                temperature=0.7,
                max_tokens=4096,
            )
        else:
            response = self.llm.chat_sync(
                messages=messages,
                temperature=0.7,
                max_tokens=4096,
            )

        return {"generation_output": response}

    def _tool_execute_node(self, state: AgentState) -> dict:
        """Node 3: Execute tool calls extracted from generation output."""
        output = state.get("generation_output", "")
        tool_calls = self._extract_tool_calls(output)

        results = []
        for call in tool_calls:
            tool_name = call.get("name", "")
            if tool_name in TOOL_EXECUTORS:
                args = call.get("arguments", {})
                try:
                    result = TOOL_EXECUTORS[tool_name](**args)
                    results.append({"tool": tool_name, "output": result})
                except Exception as e:
                    results.append({"tool": tool_name, "output": f"Error: {e}"})

        return {"tool_results": results}

    # --- Routing ---
    def _should_use_tools(self, state: AgentState) -> str:
        """Determine if tool execution is needed."""
        if not self.tool_names:
            return END

        output = state.get("generation_output", "")
        # Check if the output contains a tool call pattern
        if "```tool" in output or "TOOL_CALL:" in output:
            return "tool_execute"
        return END

    def _extract_tool_calls(self, text: str) -> list[dict]:
        """Extract tool calls from LLM output. Robust against format variations."""
        calls = []
        import re as _re

        # Pattern 1: TOOL_CALL: tool_name {"arg": "value"}
        for line in text.split("\n"):
            if "TOOL_CALL:" in line:
                try:
                    parts = line.split("TOOL_CALL:", 1)[1].strip()
                    name, args_str = parts.split(" ", 1) if " " in parts else (parts, "{}")
                    calls.append({"name": name.strip(), "arguments": json.loads(args_str)})
                except (json.JSONDecodeError, ValueError):
                    pass

        # Pattern 2: ```tool ... ``` block and variations
        for pattern in [
            r'```tool\s*\n(.*?)```',           # ```tool
            r'```\s*tool\s*\n(.*?)```',         # ``` tool (with space)
        ]:
            blocks = _re.findall(pattern, text, _re.DOTALL)
            for block in blocks:
                try:
                    parsed = json.loads(block.strip())
                    if isinstance(parsed, dict) and "name" in parsed:
                        calls.append(parsed)
                    elif isinstance(parsed, list):
                        calls.extend(c for c in parsed if isinstance(c, dict) and "name" in c)
                except json.JSONDecodeError:
                    pass

        # Pattern 3: @tool_name file_path format (code-friendly, no JSON escaping needed)
        # ```tool\n@write_file backend/main.py\n<code>\n```
        at_blocks = _re.findall(
            r'```tool\s*\n\s*@(\w+)\s+(\S+)\s*\n(.*?)```',
            text, _re.DOTALL,
        )
        for tool_name, file_path, content in at_blocks:
            if tool_name and file_path:
                calls.append({
                    "name": tool_name,
                    "arguments": {"file_path": file_path.strip(), "content": content.rstrip()},
                })

        # Pattern 4: Fallback — find JSON with "write_file" anywhere in text
        if not calls and '"write_file"' in text:
            try:
                # Find the first { ... } containing write_file
                match = _re.search(
                    r'\{\s*"name"\s*:\s*"write_file"[^}]*"arguments"\s*:\s*\{.*?\}\s*\}',
                    text, _re.DOTALL,
                )
                if match:
                    parsed = json.loads(match.group(0))
                    if isinstance(parsed, dict) and "name" in parsed:
                        calls.append(parsed)
            except json.JSONDecodeError:
                pass

        return calls

    # --- Multi-turn file generation ---
    def generate_files(self, user_input: str, stage: str, file_prompt_builder) -> list[tuple[str, str]]:
        """
        Multi-turn code generation: plan files → generate each file → return list.
        file_prompt_builder(file_path, rag_context, user_input, **ctx) → str
        """
        from backend.core.prompts import NO_PREAMBLE

        # Phase 1: RAG retrieval
        rag_context = ""
        if self.use_rag:
            result = self.rag.retrieve(query=user_input, top_k=5)
            rag_context = result.get("context_string", "")

        # Phase 2: Plan — ask LLM to list files
        plan_prompt = f"""列出此项目需要生成的所有文件清单。
每行一个文件路径，按依赖关系排序（底层模块在前，入口文件在后）。
包含所有必要的模型、Schema、API路由、Service、中间件、配置、依赖文件。

项目需求: {user_input}

参考知识库:
{rag_context or "无"}

{NO_PREAMBLE}
文件清单:"""
        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": plan_prompt}],
            temperature=0.3, max_tokens=1024,
        )
        raw_files = [line.strip() for line in response.strip().split("\n") if line.strip()]
        # Filter: keep only lines that look like file paths (contain extension)
        files = [f for f in raw_files if "." in f and "/" in f]
        print(f"[MultiFile] Plan: {len(files)} files → {files[:5]}...")

        # Phase 3: Generate each file with per-file token allocation
        results = []
        for i, file_path in enumerate(files):
            # Larger files get more tokens; entry points and services get extra
            is_large = any(kw in file_path for kw in ["main.py", "service", "App.tsx", "api.ts"])
            per_file_tokens = 12288 if is_large else 8192

            file_prompt = file_prompt_builder(file_path, rag_context, user_input)
            file_prompt += NO_PREAMBLE

            code = self.llm.chat_sync(
                messages=[{"role": "user", "content": file_prompt}],
                temperature=0.3, max_tokens=per_file_tokens,
            )
            # Strip markdown fences
            code = code.strip()
            if code.startswith("```"):
                lines = code.split("\n")
                code = "\n".join(lines[1:]) if len(lines) > 1 else code
            if code.endswith("```"):
                code = code[:-3].strip()
            results.append((file_path, code))
            print(f"[MultiFile] {i+1}/{len(files)}: {file_path} ({len(code)} chars, {per_file_tokens} tok limit)")

        return results, rag_context

    # --- Execution ---
    def run(self, user_input: str, stage: str = "", messages: list[dict] | None = None) -> AgentState:
        """Execute the agent synchronously."""
        initial_state: AgentState = {
            "messages": messages or [],
            "user_input": user_input,
            "stage": stage,
            "rag_context": "",
            "rag_results": [],
            "generation_output": "",
            "tool_results": [],
            "next_stage": "",
            "error": "",
        }
        result = self.graph.invoke(initial_state)
        return result

    def run_stream(self, user_input: str, stage: str = "", messages: list[dict] | None = None):
        """Execute the agent with streaming output."""
        initial_state: AgentState = {
            "messages": messages or [],
            "user_input": user_input,
            "stage": stage,
            "rag_context": "",
            "rag_results": [],
            "generation_output": "",
            "tool_results": [],
            "next_stage": "",
            "error": "",
        }

        for output in self.graph.stream(initial_state):
            yield output
