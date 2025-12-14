from __future__ import annotations
from typing import Any, Dict
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .tool_registry import run_tool, capabilities

app = FastAPI(title="Jarvis Runner", version="0.1")

class ToolRequest(BaseModel):
    params: Dict[str, Any] = {}
    approval_token: str | None = None

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}

@app.get("/capabilities")
def get_capabilities() -> Dict[str, Any]:
    return capabilities()

@app.post("/tool/{tool_name}")
def tool_call(tool_name: str, req: ToolRequest) -> Dict[str, Any]:
    try:
        return run_tool(tool_name, req.params or {})
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")

if __name__ == "__main__":
    main()
