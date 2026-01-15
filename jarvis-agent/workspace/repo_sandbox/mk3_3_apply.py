from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
CORE = ROOT / "agent" / "core.py"
DEVTOOLS = ROOT / "agent" / "devtools.py"

DEV_MARKER = "# MK3.3 Dev task router"


def build_dev_block() -> str:
    # Build line-by-line to avoid nested triple-quote issues
    lines = [
        "# -------------------------",
        "# MK3.3 Dev task router",
        "# -------------------------",
        "",
        "_DEV_TRIGGER_RE = re.compile(",
        '    r"\\b(',
        "        fix|debug|refactor|implement|add\\s+(a\\s+)?feature|add\\s+support|make\\s+it\\s+work|",
        "        code\\s+review|cleanup|document|lint|format|optimi[sz]e|performance\\s+issue|",
        "        stack\\s*trace|traceback|exception|error\\s+code|build\\s+failed|test\\s+failed",
        '    )\\b",',
        "    re.IGNORECASE | re.VERBOSE,",
        ")",
        "",
        "_DEV_SHORTCUTS = {",
        '    "dev status", "devmode status", "dev",',
        '    "sandbox reset", "dev sandbox reset", "reset sandbox",',
        '    "discard patch", "dev discard patch", "cancel patch",',
        '    "apply patch", "dev apply patch",',
        '    "propose patch",',
        "}",
        "",
        "def _is_dev_request(text_lower: str, normalized: str) -> bool:",
        "    # Don't intercept explicit dev shortcuts (those are handled by existing commands)",
        "    if normalized in _DEV_SHORTCUTS:",
        "        return False",
        "",
        "    # Strong signals that we're talking about code / errors",
        '    if "traceback" in text_lower:',
        "        return True",
        '    if "syntaxerror" in text_lower or "importerror" in text_lower or "typeerror" in text_lower:',
        "        return True",
        '    if "exception" in text_lower or "stack trace" in text_lower:',
        "        return True",
        '    if "error" in text_lower and ("line " in text_lower or "file " in text_lower):',
        "        return True",
        "",
        "    return bool(_DEV_TRIGGER_RE.search(text_lower))",
        "",
        "",
        "def _extract_repo_paths(text_in: str):",
        "    candidates = re.findall(r\"\\b(?:agent|runner|config|workspace)\\\\[\\w\\-./\\\\]+\\b\", text_in)",
        "    candidates += re.findall(r\"\\b(?:agent|runner|config|workspace)/[\\w\\-./]+\\b\", text_in)",
        "",
        "    cleaned = []",
        "    for p in candidates:",
        "        p = p.replace('\\\\', '/')",
        "        if p.startswith('workspace/'):",
        "            continue",
        "        if p not in cleaned:",
        "            cleaned.append(p)",
        "    return cleaned[:6]",
        "",
        "",
        "def _extract_query_tokens(user_text: str):",
        "    raw = user_text or ''",
        "    tokens = set()",
        "",
        "    for t in re.findall(r\"[A-Za-z_][A-Za-z0-9_\\.]{2,}\", raw):",
        "        tl = t.lower()",
        "        if tl in ('jarvis', 'python', 'windows', 'please'):",
        "            continue",
        "        if len(t) >= 4:",
        "            tokens.add(t)",
        "",
        "    for t in list(tokens):",
        "        if t.lower().endswith(('error', 'exception')):",
        "            tokens.add(t)",
        "",
        "    return list(tokens)[:8]",
        "",
        "",
        "def _summarize_matches(matches, limit=25) -> str:",
        "    out = []",
        "    for m in matches[:limit]:",
        "        f = m.get('file', '')",
        "        ln = m.get('line_no', '')",
        "        line = m.get('line', '')",
        "        out.append(f\"{f}:{ln}: {line}\")",
        "    return \"\\n\".join(out)",
        "",
        "",
        "def _dev_collect_context(user_text: str) -> str:",
        "    paths = _extract_repo_paths(user_text)",
        "    tokens = _extract_query_tokens(user_text)",
        "",
        "    search_blobs = []",
        "    read_blobs = []",
        "",
        "    for p in paths:",
        "        out = _run_tool('code.read_file', {'path': p, 'max_lines': 160, 'start_line': 1})",
        "        if out and isinstance(out, dict) and out.get('result'):",
        "            lines = out['result'].get('lines', [])",
        "            read_blobs.append(f\"--- FILE: {p} ---\\n\" + \"\\n\".join(lines))",
        "",
        "    base_path = 'agent'",
        "    if 'runner' in (user_text or '').lower():",
        "        base_path = 'runner'",
        "",
        "    for tok in tokens[:3]:",
        "        out = _run_tool('code.search', {'query': tok, 'path': base_path, 'max_files': 50, 'max_matches': 30})",
        "        if out and isinstance(out, dict) and out.get('result'):",
        "            res = out['result']",
        "            matches = res.get('matches', [])",
        "            if matches:",
        "                search_blobs.append(f\"--- SEARCH: {tok} (in {res.get('path')}) ---\\n\" + _summarize_matches(matches))",
        "",
        "    context = []",
        "    if search_blobs:",
        "        context.append(\"\\n\\n\".join(search_blobs))",
        "    if read_blobs:",
        "        context.append(\"\\n\\n\".join(read_blobs))",
        "",
        "    return \"\\n\\n\".join(context).strip()",
        "",
        "",
        # NOTE: removed typing annotations to avoid missing imports in core.py
        "def _dev_generate_patch(user_request: str, context_blob: str, compile_feedback: str = ''):",
        "    prompt = [",
        "        'You are the CODER model for the Jarvis repo.',",
        "        'Goal: generate a SMALL, correct unified diff (git apply compatible) to implement the requested change.',",
        "        'Rules:',",
        "        '- Output JSON ONLY.',",
        "        '- Schema: {\"description\": string, \"diff\": string}.',",
        "        \"- diff MUST be a unified diff with file paths relative to repo root, like 'agent/core.py'.\",",
        "        '- Do NOT include backticks. Do NOT include explanations outside JSON.',",
        "        '- Prefer minimal edits. Keep formatting consistent.',",
        "        '',",
        '        f"User request: {user_request}",',
        "    ]",
        "",
        "    if compile_feedback.strip():",
        "        prompt.append('')",
        "        prompt.append('Sandbox compile feedback (from previous attempt):')",
        "        prompt.append(compile_feedback)",
        "",
        "    if context_blob.strip():",
        "        prompt.append('')",
        "        prompt.append('Repo context:')",
        "        prompt.append(context_blob)",
        "",
        "    prompt.append('')",
        "    prompt.append('JSON:')",
        "",
        "    raw = _coder_model.chat(prompt).strip()",
        "    obj = _extract_first_json_object(raw) or {}",
        "    desc = obj.get('description', '').strip()",
        "    diff_text = (obj.get('diff', '') or '').rstrip() + '\\n' if obj.get('diff') else ''",
        "",
        "    return {'description': desc, 'diff': diff_text, 'raw': raw}",
        "",
        "",
        "def _handle_dev_request(user_text: str) -> None:",
        "    print('Jarvis: Entering Dev Mode (sandbox-first).')",
        "",
        "    context_blob = _dev_collect_context(user_text)",
        "",
        "    last_feedback = ''",
        "    for attempt in range(1, 4):",
        "        patch = _dev_generate_patch(user_text, context_blob, compile_feedback=last_feedback)",
        "        diff_text = patch.get('diff', '')",
        "",
        "        if not diff_text.strip():",
        "            print('Jarvis: I could not produce a valid diff yet. Try including the error text or file path.')",
        "            return",
        "",
        "        desc = patch.get('description') or f'Dev Mode patch attempt {attempt}'",
        "        result = _run_tool('dev.propose_patch', {'diff': diff_text, 'description': desc})",
        "",
        "        ok = False",
        "        feedback = ''",
        "        if isinstance(result, dict):",
        "            res = result.get('result') or {}",
        "            ok = bool(res.get('compileall_ok'))",
        "            feedback = (res.get('compileall_output_tail') or '').strip()",
        "",
        "        if ok:",
        "            print('Jarvis: ✅ Sandbox checks passed. If you want to apply this patch to the real repo, type: apply patch')",
        "            return",
        "",
        "        if not feedback:",
        "            print('Jarvis: Sandbox checks failed, but I could not retrieve compile output. Use `dev status` to inspect.')",
        "            return",
        "",
        "        print('Jarvis: Sandbox checks failed. I will attempt a fix based on the compile output.')",
        "        last_feedback = feedback",
        "",
        "    print('Jarvis: I tried a few times but could not get a clean sandbox pass. Use `dev status` to review the latest output.')",
        "",
        "",
    ]
    return "\n".join(lines) + "\n"


def remove_first_top_level_function(text: str, func_name: str) -> (str, bool):
    starts = [m.start() for m in re.finditer(rf"^def {re.escape(func_name)}\(", text, flags=re.M)]
    if len(starts) < 2:
        return text, False  # no duplicate

    start = starts[0]
    # End at next top-level def after the first one
    after = text.find("\ndef ", start + 1)
    if after == -1:
        return text, False

    new_text = text[:start] + text[after + 1 :]
    return new_text, True


def insert_before_handle_user_message(text: str, block: str) -> (str, bool):
    if DEV_MARKER in text:
        return text, False

    anchor = "def handle_user_message(user_message: str) -> None:"
    idx = text.find(anchor)
    if idx == -1:
        raise RuntimeError("Could not find handle_user_message() in agent/core.py")
    return text[:idx] + block + "\n" + text[idx:], True


def insert_autorouter_call(text: str) -> (str, bool):
    if "MK3.3 Dev Mode router (auto)" in text:
        return text, False

    target = "norm = _apply_global_replacements(normalized)"
    idx = text.find(target)
    if idx == -1:
        raise RuntimeError("Could not find 'norm = _apply_global_replacements(normalized)' in agent/core.py")

    # Insert right after the line
    line_end = text.find("\n", idx)
    if line_end == -1:
        raise RuntimeError("Unexpected end of file while patching agent/core.py")

    # Determine indentation from the target line
    line_start = text.rfind("\n", 0, idx) + 1
    indent = re.match(r"\s*", text[line_start:idx]).group(0)

    insert = (
        "\n"
        + f"{indent}# -------------------------\n"
        + f"{indent}# MK3.3 Dev Mode router (auto)\n"
        + f"{indent}# -------------------------\n"
        + f"{indent}if _is_dev_request(text_lower, normalized):\n"
        + f"{indent}    _handle_dev_request(raw)\n"
        + f"{indent}    return\n"
    )

    new_text = text[: line_end + 1] + insert + text[line_end + 1 :]
    return new_text, True


def patch_devtools(text: str) -> (str, bool):
    if '"compileall_output_tail"' in text:
        return text, False

    new, n = re.subn(
        r'("compileall_ok": ok,\n)(\s*"run_log": run_log,\n)',
        r'\1            "compileall_output_tail": compile_out[-4000:] if isinstance(compile_out, str) else "",\n\2',
        text,
        count=1,
    )
    if n == 0:
        raise RuntimeError("Could not patch dev_propose_patch() return block in agent/devtools.py")
    return new, True


def main():
    if not CORE.exists() or not DEVTOOLS.exists():
        print("ERROR: Run this from repo root (same folder as cli.py).")
        sys.exit(1)

    core_text = CORE.read_text(encoding="utf-8", errors="replace")
    dev_text = DEVTOOLS.read_text(encoding="utf-8", errors="replace")

    changed = False
    core_changes = []
    dev_changes = []

    # Remove duplicate older helpers (keep the last copy)
    core_text, did1 = remove_first_top_level_function(core_text, "_auto_alias")
    if did1:
        changed = True
        core_changes.append("Removed first duplicate _auto_alias() (kept last).")

    core_text, did2 = remove_first_top_level_function(core_text, "_resolve_command")
    if did2:
        changed = True
        core_changes.append("Removed first duplicate _resolve_command() (kept last).")

    # Insert Dev Mode block and autorouter call
    dev_block = build_dev_block()
    core_text, did3 = insert_before_handle_user_message(core_text, dev_block)
    if did3:
        changed = True
        core_changes.append("Inserted MK3.3 Dev Mode router block.")

    core_text, did4 = insert_autorouter_call(core_text)
    if did4:
        changed = True
        core_changes.append("Inserted Dev Mode auto-router call in handle_user_message().")

    # Patch devtools to return compile tail
    dev_new, did5 = patch_devtools(dev_text)
    if did5:
        changed = True
        dev_text = dev_new
        dev_changes.append("Added compileall_output_tail to dev.propose_patch result.")

    # Write files
    CORE.write_text(core_text, encoding="utf-8")
    DEVTOOLS.write_text(dev_text, encoding="utf-8")

    if not changed:
        print("No changes needed (already applied).")
        return

    print("Applied MK3.3 Dev Mode changes:")
    for c in core_changes + dev_changes:
        print(f" - {c}")


if __name__ == "__main__":
    main()
