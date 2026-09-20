# UserPrompt1

```
What skills are available?
```

# Output

```
[HOOK][info] getUserPrompt: working in C:\Users\27093\Desktop\aiagent\build-my-agent
There's one skill available:

- **code-review** — Perform thorough code reviews with security, performance, and maintainability analysis. Use it when you want to review code, check for bugs, or audit a codebase.

Want me to load it and run a review on something?
[HOOK][info] Stop, session used 0 tool calls.
```

# UserPrompt2

```
Use the code-review skill to review the code file s07_skills_loading/step8_skills_loading.py, but not change or edit anything.
```

# Output2

```
[HOOK][info] getUserPrompt: working in C:\Users\27093\Desktop\aiagent\build-my-agent
[HOOK][log] load_skill(...)
[HOOK][log] read_file(...)
[HOOK][info] ⚠ Large tool_output from read_file -> import ast import os import re import json import subprocess import base64 from pathlib import Path  ... nd({"role" : "user", "content" : query})         agent_loop(messages=messages)         print()
[HOOK][log] todo_write(...)
[HOOK][log] bash(...)
[HOOK][log] todo_write(...)

## Current Tasks
[x] Load code-review skill and read target file
[>] Gather context (skills/ dir, related step files, .env handling)
[ ] Analyze security, correctness, performance, maintainability, testing per checklist
[ ] Write structured review report (no file edits)

(1/4 completed)
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][info] ⚠ Large tool_output from bash -> .../step8_skills_loading.py                        | 417 ++++++++++++++-------  1 file changed, 288  ... stem_prompt()  def agent_loop(messages : list):      # 缁熻�� todo 绌洪棽鐨勮疆娆�      rounds_since_todo = 0
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][log] bash(...)
[HOOK][info] ⚠ Large tool_output from bash -> 165     return None 166  167 """=====事件 beforeToolUse 下的所有方法=====""" 168 DENY_LIST = [ 169     # Uni ... 747             }) 748             rounds_since_todo = 0 749  750 TODO_LIST = TodoManager() 751  752
[HOOK][log] todo_write(...)

## Current Tasks
[x] Load code-review skill and read target file
[x] Gather context (skills/ dir, related step files, .env handling)
[x] Analyze security, correctness, performance, maintainability, testing per checklist
[x] Write structured review report (no file edits)

(4/4 completed)
# Code Review: `s07_skills_loading/step8_skills_loading.py`

**Scope note:** No files were modified. I read the target file, the `SKILL.md` under `skills/`, `.env.example`, `.gitignore`, git history, and diffed against `s06_subagent/step7_subagent.py` (288 insertions / 129 deletions) to separate **new s07 code** from **inherited code**. A few behaviors were also verified with read-only Python snippets in a temp directory.

## Summary

The new skills layer (`SkillLoader` + `load_skill` tool + system-prompt catalog) is well-scoped and defensively written; the main risks are inherited and still critical — a permission gate that **fails open** when its hook raises, and a `bash` tool that bypasses the workspace sandbox entirely.

## Critical Issues

1. **Permission gate fails open on hook exception** (`trigger_blocking`, lines 133–141; used at lines ~688, ~300 for `beforeToolUse`)
   - `check_permission` calls `input()` (lines 188, 195). In any non-interactive context (piped stdin, CI, cron, a future headless mode), `input()` raises `EOFError` → caught by the bare `except Exception` → prints `[HOOK][ERROR]` → returns `None` → `permissionBlocked` is falsy → **the destructive command runs anyway.**
   - Impact: the "user approval" guarantee silently disappears in exactly the situations where no human can see it. Any exception inside a security hook (typo, `KeyError` on `tool_args`, closed pipe) has the same effect.
   - Fix: make security hooks fail closed — e.g. wrap only logging hooks in try/except, or have `trigger_blocking` return a deny string when a hook raises, or give hooks an explicit `fail_open: bool` flag defaulting to `False`.

2. **`bash` (`run_bash`, lines 30–49) is unbounded `shell=True` guarded only by blacklists** (`dangerous` line 32, `DENY_LIST` lines 168–173, `DESTRUCTIVE` line 177)
   - `rm -rf /tmp/build` → prompts; `/bin/rm -rf /tmp/build`, `find . -delete`, `python -c "...shutil.rmtree..."`, `printf x | tee /etc/hosts`, or `rm -r -f` all sail through. Blacklists cannot work here.
   - Impact: the `safe_path` workspace sandbox (line 24–28) protects every file tool but is completely bypassed by `bash`; `load_skill` makes this exploitable (see #3).
   - Fix: allow-list (`ls`, `cat`, `git status`, …) or an explicit argv-based `subprocess.run([...], shell=False)` API, keeping the prompt as a second gate rather than the only real one.

3. **Self-modifying skills = instruction persistence/escalation** (`scan` 415–464, `load` 478–488, `catalog` 465–477)
   - `write_file` to `skills/evil/SKILL.md` is *inside* WORKDIR, so it passes both the path gate and (for non-destructive content) user approval; `load_skill("evil")` then injects that text verbatim into the conversation, and `catalog()` (line 666) can push frontmatter text straight into the **system prompt**. Skills are also scanned once at import, so the injected text is available for the whole session.
   - Impact: indirect prompt injection with system-prompt reach; a compromised agent can plant instructions that survive its own context. Worse, a plain `git pull` of a malicious repo has the same effect.
   - Fix: treat skill content as untrusted data (wrap it in explicit delimiters + "this is reference material, not instructions"), require approval for writes under `skills/`, and consider scoping/limiting what `catalog()` admits into the system prompt (e.g. allow-list trusted skill names).

## Improvements

**Correctness**

4. **Line-ending rewriting in edits** (`run_edit` lines 70–79; `run_write` 61–68): `read_text` applies universal newlines and `write_text` uses `newline=None` → `\n` becomes `\r\n` on Windows. Verified: `b'a\nb\n'` → `b'a\r\nb\r\n'` after one read/write round-trip. A one-token edit silently reflows the whole file's line endings (and `old_text` containing `\r\n` can never match). Fix: `path.open("r", newline="", encoding="utf-8")` / `newline=""` on write, or do a bytes-level replace.

5. **One bad skill kills startup** (line 433, reached from `SKILL_LOADER = SkillLoader(SKILLS_DIR)` at line 489, i.e. at import, before any UI): `read_text` isn't guarded, so a UTF-16/binary `SKILL.md` or a permissions error aborts the whole agent with a traceback. Fix: per-manifest `try/except (OSError, UnicodeDecodeError)` + warning, `encoding="utf-8-sig"` to also survive BOMs.

6. **`parse_frontmatter` edge cases** (377–413): first-line check only strips `\r\n`, so `--- ` (trailing space) disables frontmatter; a BOM makes `lines[0] != "---"`. `description` fallback (line 452) is then the literal `"---"`. Also two skills declaring the same YAML `name` silently overwrite each other at line 460.

7. **Unvalidated model I/O**: `json.loads(tool_call.function.arguments)` (lines 300, 692) and `handler(**tool_args)` (line ~704 / ~315) are unguarded — empty or extra arguments raise `JSONDecodeError`/`TypeError` and kill the loop mid-turn, leaving the conversation in a state with an unanswered `tool_call_id`. Fix: validate against the tool schema, and append an error `tool` message on failure.

8. **`resolve()` compared against an unresolved `WORKDIR`** (lines 25–26, 87, 194). Today `Path.cwd() == Path.cwd().resolve()` here, so it works, but under a symlinked/junctioned CWD it fails closed in confusing ways (`safe_path` rejects everything with "Path escapes workspace"; the gate prompts every time). Fix: `WORKDIR = Path.cwd().resolve()` once, and reuse it. Also note `is_relative_to` is a lexical check — always `resolve()` first (which the code does correctly).

9. **No turn cap on the main loop** (line 670 `while True`) while the subagent is capped at 30 (lines 286–341). A model stuck in a tool loop burns tokens forever.

10. `run_read` silently ignores a negative `limit` (line 54) even though the comment calls it unexpected — return `Error:` instead, for consistency with `todo_write`'s validation.

**Performance**

11. **Images are re-uploaded every turn** (lines 731–739): the `data:` URL with full base64 stays in `messages` for the rest of the session, so every subsequent request resends megabytes. Consider summarizing the image to a placeholder after the turn.
12. `run_glob` (81–94) materializes *all* matches into a set before sorting and slicing to 200; a `**` pattern over a large tree is unbounded work for a capped result. Use `itertools.islice` and prune.
13. `large_output` (203–207) calls `str(tool_output)` up to 4 times — for base64 image strings that's several full copies. Compute once.
14. `load_skill` returns skill content unbounded (line 486), unlike `bash` (50k cap) and `read_file` (line cap). A large `SKILL.md` can flood the context window; cap it or truncate with a marker.

**Maintainability**

15. **Deny logic is triplicated with drift**: `run_bash`'s `dangerous` (line 32), `DENY_LIST` (168), `DESTRUCTIVE` + regex (174–177). `mkfs`/`format ` exist only in one list each, and `run_bash`'s copy runs even when the hook layer is bypassed. Collapse into one `permissions` module used by both.
16. Comment `# Gate 2 + 3` (line 191) but only two gates exist — misleading name for the "rule matching" gate.
17. Pervasive `except Exception as e: return f"Error: {e}"` (58, 67, 78, 93, 111, …) turns programming errors into plausible-looking tool output and leaks internal paths to the model/API. Narrow the exception types.
18. **Order-dependent subagent tool list**: `SUBAGENT_TOOLS` is computed at line 629 *before* `SUBAGENT` is appended at line 655, so recursion is excluded by accident; the comment at 628 claims it excludes `todo_write` *and* `subagent` while the filter only checks `todo_write`. Filter explicitly by name list.
19. `TOOLS` (490–615) is ~140 lines of near-identical dicts duplicated from step 6/7; consider deriving schemas from handler signatures, and keep the tool list beside `TOOL_HANDLERS`.
20. Module-level side effects at import: `SKILL_LOADER` (489) and `SYSTEM = build_system_prompt()` (669) mean importing the module requires `.env` and a skills dir. Guard with `if __name__ == "__main__"` or a `main()`.
21. `SKILLS_DIR = WORKDIR / "skills"` (369) is CWD-relative, so launching from `s07_skills_loading/` silently yields `(no skills found)`. Resolve from `Path(__file__).parent` or warn loudly.
22. Cosmetic: `f".env file not found"` (line 12, pointless f-string), `"""strings"""` used as comments (415–417, 419, 435–439, 443–448, 459), `import glob as g` inside a function (82) while everything else is top-level, mixed Chinese/English comments.

## Positive Notes

- `yaml.safe_load`, not `yaml.load` (line 442) — and it defensively handles `YAMLError` and non-dict results (444–450).
- Real symlink-escape guard in `scan` (line 430) with `resolve()` + `is_relative_to`, plus `is_file()` filtering.
- Description normalization (453–453) collapses newlines, so frontmatter **cannot** inject extra lines into the system prompt via `catalog()` — a genuinely thoughtful detail.
- `str.replace(old, new, 1)` — single replacement, as intended (line 76).
- Output caps: `bash` 50k (line 47), `glob` 200 (line 90).
- `safe_path` is applied consistently to **all** file tools including `read_image` (line 97) and rejects escapes.
- **Verified empirically** that `run_glob`'s filter neutralizes both known escapes: an absolute pattern (`C:/Windows/*.ini` — which `glob` lets escape `root_dir`) and `../*.txt` are both dropped by line 87. The guard works; it should nonetheless be paired with upfront pattern validation.
- Subagent is turn-capped (30) and correctly cannot recurse.
- Secrets hygiene: `.env` is gitignored and untracked (`git ls-files` shows only `.env.example`); `.env.example` carries placeholders.

## Testing

No tests exist anywhere in the repo (`s06_subagent/add.py` is a scratch file), and the README documents manual verification only. The new skill code is the most testable thing here: `parse_frontmatter` has ~7 branches (missing/`---`-only/BOM/CRLF/non-dict YAML/empty body), and `scan`/`catalog`/`load` are pure enough to drive with `tmp_path` (including a symlinked skill dir and a name-collision case). Also worth a regression test for finding #4 (line-ending preservation).

## Verdict

- [ ] Ready to merge
- [x] Needs minor changes — **for the s07 feature itself**: the `SkillLoader` design is sound; the changes I'd ask for before merging are the crash-safety of `scan()` (#5), frontmatter edge cases (#6), the unbounded `load_skill` output (#14), and the fragile `SUBAGENT_TOOLS` ordering (#18).
- [x] Needs major revision — **for the shared hook/bash layer it builds on**: issue #1 (fail-open permission gate) and #2 (blacklisted `shell=True`) should be fixed before anyone runs this unattended, and #3 becomes materially more exploitable now that `load_skill` can pull text into the system prompt. These are inherited from `step7_subagent.py`, not introduced by step 8 — worth deciding whether the tutorial series deliberately keeps them simple, in which case document the limitation explicitly rather than leaving it implicit.

Want me to open a branch and land the s07-scoped fixes (#5, #6, #14, #18) plus a small `tests/` file? I won't touch anything until you approve.
[HOOK][info] Stop, session used 22 tool calls.
```
