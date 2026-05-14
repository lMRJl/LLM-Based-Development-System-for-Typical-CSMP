from openai import OpenAI
from backend.config import get_settings


class LLMClient:
    """DeepSeek LLM 客户端 — OpenAI SDK 兼容 (Chat only)"""

    _DEFAULT_TIMEOUT = 120.0  # seconds per API call
    _LARGE_TIMEOUT = 300.0    # for large max_tokens (>16K)

    def __init__(self):
        settings = get_settings()
        self.client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=self._LARGE_TIMEOUT,
        )
        self.model = settings.deepseek_model

    def chat(self, messages: list[dict], stream: bool = False, **kwargs):
        kwargs.setdefault("timeout", self._DEFAULT_TIMEOUT)
        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=stream,
            **kwargs,
        )

    def chat_sync(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096) -> str:
        # Use longer timeout for large generation requests
        call_timeout = self._LARGE_TIMEOUT if max_tokens > 16384 else self._DEFAULT_TIMEOUT
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
                timeout=call_timeout,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            import logging
            logging.getLogger("llm").error(f"chat_sync failed: {e}")
            raise

    def chat_stream(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096):
        call_timeout = self._LARGE_TIMEOUT if max_tokens > 16384 else self._DEFAULT_TIMEOUT
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                timeout=call_timeout,
            )
            for chunk in response:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            import logging
            logging.getLogger("llm").error(f"chat_stream failed: {e}")
            raise


_llm_instance: LLMClient | None = None


def get_llm():
    global _llm_instance
    if _llm_instance is None:
        import os
        if os.environ.get("MOCK_LLM", "").lower() in ("true", "1", "yes"):
            from backend.core.mock_llm import get_mock_llm
            _llm_instance = get_mock_llm()
        else:
            _llm_instance = LLMClient()
    return _llm_instance
