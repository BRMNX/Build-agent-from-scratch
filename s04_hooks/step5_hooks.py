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
        return out[:50000] if out else "(no tool_output)"
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
=======================
s04 new content : hooks
=======================
"""

""" 
=========================================================
    + HOOKS类似于注册表，HOOKS 中的每个键都是一个 事件        
    + 每个事件包含若干方法，所有方法都需要先向HOOKS注册才能够调用
=========================================================
"""
HOOKS = {
    "beforeLLM": [],
    "beforeToolUse": [],
    "afterToolUse": [],
    "afterLLM": [],
}
"""注册方法"""
def register_hook(event: str, _function_):
    HOOKS[event].append(_function_)

"""每个方法都配备一个触发器，只有符合触发条件，才调用该方法"""
def trigger_hook(event: str, short_circuit: bool = True,**kwargs):
    # short_circuit: bool 是否在此 hook 触发时直接截断
    # **kwargs: 方便统一传入参数，每个 hook 选择自己所需的参数即可
    if short_circuit:
        return trigger_blocking(event, **kwargs)
    return trigger_notify(event, **kwargs)
    
# 截断型 hook
def trigger_blocking(event: str, **kwargs):
    for _function_ in HOOKS[event]:
        try:
            result = _function_(**kwargs)
            if result is not None: # 触发且被截断 -> 返回触发结果，不再执行后续hook
                return result
        except Exception as e:
            print(f"[HOOK][ERROR] {_function_.__name__}: {e}")
    return None # 不触发 -> 程序继续
# 通知型 hook
def trigger_notify(event: str, **kwargs):
    for _function_ in HOOKS[event]:
        try:
            _function_(**kwargs)
        except Exception as e:
            print(f"[HOOK][ERROR] {_function_.__name__}: {e}")
    return None # 不论触发/不触发 -> 程序继续

"""=====事件 beforeLLM 下的所有方法====="""
def getUserPrompt():
    print(f"\033[90m[HOOK][info] getUserPrompt: working in {WORKDIR}\033[0m")
    return None

"""=====事件 afterLLM 下的所有方法====="""
def summary(messages: list):
    def get_role(m):
        if isinstance(m, dict):
            return m.get("role")
        return getattr(m, "role", None)

    tool_count = sum(1 for m in messages if get_role(m) == "tool")
    print(f"\033[90m[HOOK][info] Stop, session used {tool_count} tool calls.\033[0m")
    return None

"""=====事件 beforeToolUse 下的所有方法====="""
DENY_LIST = [
    # Unix 风格
    "rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sda",
    # Windows 风格
    "format ", "del /f /s /q", "rd /s /q", "diskpart",
]
DESTRUCTIVE_COMMAND_WORD = re.compile(
    r"(?i)(?:^|[;&|()\n])\s*(?:rm|del)(?=\s|$|[;&|()])"
)
DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]
def check_permission(tool_name: str, tool_args: dict, **kwargs):
    # Gate 1: Hard deny
    if tool_name == "bash":
        command = tool_args.get("command", "")
        for pattern in DENY_LIST:
            if pattern in command:
                return "Permission denied by DENY_LIST."
            if bool(DESTRUCTIVE_COMMAND_WORD.search(command)) or any(keyword in command for keyword in DESTRUCTIVE):
                print(f"\n\033[33m[permission] Potentially destructive command\033[0m")
                print(f"Tool: {tool_name}({command})")
                choice = input("===Allow? [yes/No]=== ").strip().lower()
                if choice not in ("y", "yes"):
                    return "Permission denied by user"
    # Gate 2 + 3: Rule matching -> User approval 
    if tool_name in ("read_file", "write_file", "edit_file", "glob_file", "read_image"):
        path = tool_args.get("path", "")
        if not (WORKDIR / path).resolve().is_relative_to(WORKDIR):
            choice = input("===Allow? [yes/No]===\n").strip().lower()
            if choice not in ("y", "yes"):
                return "Permission denied by user."
    return None
def log_before_tool_use(tool_name: str, **kwargs):
    print(f"[HOOK][log] {tool_name}(...)")

"""=====事件 afterToolUse 下的所有方法====="""
def large_output(tool_name: str, tool_output, **kwargs):
    if len(str(tool_output)) > 5000:
        preview = str(tool_output).replace("\n", " ")[:100] + " ... " + str(tool_output).replace("\n", " ")[-100:]
        print(f"\033[90m[HOOK][info] ⚠ Large tool_output from {tool_name} -> {preview}\033[0m")

def agent_loop(messages : list):
    while True:
        response = client.chat.completions.create(
            model = MODEL,
            messages = messages,
            tools = TOOLS,
        )
        msg = response.choices[0].message
        messages.append(msg)
        # 若无工具调用，直接输出最后一次结果，并返回
        if not msg.tool_calls:
            if msg.content:
                print(msg.content)
            trigger_hook("afterLLM", short_circuit=False, messages=messages)
            return
        # 若有工具调用，需要将工具信息重新回传到模型
        image_loads = []  # 收集本轮所有图像
        for tool_call in msg.tool_calls:
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)
            """beforeToolUse事件，先判断该事件中是否有 方法 触发，如果触发，立即终止"""
            permissionBlocked = trigger_hook("beforeToolUse", short_circuit=True, tool_name=tool_name, tool_args=tool_args)
            if permissionBlocked:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(permissionBlocked)
                })
                continue
            handler = TOOL_HANDLERS.get(tool_name)
            tool_output = handler(**tool_args) if handler else f"Unknown: {tool_name}"
            """afterToolUse事件，判断该事件中是否有 方法 触发，即使触发，仍需检查后续hook"""
            trigger_hook("afterToolUse", short_circuit=False, tool_name=tool_name, tool_args=tool_args, tool_output=tool_output)
            # 模型调用了read_image
            if isinstance(tool_output, str) and tool_output.startswith("__IMAGE__:"):
                _, suffix, data = tool_output.split(":", 2)
                # tool_call 仍需回应
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"[Image loaded: {suffix}, {len(data)} chars of base64]",
                })
                # 将此图片的信息加入 image_loads 中
                image_loads.append((suffix, data, tool_args.get("path", "image")))
            # 模型调用了别的工具
            else:
                messages.append({
                    "role" : "tool",
                    "tool_call_id" : tool_call.id,
                    "content" : tool_output,
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

def Register():
    register_hook("beforeLLM", getUserPrompt)

    register_hook("beforeToolUse", log_before_tool_use)
    register_hook("beforeToolUse", check_permission)

    register_hook("afterToolUse", large_output)

    register_hook("afterLLM", summary)

if __name__ == "__main__":
    Register()
    print("- Agent loop started.\n- Enter your question, press Enter to send.\n- Type q to quit.\n")
    messages = [{"role" : "system", "content" : SYSTEM}]
    while True:
        query = input(f"\001\033[36m\002{DISPLAY} >> \001\033[0m\002")
        if query.strip().lower() in ("q", "exit", ""):
            break
        trigger_hook("beforeLLM", short_circuit=False)
        messages.append({"role" : "user", "content" : query})
        agent_loop(messages=messages)
        print()

    