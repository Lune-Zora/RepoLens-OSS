"""LLM 可插拔层。

仅用标准库 urllib 调用任意 OpenAI 兼容的 chat/completions 接口。
不参与定位；只负责「解释证据、生成计划与补丁」。失败时返回 None，由调用方回退。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional

from .llm_audit import LLMTrace


class LLMError(Exception):
    pass


class OpenAICompatLLM:
    """OpenAI 兼容接口客户端（可接公网模型，也可接自建网关）。"""

    def __init__(self, base_url: str, api_key: str,
                 model: str = "gpt-4o-mini", timeout: int = 60,
                 use_response_format: bool = False, purpose: str = "unspecified"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = int(os.environ.get("REPLENS_LLM_TIMEOUT", str(timeout)))
        # response_format 很多网关不支持（自建/国产模型），默认关闭
        self.use_response_format = use_response_format
        self.supports_schema = use_response_format
        self.purpose = purpose
        self.trace_context = {}

    def chat_json(self, system: str, user: str, temperature: float = 0.2, schema=None) -> Optional[dict]:
        """发送一次对话并解析 JSON 响应；调用/解析失败时抛出 LLMError。

        temperature 用于 Best-of-N 候选采样：不同温度产生多样性。
        """
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": int(os.environ.get("REPLENS_LLM_MAX_TOKENS", "2200")),
        }
        if os.environ.get("REPLENS_LLM_REASONING_EFFORT"):
            payload["reasoning_effort"] = os.environ["REPLENS_LLM_REASONING_EFFORT"]
        if self.use_response_format:
            payload["response_format"] = ({"type":"json_schema", "json_schema":{"name":"replens_response","strict":True,"schema":schema}}
                                          if schema is not None else {"type": "json_object"})
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            # Ollama 等本地服务不要求鉴权，仅在配置了 key 时附加
            req.add_header("Authorization", f"Bearer {self.api_key}")
        trace = LLMTrace(payload, self.purpose, self.trace_context, self.api_key)
        body = ""
        try:
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
                raise LLMError(f"LLM 调用失败: {e}") from e
            try:
                obj = json.loads(body)
                content = obj["choices"][0]["message"]["content"]
                if not content:
                    raise LLMError("模型没有返回最终答案（可能输出预算被推理消耗）；检查输出预算和reasoning_effort配置")
                result = content if isinstance(content, dict) else self._extract_json(content)
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                raise LLMError(f"LLM 响应解析失败: {e}") from e
        except Exception as e:
            trace.finish(response_text=body, error=e)
            raise
        trace.finish(response_text=body, parsed=result)
        return result

    @staticmethod
    def _extract_json(content: str) -> dict:
        """健壮地提取 JSON：兼容 markdown 代码块包裹、首尾花括号定位。"""
        # 直接解析
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        # 剥离 ```json ... ``` 代码块
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # 定位首尾花括号
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise LLMError("LLM 返回内容不含有效 JSON")


class TransformersLocalLLM:
    """本地 transformers 模型（用于 LoRA 微调后模型的直接加载与评测）。

    与 OpenAICompatLLM 保持相同接口（chat_json），因此 planner/patchgen 零改动切换。
    需要可选依赖：torch + transformers（pip install -i 清华源）。
    """

    def __init__(self, model_path: str):
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.model_path = model_path
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_path, trust_remote_code=True, torch_dtype="auto", device_map="auto")

    def chat_json(self, system: str, user: str, temperature: float = 0.2) -> Optional[dict]:
        try:
            import torch
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
            text = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True)
            inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)
            with torch.no_grad():
                out = self.model.generate(
                    **inputs, max_new_tokens=512,
                    temperature=temperature if temperature > 0 else 0.2,
                    do_sample=True, top_p=0.9)
            resp = self.tokenizer.decode(
                out[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
            return self._extract_json(resp)
        except Exception as e:
            raise LLMError("本地模型调用失败: " + str(e))

    @staticmethod
    def _extract_json(content: str) -> dict:
        import re as _re
        try:
            import json as _json
            return _json.loads(content)
        except Exception:
            pass
        m = _re.search(r"\{(.*)\}", content, _re.DOTALL)
        if m:
            try:
                import json as _json
                return _json.loads("{" + m.group(1) + "}")
            except Exception:
                pass
        raise LLMError("本地模型返回内容不含有效 JSON")
