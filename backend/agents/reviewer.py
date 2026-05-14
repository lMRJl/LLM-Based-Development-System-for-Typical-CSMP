"""Reviewer Agent - structured review plus targeted code repair with parallel execution."""

import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from backend.agents.base import BaseAgent
from backend.core.prompts import REVIEWER_SYSTEM


class ReviewerAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="reviewer",
            system_prompt=REVIEWER_SYSTEM,
            tools=["read_file", "write_file"],
            use_rag=True,
        )

    # Module names that can have file-level repairs
    _REPAIRABLE_MODULES = {"backend", "frontend", "database"}

    def review(
        self,
        user_input: str,
        artifacts_to_review: str = "",
        generated_files: dict[str, list[dict]] | None = None,
        validations: dict | None = None,
        tech_stack: dict | None = None,
    ) -> dict:
        """Review artifacts and repair files when findings are repairable."""
        generated_files = generated_files or {}
        validations = validations or {}
        tech_stack = tech_stack or {}

        rag_context = self._retrieve_review_context(user_input, artifacts_to_review)
        review_output = self._structured_review(
            user_input=user_input,
            artifacts_to_review=artifacts_to_review,
            validations=validations,
            tech_stack=tech_stack,
            rag_context=rag_context,
        )
        findings = self._parse_findings(review_output)
        repaired_files = self._repair_files(
            user_input=user_input,
            findings=findings,
            generated_files=generated_files,
            tech_stack=tech_stack,
            context=artifacts_to_review,
        )

        repair_summary = self._format_repair_summary(repaired_files)
        review_report = review_output
        if repair_summary:
            review_report = f"{review_output.rstrip()}\n\n## 自动修复结果\n{repair_summary}"

        return {
            "review_report": review_report,
            "rag_context": rag_context,
            "findings": findings,
            "files": repaired_files,
        }

    def _retrieve_review_context(self, user_input: str, artifacts_to_review: str) -> str:
        if not self.use_rag:
            return ""
        query = (
            f"code review security audit OWASP Top 10 SQL注入 XSS CSRF "
            f"authentication authorization RBAC JWT audit log API consistency "
            f"{user_input[:300]} {artifacts_to_review[:500]}"
        )
        result = self.rag.retrieve(
            query=query,
            top_k=5,
            kb_types=["compliance", "pattern", "template"],
            use_query_transform=True,
            use_rerank=True,
            use_self_rag=True,
            use_graph=False,
        )
        return result.get("context_string", "")

    def _structured_review(
        self,
        user_input: str,
        artifacts_to_review: str,
        validations: dict,
        tech_stack: dict,
        rag_context: str,
    ) -> str:
        prompt = REVIEWER_SYSTEM.format(
            user_input=user_input,
            tech_stack=json.dumps(tech_stack, ensure_ascii=False, indent=2),
            artifacts_to_review=artifacts_to_review or "无产出物摘要。",
            validations=json.dumps(validations, ensure_ascii=False, indent=2),
            rag_context=rag_context or "无相关安全/合规上下文。",
        )
        return self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=4096,
        )

    def _parse_findings(self, review_output: str) -> list[dict]:
        # Strategy 1: extract ```json ... ``` code block
        blocks = re.findall(r"```json\s*(.*?)```", review_output, re.DOTALL | re.IGNORECASE)
        candidates = blocks or [review_output]
        for raw in candidates:
            try:
                parsed = json.loads(raw.strip())
                findings = parsed.get("findings", []) if isinstance(parsed, dict) else []
                if isinstance(findings, list):
                    return [f for f in findings if isinstance(f, dict)]
            except json.JSONDecodeError:
                continue

        # Strategy 2: regex fallback — extract each finding object fragment
        print("[ReviewerAgent] WARNING: JSON parse failed, attempting regex fallback for findings")
        findings = []
        # Find JSON-object-like fragments containing "module" field
        fragments = re.findall(r'\{[^}]*"module"[^}]*\}', review_output, re.DOTALL)
        for fragment in fragments:
            def _extract(pattern, text, default="", group=1):
                m = re.search(pattern, text)
                return m.group(group) if m else default

            findings.append({
                "module": _extract(r'"module"\s*:\s*"(\w+)"', fragment),
                "file_path": _extract(r'"file_path"\s*:\s*"([^"]*)"', fragment),
                "severity": _extract(r'"severity"\s*:\s*"(critical|high|medium|low)"', fragment, "medium"),
                "issue": _extract(r'"issue"\s*:\s*"([^"]*)"', fragment),
                "fix_instruction": _extract(r'"fix_instruction"\s*:\s*"([^"]*)"', fragment),
                "repairable": _extract(r'"repairable"\s*:\s*(true|false)', fragment, "false") == "true",
            })

        # Filter out fragments where no meaningful data was extracted
        findings = [f for f in findings if f["module"] and f["issue"]]
        if findings:
            print(f"[ReviewerAgent] Regex fallback extracted {len(findings)} findings")
        return findings

    def _repair_files(
        self,
        user_input: str,
        findings: list[dict],
        generated_files: dict[str, list[dict]],
        tech_stack: dict,
        context: str,
    ) -> list[dict]:
        files_by_path: dict[str, str] = {}
        all_file_paths: dict[str, list[str]] = defaultdict(list)
        for module in self._REPAIRABLE_MODULES:
            for item in generated_files.get(module, []):
                if isinstance(item, dict) and item.get("path") and isinstance(item.get("code"), str):
                    files_by_path[item["path"]] = item["code"]
                    all_file_paths[module].append(item["path"])

        # Group findings by file_path
        grouped_findings: dict[str, list[dict]] = defaultdict(list)
        for finding in findings:
            if not finding.get("repairable"):
                continue
            module = finding.get("module", "")
            if module not in self._REPAIRABLE_MODULES:
                continue
            path = str(finding.get("file_path", "")).strip()
            if path in files_by_path:
                grouped_findings[path].append(finding)

        if not grouped_findings:
            return []

        # Build cross-file context: list of sibling files in the same module
        sibling_context = {}
        for path in grouped_findings:
            module = "backend" if path.startswith("backend/") else (
                "frontend" if path.startswith("frontend/") else "database"
            )
            siblings = all_file_paths.get(module, [])
            sibling_context[path] = f"Module '{module}' files: {', '.join(siblings[:30])}"

        # Parallel repair across independent files
        repaired: list[dict] = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {}
            for path, path_findings in grouped_findings.items():
                futures[path] = executor.submit(
                    self._repair_single_file,
                    user_input=user_input,
                    file_path=path,
                    code=files_by_path[path],
                    findings=path_findings,
                    tech_stack=tech_stack,
                    context=context,
                    sibling_info=sibling_context.get(path, ""),
                )

            for path, future in futures.items():
                try:
                    repaired_path, repaired_code = future.result(timeout=120)
                    # Validate: reject repairs that still have GENERATION_ERROR
                    if "GENERATION_ERROR" in repaired_code:
                        print(f"[ReviewerAgent] Repair failed for {path} (GENERATION_ERROR), keeping original")
                        continue
                    if not repaired_code.strip():
                        print(f"[ReviewerAgent] Repair produced empty code for {path}, keeping original")
                        continue
                    repaired.append({"path": repaired_path, "code": repaired_code})
                except Exception as e:
                    print(f"[ReviewerAgent] Repair exception for {path}: {e}, keeping original")

        return repaired

    def _repair_single_file(
        self,
        user_input: str,
        file_path: str,
        code: str,
        findings: list[dict],
        tech_stack: dict,
        context: str,
        sibling_info: str = "",
    ) -> tuple[str, str]:
        prompt = f"""你是资深代码修复工程师。根据审查发现修复指定文件，输出完整的修复后文件内容。

硬性要求:
- 只能修复当前文件 ({file_path})，不要输出其他文件。
- 必须保持与已有模块、{sibling_info}中列出的同模块文件接口一致。
- 不要删除无关功能，只修复审查发现中列出的问题。
- 输出格式必须严格为:
PATH：{file_path}
CODE：
<完整修复后代码，不要 Markdown 围栏>

用户需求摘要:
{user_input}

技术栈:
{json.dumps(tech_stack, ensure_ascii=False, indent=2)}

同模块文件（修复时需保持接口一致）:
{sibling_info}

相关上下文:
{context[:5000]}

当前文件:
{file_path}

当前代码:
{code}

审查发现（只修复以下问题）:
{json.dumps(findings, ensure_ascii=False, indent=2)}

重要: 输出必须以 PATH：{file_path} 开头，紧接着 CODE：换行后写完整代码。"""
        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=8192,
        )
        return self._parse_path_code_response(response, default_path=file_path)

    def _parse_path_code_response(self, response: str, default_path: str) -> tuple[str, str]:
        text = response.replace("\r\n", "\n").strip()
        match = re.search(
            r"^\s*PATH\s*[:：]\s*(.*?)\s*\n\s*CODE\s*[:：]\s*\n?(.*)\s*$",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if match:
            path = match.group(1).strip().replace("\\", "/") or default_path
            code = self._strip_code_fence(match.group(2).strip())
            return path, self._ensure_trailing_newline(code)
        return default_path, self._ensure_trailing_newline(self._strip_code_fence(text))

    def _strip_code_fence(self, code: str) -> str:
        if code.startswith("```"):
            lines = code.split("\n")
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            code = "\n".join(lines)
        return code.strip()

    def _ensure_trailing_newline(self, code: str) -> str:
        return code.rstrip() + "\n"

    def _format_repair_summary(self, repaired_files: list[dict]) -> str:
        if not repaired_files:
            return ""
        lines = ["已生成以下修复文件，并将覆盖对应项目文件:"]
        for item in repaired_files:
            lines.append(f"- {item['path']}")
        return "\n".join(lines)
