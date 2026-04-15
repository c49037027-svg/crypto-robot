"""
Gemini AI 客戶端 (使用最新 google-genai SDK)
- 有 API Key → 使用 Gemini AI 分析
- 無 API Key → 返回空字串，代理人自動改用規則邏輯
"""
import json
import re
from typing import Any, Dict, Optional
from utils.logger import get_logger

logger = get_logger("GeminiClient")


class GeminiClient:
    """
    Gemini API 封裝 (google-genai SDK)
    預設模型: gemini-2.0-flash (最新、快速、免費額度大)
    """

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.enabled = bool(api_key and api_key.strip())
        self._model_name = model
        self._client = None

        if self.enabled:
            try:
                from google import genai
                self._client = genai.Client(api_key=api_key)
                logger.info(f"Gemini AI 已啟用 (模型: {model})")
            except Exception as e:
                logger.error(f"Gemini 初始化失敗: {e}")
                self.enabled = False
        else:
            logger.info("未設定 GEMINI_API_KEY，使用純規則模式")

    def call(self, system_prompt: str, user_prompt: str) -> str:
        """
        呼叫 Gemini API，返回文字，失敗時返回空字串
        """
        if not self.enabled or self._client is None:
            return ""
        try:
            from google.genai import types
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=f"{system_prompt}\n\n{user_prompt}",
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=1024,
                ),
            )
            return response.text or ""
        except Exception as e:
            logger.warning(f"Gemini API 呼叫失敗: {e}")
            return ""

    def parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        """從 Gemini 回應中提取 JSON"""
        if not text:
            return None
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass
        for pattern in [
            r"```json\s*([\s\S]*?)\s*```",
            r"```\s*([\s\S]*?)\s*```",
            r"\{[\s\S]*\}",
        ]:
            m = re.search(pattern, text)
            if m:
                candidate = m.group(1) if "```" in pattern else m.group(0)
                try:
                    return json.loads(candidate.strip())
                except json.JSONDecodeError:
                    continue
        logger.debug(f"JSON 解析失敗: {text[:150]}")
        return None

    @property
    def mode(self) -> str:
        return f"Gemini ({self._model_name})" if self.enabled else "純規則模式"
