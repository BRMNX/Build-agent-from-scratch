import os
import re
import json
import subprocess
import base64
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv, find_dotenv

if not find_dotenv():
    raise FileNotFoundError(f".env file not found")
load_dotenv()
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
    base_url=os.environ.get("OPENAI_BASE_URL"),
)
WORKDIR = Path.cwd()
MODEL = os.environ.get("MODEL_ID")
DISPLAY = os.environ.get("DISPLAY_NAME", MODEL)
SYSTEM = (
    f"You are a coding agent at {WORKDIR}. "
    "All destructive operations require user approval. "
    "If a tool call is denied, explain what you were trying to do "
    "and ask the user how to proceed."
)
TOOLS = [
    {
        "type" : "function",
        "function" : {
            "name" : "bash",
            "description" : "Run a shell command.",
            "parameters" : {
                "type" : "object",
                "properties" : {"command" : {"type" : "string"}},
                "required" : ["command"],
            },
        },
    },
    {
        "type" : "function",
        "function" : {
            "name" : "read_file",
            "description" : "Read file contents.",
            "parameters" : {
                "type" : "object",
                "properties" : {
                    "path" : {"type" : "string"},
                    "limit" : {"type" : "integer"},
                },
                "required" : ["path"],
            },
        },
    },
    {
        "type" : "function",
        "function" : {
            "name" : "write_file",
            "description" : "Write contents to a file.",
            "parameters" : {
                "type" : "object",
                "properties" : {
                    "path" : {"type" : "string"},
                    "content" : {"type" : "string"},
                },
                "required" : ["path", "content"],
            },
        },
    },
    {
        "type" : "function",
        "function" : {
            "name" : "edit_file",
            "description" : "Replace exact text in a file once.",
            "parameters" : {
                "type" : "object",
                "properties" : {
                    "path" : {"type" : "string"},
                    "old_text" : {"type" : "string"},
                    "new_text" : {"type" : "string"},
                },
                "required" : ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type" : "function",
        "function" : {
            "name" : "glob_file",
            "description" : "Find files matching a glob pattern; ** matches recursively.",
            "parameters" : {
                "type" : "object",
                "properties" : {
                    "pattern" : {"type" : "string"},
                },
                "required" : ["pattern"],
            },
        },
    },
    {
        "type" : "function",
        "function" : {
            "name" : "read_image",
            "description" : "Read a local image file and return its content for visual analysis. Supports jpg, png, gif, webp.",
            "parameters" : {
                "type" : "object",
                "properties" : {
                    "path" : {"type" : "string"},
                },
                "required" : ["path"],
            },
        },
    },
]



def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_bash(command : str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(
            command,
            shell=True,              # 让系统 shell 解析命令
            cwd=os.getcwd(),         # 在项目根目录执行
            capture_output=True,     # 捕获 stdout 和 stderr
            text=True,               # 以文本形式返回
            errors="replace",        # 解码失败时替换而不是报错
            timeout=120,             # 120 秒超时
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"
    
def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = safe_path(path).read_text(encoding="utf-8").splitlines()
        # Actually, limit <= 0 is unexpected, so we add limit > 0.
        if limit is not None and limit > 0 and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        text = file_path.read_text(encoding="utf-8")
        if old_text not in text:
            return f"Error: text not found in {path}"
        file_path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

def run_glob(pattern: str) -> str:
    import glob as g
    try:
        matches = sorted({
            match for match in g.glob(
                pattern, root_dir=WORKDIR, recursive=True)
            if (WORKDIR / match).resolve().is_relative_to(WORKDIR)
        })
        shown = matches[:200]
        if len(matches) > 200:
            shown.append("... (more matches omitted; narrow the pattern)")
        return "\n".join(shown) if shown else "(no matches)"
    except Exception as e:
        return f"Error: {e}"

def run_read_image(path : str) -> str:
    try:
        file_path = safe_path(path)
        suffix = file_path.suffix.lower() # 文件的后缀
        suffix_map = {
            ".jpg" : "image/jpeg",
            ".jpeg" : "image/jpeg",
            ".png" : "image/png",
            ".gif" : "image/gif",
            ".webp" : "image/webp",
        }
        if suffix not in suffix_map:
            return f"Error: unsupported image format: {suffix}"
        data = base64.b64encode(file_path.read_bytes()).decode()
        return f"__IMAGE__:{suffix_map[suffix]}:{data}"
    except Exception as e:
        return f"Error: {e}"

"""
=============================
New content : s03_permission
=============================
"""
DENY_LIST = [
    # Unix 风格
    "rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sda",
    # Windows 风格
    "format ", "del /f /s /q", "rd /s /q", "diskpart",
]
def check_deny_list(command : str) -> str | None:
    for pattern in DENY_LIST:
        if pattern in command:
            return f"Blocked: '{pattern}' is on the deny list"
    return None

DESTRUCTIVE_COMMAND = re.compile(r"(?i)(?:^|[;&|()\n])\s*(?:rm|del)(?=\s|$|[;&|()])")
def has_destructive_command(command : str) -> bool:
    return bool(DESTRUCTIVE_COMMAND.search(command))

PERMISSION_RULE = [
    {"tools": ["read_file", "write_file", "edit_file", "read_image"],
     "need_check": lambda args: not (WORKDIR / args.get("path", "")).resolve().is_relative_to(WORKDIR),
     "message": "Accessing path outside workspace"},
    {"tools": ["bash"],
     "need_check": lambda args: has_destructive_command(args.get("command", "")) or
     any(kw in args.get("command", "") for kw in ["rm ", "del ", "> /etc/", "chmod 777"]),
     "message": "Potentially destructive command"},
]
def check_rules(tool_name: str, args: dict) -> str | None:
    for rule in PERMISSION_RULE:
        if tool_name in rule["tools"] and rule["need_check"](args):
            return rule["message"]
    return None

def ask_user(tool_name: str, args: dict, reason: str) -> str:
    print(f"\n⚠ {reason}")
    print(f"Tool: {tool_name}({args})")
    choice = input("===Allow? [yes/no]===").strip().lower()
    return "allow" if choice in ("yes", "y") else "deny"

def check_permission(tool_call) -> bool:
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    # Gate 1: Hard deny
    if name == "bash":
        reason = check_deny_list(args.get("command",""))
        if reason:
            print(f"\n⛔ {reason}")
            return False
    # Gate 2 + 3: Rule matching -> User approval 
    reason = check_rules(name, args)
    if reason:
        decision = ask_user(name, args, reason)
        if decision == "deny":
            return False
    return True

def agent_loop(messages : list):
    while True:
        response = client.chat.completions.create(
            model = MODEL,
            messages = messages,
            tools = TOOLS,
        )
        msg = response.choices[0].message
        """
        msg是模型最新一轮的响应
        响应结构：
            - msg.content -> 文本内容，可能是None
            - msg.tool_calls -> 工具调用列表，可能是None
        对于 msg.tool_calls 中每个 tool_call 的结构：
            - tool_call.id -> 调用id
            - tool_call.function.name -> 工具名
            - tool_call.function.arguments -> JSON字符串，需要json.loads()
        """
        messages.append(msg)
        # 若无工具调用，直接输出最后一次结果，并返回
        if not msg.tool_calls:
            if msg.content:
                print(msg.content)
            return
        # 若有工具调用，需要将工具信息重新回传到模型
        image_loads = []  # 收集本轮所有图像
        for tool_call in msg.tool_calls:
            if not check_permission(tool_call):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": "Permission denied.",
                })
                continue
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            handler = TOOL_HANDLERS.get(name)
            if handler:
                try:
                    print(f"Use tool <\033[33m{name}\033[0m>")
                    output = handler(**args)
                except TypeError as e:
                    output = f"Error: invalid arguments: {e}"
                except Exception as e:
                    output = f"Error: tool execution failed: {e}"
            else:
                output = f"Unknown tool: {name}"
            # 模型调用了read_image
            if isinstance(output, str) and output.startswith("__IMAGE__:"):
                _, suffix, data = output.split(":", 2)
                # tool_call 仍需回应
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"[Image loaded: {suffix}, {len(data)} chars of base64]",
                })
                # 将此图片的信息加入 image_loads 中
                image_loads.append((suffix, data, args.get("path", "image")))
            # 模型调用了别的工具
            else:
                messages.append({
                    "role" : "tool",
                    "tool_call_id" : tool_call.id,
                    "content" : output,
                })
        # 本轮如果有图像，追加一条 user 消息把图像传给模型
        if image_loads:
            content = []
            for suffix, data, path in image_loads:
                content.append({"type": "text", "text": f"Here is the image: {path}"})
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{suffix};base64,{data}"},
                })
            messages.append({"role": "user", "content": content})

TOOL_HANDLERS = {
    "bash" : run_bash,
    "read_file" : run_read,
    "write_file" : run_write,
    "edit_file" : run_edit,
    "glob_file" : run_glob,
    "read_image" : run_read_image,
}

if __name__ == "__main__":
    print("- Agent loop started.\n- Enter your question, press Enter to send.\n- Type q to quit.\n")
    messages = [{"role" : "system", "content" : SYSTEM}]
    while True:
        query = input(f"\001\033[36m\002{DISPLAY} >> \001\033[0m\002")
        if query.strip().lower() in ("q", "exit", ""):
            break
        messages.append({"role" : "user", "content" : query})
        agent_loop(messages=messages)
        print()

    