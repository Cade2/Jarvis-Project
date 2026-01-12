from typing import List
import json

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from typing import List, Tuple, Any, Dict
from pathlib import Path
import urllib.request
import urllib.error



class DummyPlannerModel:
    """
    Placeholder for a future local LLM-based planner.

    For now, this class pretends to be an LLM by returning
    hard-coded JSON based on very simple keyword checks.
    """

    def generate(self, prompt: str) -> str:
        lower = prompt.lower()

        if "remind me" in lower:
            return json.dumps({
                "tool_name": "create_reminder",
                "params": {
                    "text": "Reminder created by DummyPlannerModel",
                    "when": "tomorrow 18:00"
                }
            })

        if "open " in lower:
            return json.dumps({
                "tool_name": "open_application",
                "params": {
                    "app_name": "notepad"
                }
            })

        # Default: no tool
        return json.dumps({
            "tool_name": "none",
            "params": {}
        })


class OllamaModel:
    """
    Minimal Ollama client using stdlib only (no requests dependency).
    Uses /api/generate with a single prompt string.
    """
    def __init__(self, model_name: str, host: str = "http://127.0.0.1:11434"):
        self.model_name = model_name
        self.host = host.rstrip("/")

    def chat(self, messages: List[str], max_new_tokens: int = 256, temperature: float = 0.2) -> str:
        prompt = "\n".join(messages)

        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            # max_new_tokens equivalent in Ollama is num_predict
            "options": {
                "num_predict": int(max_new_tokens),
                "temperature": float(temperature),
            },
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read().decode("utf-8")
                obj = json.loads(body)
                return (obj.get("response") or "").strip()
        except urllib.error.URLError as e:
            raise RuntimeError(
                "Ollama is not reachable. Make sure it's running (try: `ollama serve`) "
                f"and that {self.host} is accessible."
            ) from e



class ChatModel:
    """
    Local chat model wrapper using Microsoft's Phi-2.

    This is the "brain" Jarvis uses whenever no tool is chosen.
    """

    def __init__(self, model_name: str = "microsoft/phi-2"):
        self.model_name = model_name
        print(f"[ChatModel] Loading model '{model_name}' (this might take a moment)...")

        # Load tokenizer & model
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            trust_remote_code=True,
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float32,      # safer default for CPU; we'll optimise later
            trust_remote_code=True,
        )

        # Pick device
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)

        # Make sure pad token is set to avoid attention_mask warnings
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

    def chat(self, messages: List[str], max_new_tokens: int = 256, temperature: float = 0.2) -> str:
        """
        Very simple chat interface.

        Args:
            messages: List of chat turns (e.g. ["User: ...", "Assistant:", ...]).
            max_new_tokens: Maximum number of new tokens to generate.
        """
        prompt = "\n".join(messages)

        # Tokenise without relying on pad/attention_mask tricks
        enc = self.tokenizer(
            prompt,
            return_tensors="pt",
            padding=False,
        )
        input_ids = enc["input_ids"].to(self.device)

        # Explicit attention mask = all ones (no padding)
        attention_mask = torch.ones_like(input_ids)

        with torch.no_grad():
            outputs = self.model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                top_p=0.9,
                temperature=0.6,          # slightly more focused
                repetition_penalty=1.1,
                pad_token_id=self.tokenizer.eos_token_id,
            )

        full = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        # Return only the new text after the prompt
        return full[len(prompt):].strip()


def _repo_root() -> Path:
    # agent/models.py -> agent/ -> jarvis-agent/
    return Path(__file__).resolve().parent.parent

def _load_models_config() -> Dict[str, Any]:
    cfg_path = _repo_root() / "config" / "models.json"
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # Safe defaults (if user forgot to create config/models.json)
    return {
        "models": {
            "general": {"provider": "ollama", "name": "llama3.1:8b"},
            "coder": {"provider": "ollama", "name": "qwen2.5-coder:14b"},
            "research": {"provider": "ollama", "name": "qwen2.5:14b-instruct-q4_K_M"},
        },
        "ollama": {"host": "http://127.0.0.1:11434"},
    }

def build_model(model_cfg: Dict[str, Any], ollama_host: str) -> Any:
    provider = (model_cfg.get("provider") or "hf").lower()
    name = model_cfg.get("name") or "microsoft/phi-2"

    if provider == "ollama":
        return OllamaModel(name, host=ollama_host)

    # fallback to your current HF model
    return ChatModel(model_name=name)

def load_model_roles() -> Tuple[Any, Any, Any]:
    """
    Returns: (general_model, coder_model, research_model)

    Each returned model exposes:
      `.chat(messages: List[str], max_new_tokens=..., temperature=...) -> str`

    RoleModel wraps each model to apply default generation settings from config.
    """
    cfg = _load_models_config()
    ollama_host = (cfg.get("ollama") or {}).get("host", "http://127.0.0.1:11434")

    models_cfg = cfg.get("models") or {}
    general = build_model(models_cfg.get("general", {}), ollama_host)
    coder = build_model(models_cfg.get("coder", {}), ollama_host)
    research = build_model(models_cfg.get("research", {}), ollama_host)

    # Apply per-role generation defaults (speed/quality tuning)
    gen_cfg = cfg.get("generation") or {}

    g = gen_cfg.get("general", {})
    c = gen_cfg.get("coder", {})
    r = gen_cfg.get("research", {})

    general_wrapped = RoleModel(
        general,
        num_predict=int(g.get("num_predict", 120)),
        temperature=float(g.get("temperature", 0.4)),
    )
    coder_wrapped = RoleModel(
        coder,
        num_predict=int(c.get("num_predict", 220)),
        temperature=float(c.get("temperature", 0.2)),
    )
    research_wrapped = RoleModel(
        research,
        num_predict=int(r.get("num_predict", 350)),
        temperature=float(r.get("temperature", 0.3)),
    )

    return general_wrapped, coder_wrapped, research_wrapped



class RoleModel:
    def __init__(self, base_model, num_predict: int, temperature: float):
        self.base = base_model
        self.num_predict = num_predict
        self.temperature = temperature

    def chat(self, messages: List[str], max_new_tokens: int = None, temperature: float = None) -> str:
        return self.base.chat(
            messages,
            max_new_tokens=max_new_tokens or self.num_predict,
            temperature=temperature if temperature is not None else self.temperature,
        )

