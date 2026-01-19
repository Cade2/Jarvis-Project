# agent/models.py
from __future__ import annotations

from typing import List, Tuple, Any, Dict, Optional
from pathlib import Path
import json
import socket
import time
import http.client
import urllib.request
import urllib.error




def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _normalize_ollama_host(host: str) -> str:
    h = (host or "http://127.0.0.1:11434").strip().rstrip("/")
    for suffix in ("/api", "/v1"):
        if h.endswith(suffix):
            h = h[: -len(suffix)]
    return h


def _load_models_config() -> Dict[str, Any]:
    cfg_path = _repo_root() / "config" / "models.json"
    if cfg_path.exists():
        return json.loads(cfg_path.read_text(encoding="utf-8"))

    return {
        "models": {
            "general": {"provider": "ollama", "name": "llama3.1:8b"},
            "coder": {"provider": "ollama", "name": "qwen2.5-coder:14b"},
            "research": {"provider": "ollama", "name": "qwen2.5:14b-instruct-q4_K_M"},
        },
        "ollama": {"host": "http://127.0.0.1:11434", "timeout_seconds": 600},
        "generation": {
            "general": {"num_predict": 120, "temperature": 0.4},
            "coder": {"num_predict": 220, "temperature": 0.2},
            "research": {"num_predict": 350, "temperature": 0.3},
        },
    }


class OllamaModel:
    """
    Minimal Ollama client using stdlib only.
    """
    def __init__(self, model_name: str, host: str, timeout_seconds: int = 600):
        self.model_name = model_name
        self.host = _normalize_ollama_host(host)
        self.timeout_seconds = int(timeout_seconds)

    def chat(
        self,
        messages: List[str],
        max_new_tokens: int = 256,
        temperature: float = 0.2,
        format: Optional[str] = None,   # e.g. "json"
        **kwargs,
    ) -> str:
        prompt = "\n".join(messages)

        url = f"{self.host}/api/generate"
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": int(max_new_tokens),
                "temperature": float(temperature),
            },
        }

        # If you pass format="json", Ollama will try to force valid JSON output.
        if format:
            payload["format"] = format

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        # Retry transient “connection dropped” cases (Ollama sometimes restarts under load)
        last_err: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    obj = json.loads(body)
                    return (obj.get("response") or "").strip()

            except (
                ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
                OSError,
                http.client.RemoteDisconnected,
                http.client.IncompleteRead,
                json.JSONDecodeError,
            ) as e:
                last_err = e
                time.sleep(0.4 * attempt)
                continue

            except urllib.error.HTTPError as e:
                err_body = ""
                try:
                    err_body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                raise RuntimeError(f"Ollama HTTP {e.code} {e.reason}: {err_body[:500]}") from e

            except (TimeoutError, socket.timeout) as e:
                raise RuntimeError(
                    f"Ollama timed out after {self.timeout_seconds}s for '{self.model_name}'."
                ) from e

            except urllib.error.URLError as e:
                # If the reason is a dropped connection, treat it like a retryable error
                reason = getattr(e, "reason", None)
                if isinstance(
                    reason,
                    (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError, http.client.RemoteDisconnected),
                ):
                    last_err = e
                    time.sleep(0.4 * attempt)
                    continue

                raise RuntimeError(
                    f"Ollama not reachable at {self.host}. Is `ollama serve` running?"
                ) from e

        raise RuntimeError(
            f"Ollama connection dropped while generating from '{self.model_name}'. "
            f"This usually means the model crashed/OOM or the request was too large. "
            f"Try a smaller coder model or lower num_predict."
        ) from last_err


class ChatModel:
    """
    HF fallback model (phi-2 etc).
    """
    def __init__(self, model_name: str = "microsoft/phi-2"):
        # Lazy imports so Ollama-only installs don't break
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._torch = torch
        self._AutoModelForCausalLM = AutoModelForCausalLM
        self._AutoTokenizer = AutoTokenizer

        self.model_name = model_name
        print(f"[ChatModel] Loading model '{model_name}'...")

        self.tokenizer = self._AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        self.model = self._AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=self._torch.float32,
            trust_remote_code=True,
        )

        self.device = self._torch.device("cuda" if self._torch.cuda.is_available() else "cpu")
        self.model.to(self.device)

        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

    def chat(self, messages: List[str], max_new_tokens: int = 256, temperature: float = 0.2, **kwargs) -> str:
        torch = self._torch

        prompt = "\n".join(messages)
        enc = self.tokenizer(prompt, return_tensors="pt", padding=False)
        input_ids = enc["input_ids"].to(self.device)
        attention_mask = torch.ones_like(input_ids)

        with torch.no_grad():
            outputs = self.model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                top_p=0.9,
                temperature=temperature,
                repetition_penalty=1.1,
                pad_token_id=self.tokenizer.eos_token_id,
            )

        full = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        return full[len(prompt):].strip()


class RoleModel:
    def __init__(self, base_model, num_predict: int, temperature: float):
        self.base = base_model
        self.num_predict = num_predict
        self.temperature = temperature

    def chat(self, messages: List[str], max_new_tokens: int = None, temperature: float = None, **kwargs) -> str:
        return self.base.chat(
            messages,
            max_new_tokens=max_new_tokens or self.num_predict,
            temperature=self.temperature if temperature is None else temperature,
            **kwargs,
        )


def build_model(model_cfg: Dict[str, Any], ollama_host: str, ollama_timeout_seconds: int) -> Any:
    provider = (model_cfg.get("provider") or "hf").lower()
    name = model_cfg.get("name") or "microsoft/phi-2"

    if provider == "ollama":
        return OllamaModel(name, host=ollama_host, timeout_seconds=int(ollama_timeout_seconds))

    return ChatModel(model_name=name)


from typing import Tuple, Any

def load_model_roles() -> Tuple[Any, Any, Any, Any, Any, Any]:
    cfg = _load_models_config()

    ollama_cfg = cfg.get("ollama") or {}
    ollama_host = _normalize_ollama_host(ollama_cfg.get("host", "http://127.0.0.1:11434"))
    ollama_timeout_seconds = int(ollama_cfg.get("timeout_seconds", 600))
    print(f"[Models] Ollama host: {ollama_host} | timeout: {ollama_timeout_seconds}s")

    models_cfg = cfg.get("models") or {}
    gen_cfg = cfg.get("generation") or {}

    def _gen(name: str, default_num: int, default_temp: float):
        g = gen_cfg.get(name) or {}
        num = int(g.get("num_predict", default_num))
        temp = float(g.get("temperature", default_temp))
        return num, temp

    # Build base models
    general_base = build_model(models_cfg.get("general", {}), ollama_host, ollama_timeout_seconds)
    coder_base = build_model(models_cfg.get("coder", {}), ollama_host, ollama_timeout_seconds)
    research_base = build_model(models_cfg.get("research", {}), ollama_host, ollama_timeout_seconds)

    math_cfg = models_cfg.get("math")
    math_base = build_model(math_cfg, ollama_host, ollama_timeout_seconds) if math_cfg else research_base

    science_cfg = models_cfg.get("science")
    science_base = build_model(science_cfg, ollama_host, ollama_timeout_seconds) if science_cfg else general_base

    review_cfg = models_cfg.get("review")
    review_base = build_model(review_cfg, ollama_host, ollama_timeout_seconds) if review_cfg else research_base

    # Generation settings (safe defaults)
    g_num, g_temp = _gen("general", 120, 0.4)
    c_num, c_temp = _gen("coder", 220, 0.2)
    r_num, r_temp = _gen("research", 350, 0.3)
    m_num, m_temp = _gen("math", 200, 0.0)
    s_num, s_temp = _gen("science", 400, 0.2)
    v_num, v_temp = _gen("review", 500, 0.0)

    return (
        RoleModel(general_base, num_predict=g_num, temperature=g_temp),
        RoleModel(coder_base, num_predict=c_num, temperature=c_temp),
        RoleModel(research_base, num_predict=r_num, temperature=r_temp),
        RoleModel(math_base, num_predict=m_num, temperature=m_temp),
        RoleModel(science_base, num_predict=s_num, temperature=s_temp),
        RoleModel(review_base, num_predict=v_num, temperature=v_temp),
    )




