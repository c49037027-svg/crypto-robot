"""
基礎代理人類別
所有 AI Agent 繼承此類，統一 Claude API 呼叫介面
"""
import json
import re
from typing import Any, Dict, Optional
import anthropic
from utils.logger import get_logger

logger = get_logger("BaseAgent")


class BaseAgent:
    """
    所有 Agent 的基類
    - 提供 Claude API 呼叫
    - 統一 JSON 解析
    - 錯誤處理與重試
    """

    def __init__(self, name: str, system_prompt: str, model: str, api_key: str,
                 max_tokens: int = 4096):
        self.name = name
        self.system_prompt = system_prompt
        self.model = model
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic(api_key=api_key)
        self._call_count = 0
        self._log = get_logger(name)

    def _call_claude(self, user_message: str, temperature: float = 0.3) -> str:
        """
        呼叫 Claude API，返回原始文字回應
        使用同步 API (在 asyncio 中用 run_in_executor 包裝)
        """
        self._call_count += 1
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text
        except anthropic.APIError as e:
            self._log.error(f"Claude API 錯誤: {e}")
            return ""
        except Exception as e:
            self._log.error(f"未預期錯誤: {e}")
            return ""

    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        """從 Claude 回應中提取 JSON"""
        # 嘗試直接解析
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # 嘗試提取 ```json ... ``` 區塊
        patterns = [
            r"```json\s*([\s\S]*?)\s*```",
            r"```\s*([\s\S]*?)\s*```",
            r"\{[\s\S]*\}",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                candidate = match.group(1) if "```" in pattern else match.group(0)
                try:
                    return json.loads(candidate.strip())
                except json.JSONDecodeError:
                    continue

        self._log.warning(f"無法解析 JSON，原始回應: {text[:200]}")
        return None
