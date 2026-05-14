"""Deploy Agent — 部署配置生成 (Docker/CI/CD)"""

from backend.agents.base import BaseAgent
from backend.core.prompts import DEPLOY_SYSTEM


class DeployAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="deploy",
            system_prompt=DEPLOY_SYSTEM,
            tools=["write_file", "run_shell"],
            use_rag=True,
        )

    def generate(self, user_input: str, project_summary: str = "") -> dict:
        prompt = DEPLOY_SYSTEM.format(
            project_summary=project_summary or user_input,
            rag_context="{rag_context}",
        )
        self.system_prompt = prompt
        state = self.run(user_input=user_input, stage="deploy")
        return {
            "deploy_config": state.get("generation_output", ""),
            "rag_context": state.get("rag_context", ""),
        }
