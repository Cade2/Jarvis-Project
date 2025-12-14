from typing import List
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
import json


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

    def chat(self, messages: List[str], max_new_tokens: int = 160) -> str:
        """
        Very simple chat interface.

        messages: ["User: ...", "Assistant:", ...]
        """
        prompt = "\n".join(messages)

        # Phi-2 docs recommend return_attention_mask=False for simplicity
        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            return_attention_mask=False,
        ).to(self.device)

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                top_p=0.9,
                temperature=0.7,
                repetition_penalty=1.1,
                pad_token_id=self.tokenizer.eos_token_id,
            )

        full = self.tokenizer.decode(outputs[0], skip_special_tokens=True)

        # Return only the new text after the prompt
        return full[len(prompt):].strip()
