import json
from typing import Dict, Any


class DummyPlannerModel:
    """
    Placeholder for a future local LLM-based planner.

    For now, this class pretends to be an LLM by returning
    hard-coded JSON based on very simple keyword checks.

    Later, we'll replace the body of `generate` with a real call to
    a local model (e.g. via llama.cpp or another runtime).
    """

    def generate(self, prompt: str) -> str:
        # Naive keyword-based stub, just for structure/testing.
        lower = prompt.lower()

        # Very rough detection. In a real model, we'd parse the user message
        # inside the prompt instead of looking at the prompt text itself.
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
    
from typing import List
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


class ChatModel:
    """
    Basic local chat model wrapper.

    v0 uses a small Hugging Face text-generation model as a placeholder.
    Later we can swap this for a better instruct model or a llama.cpp backend
    without changing the rest of the code.
    """

    def __init__(self, model_name: str = "gpt2"):
        self.model_name = model_name
        print(f"[ChatModel] Loading model '{model_name}' (this might take a moment)...")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(model_name)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)

    def chat(self, messages: List[str], max_new_tokens: int = 120) -> str:
        """
        Very simple chat API.

        messages: list of strings (we'll just join them for now).
        """
        prompt = "\n".join(messages)

        inputs = self.tokenizer.encode(prompt, return_tensors="pt").to(self.device)

        with torch.no_grad():
            outputs = self.model.generate(
                inputs,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                top_p=0.9,
                temperature=0.8,
                pad_token_id=self.tokenizer.eos_token_id,
            )

        full = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        # Return only the new part after the original prompt
        return full[len(prompt):].strip()

