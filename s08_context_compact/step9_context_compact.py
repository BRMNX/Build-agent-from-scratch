import ast
import io
import os
import re
import json
import subprocess
import base64
from pathlib import Path
from PIL import Image
from types import SimpleNamespace
import uuid
from openai import OpenAI
from dotenv import load_dotenv, find_dotenv
import yaml

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
        # 打开图片
        img = Image.open(file_path)
        # 如果原图很大，才缩；400×400 不需要缩
        if max(img.size) > 1024:
            img.thumbnail((1024, 1024))

        # 关键：以 quality=60 重新编码为 JPEG，降低base64的字符数
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=60)
        data = base64.b64encode(buf.getvalue()).decode()

        return f"__IMAGE__:image/jpeg:{data}"
    except ImportError:
        return "Error: Pillow not installed. Run: pip install pillow"
    except Exception as e:
        return f"Error: {e}"

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

class TodoManager:
    def __init__(self):
        self.items: list[dict] = []
    def update(self, todos: list | str) -> str:
        """
        update() 函数做四件事：
        传入一个待办列表 -> 检查是否合法 -> 存入self.items -> 将任务列表渲染出来(调用render()函数)
        """
        # 先检查传入的任务列表为 str 类型还是 list 类型
        if isinstance(todos, str):
            try:
                todos = json.loads(todos)
            except json.JSONDecodeError:
                try:
                    todos = ast.literal_eval(todos)
                except (SyntaxError, ValueError) as e:
                    raise ValueError("todos must be a list or JSON array string") from e
        if not isinstance(todos, list):
            raise ValueError("todos must be a list")
        # 检查待办项不得超过 20 项
        if len(todos) > 20:
            raise ValueError("Max 20 todos allowed")
        # 将待办列表先复制到 validated，将形式统一为 list，并统计正在执行的项目数量
        validated = []
        in_progress_count = 0
        for index, todo in enumerate(todos):
            if not isinstance(todo, dict):
                raise ValueError(f"todos[{index}] must be an object")
            # 检查每个待办项的内容与状态是否合规
            content = str(todo.get("content", "")).strip()
            status = str(todo.get("status", "waiting")).lower()
            if not content:
                raise ValueError(f"todos[{index}] requires content")
            if status not in ("waiting", "in_progress", "completed"):
                raise ValueError(f"todos[{index}] has invalid status '{status}'")
            if status == "in_progress":
                in_progress_count += 1
            validated.append({"content": content, "status": status})
        if in_progress_count > 1:
            raise ValueError("Only one todo can be in_progress at a time")
        # 将统一形式后的列表复制到 self.items
        self.items = validated
        # 渲染列表
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos."
        lines = []
        for todo in self.items:
            marker = {
                "waiting": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }[todo["status"]]
            lines.append(f"{marker} {todo['content']}")
        done = sum(todo["status"] == "completed" for todo in self.items)
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)

def run_todo_write(todos: list | str) -> str:
    try:
        output = TODO_LIST.update(todos)
    except ValueError as e:
        return f"Error: {e}"
    print(f"\n\033[33m## Current Tasks\033[0m\n{output}")
    return output

SUBAGENT_SYSTEM = (
    f"You are a coding agent at {WORKDIR}. "
    "Complete the given task, then return a concise final answer."
)
def run_subagent(task_prompt: str) -> str:
    print("\n\033[35m[Subagent started]\033[0m")
    subagent_messages = [{"role": "system","content":SUBAGENT_SYSTEM},
                         {"role": "user", "content": task_prompt}]
    for _ in range(30):
        response = client.chat.completions.create(
            model = MODEL,
            messages = subagent_messages,
            tools = SUBAGENT_TOOLS,
        )
        msg = response.choices[0].message
        subagent_messages.append(msg)
        if not msg.tool_calls:
            # 如果没有调用工具，我们应该把subagent的结果返回给主agent，而不是输出
            trigger_hook("afterLLM", short_circuit=False, messages=subagent_messages)
            return str(msg.content) if msg.content else "(no summary)"
        # 若有工具调用，需要将工具信息重新回传到模型
        image_loads = []  # 收集本轮所有图像
        for tool_call in msg.tool_calls:
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)
            """beforeToolUse事件，先判断该事件中是否有 方法 触发，如果触发，立即终止"""
            permissionBlocked = trigger_hook("beforeToolUse", short_circuit=True, tool_name=tool_name, tool_args=tool_args)
            if permissionBlocked:
                subagent_messages.append({
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
                subagent_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"[Image loaded: {suffix}, {len(data)} chars of base64]",
                })
                # 将此图片的信息加入 image_loads 中
                image_loads.append((suffix, data, tool_args.get("path", "image")))
            # 模型调用了别的工具
            else:
                subagent_messages.append({
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
            subagent_messages.append({"role": "user", "content": content})
    print("\033[35m[Subagent stopped]\033[0m")
    return "Subagent stopped after 30 turns without a final answer."

SKILLS_DIR = WORKDIR / "skills"
class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills: dict[str, dict[str, str]] = {}
        self.scan()
        
    @staticmethod
    def parse_frontmatter(text: str) -> tuple[dict, str]:
        """
        parse_frontmatter()函数解析 YAML 头部
        SKILL.md 的格式是这样的：
            ---
            name: code-review
            description: Review code for bugs and style issues.
            ---

            # Code Review

            Step 1: Read the file...
            Step 2: ...

        --- 之间的部分是 frontmatter（YAML 元数据），后面的部分是 body（正文）。这个函数把两部分拆开。
        """
        lines = text.splitlines(keepends=True) # splitlines(keepends=True) 按行切分，保留换行符
        if not lines or lines[0].rstrip("\r\n") != "---":
            return {}, text # 如果第一行不是 ---，说明这个文件没有 frontmatter，直接返回空字典 + 原文
        # 从第 2 行开始，找下一个 ---
        closing_index = next(
            (index for index, line in enumerate(lines[1:], start=1)
             if line.rstrip("\r\n") == "---"),
            None,
        )
        if closing_index is None:
            return {}, text # 如果找不到，直接返回空字典 + 原文
        # 切分
        frontmatter = "".join(lines[1:closing_index]) # lines[1:closing_index]：从第 1 行到结束标记前一行，这就是 YAML 内容
        body = "".join(lines[closing_index + 1:]).strip() # 结束标记之后的所有行，就是 body，.strip()去掉首尾空白
        try:
            metadata = yaml.safe_load(frontmatter) or {} # yaml.safe_load 把 YAML 字符串转成 Python dict
        except yaml.YAMLError: # YAML 语法错误 → 捕获异常，返回空 dict
            metadata = {} 
        if not isinstance(metadata, dict): # YAML 解析出非 dict（比如整个 frontmatter 是一个字符串）→ 也返回空 dict
            metadata = {}
        return metadata, body

    def scan(self):
        """
        遍历目录，建立索引
        """
        """重置状态"""
        self.skills.clear() # 先将skills列表清空，这样做在每次scan()时可以实时更新skill列表
        if not self.skills_dir.exists(): # 如果目录不存在，直接返回，不报错
            return
        """找到所有SKILL.md"""
        skills_root = self.skills_dir.resolve()
        for manifest in sorted(self.skills_dir.glob("*/SKILL.md")):
            # glob("*/SKILL.md") 匹配 skills/code-review/SKILL.md 这种两级结构。sorted 让顺序稳定
            # manifest.is_file()：确保是文件，不是目录或符号链接指向的目录
            # manifest.resolve().is_relative_to(skills_root)：解析后必须在 skills_root 内，防止符号链接逃逸
            if (not manifest.is_file()
                    or not manifest.resolve().is_relative_to(skills_root)):
                continue
            """读取和解析"""
            content = manifest.read_text(encoding="utf-8")
            metadata, body = self.parse_frontmatter(content)
            """
            确定name
            优先用 YAML 里的 name。如果不是字符串（比如缺失或类型不对），
            回退到目录名：skills/code-review/SKILL.md → code-review
            """
            raw_name = metadata.get("name")
            name = raw_name.strip() if isinstance(raw_name, str) else ""
            name = name or manifest.parent.name
            """
            确定description
            优先用 YAML 的 description
            没有就用 body 的第一行（通常是 # Code Review 这种标题）
            规范化：去掉开头的 # 和空白，把所有连续空白折叠成一个空格
            """
            raw_description = metadata.get("description")
            description = (raw_description.strip()
                           if isinstance(raw_description, str) else "")
            description = description or body.split("\n", 1)[0]
            description = " ".join(str(description).lstrip("# ").split())
            # str(description)：确保是字符串
            # .lstrip("# ")：去掉开头的 # 和空格
            # .split()：按任意空白切分，自动去掉空字符串
            # " ".join(...)：用单个空格重新拼接
            # 结果：" # Code Review " → "Code Review"。这个描述会出现在 system prompt 的目录里，必须是单行、干净的。
            """存入字典"""
            self.skills[name] = {
                "name": name,
                "description": description,
                "content": content,
            }
    def catalog(self) -> str:
        """
        生成SYSTEM_PROMPT里的目录
        输出：
        - code-review: Review code for bugs and style issues.
        - pdf: Extract text from PDF files.
        """
        if not self.skills:
            return "(no skills found)"
        return "\n".join(
            f"- {skill['name']}: {skill['description']}"
            for skill in self.skills.values()
        )
    def load(self, name: str) -> str:
        """
        按需返回完整内容\n
        模型调用 load_skill("code-review") 时，返回完整 SKILL.md 原文。如果名字不存在，返回错误信息并列出所有可用 skill，让模型自己纠正。\n
        ", ".join(self.skills) 遍历 dict 时迭代的是 key，所以这里生成的是所有 skill 名的列表。
        """
        skill = self.skills.get(name)
        if skill:
            return skill["content"]
        available = ", ".join(self.skills) or "none"
        return f"Error: Unknown skill '{name}'. Available: {available}"
SKILL_LOADER = SkillLoader(SKILLS_DIR)
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
    {
        "type" : "function",
        "function" : {
            "name" : "todo_write",
            "description" : "Create and manage a task list for your current coding session. Use this to plan multi-step tasks and track progress.",
            "parameters" : {
                "type" : "object",
                "properties" : {
                    "todos" : {
                        "type" : "array",
                        "maxItems" : 20,
                        "items" : {
                            "type" : "object",
                            "properties" : {
                                "content" : {"type":"string", "minLength":1},
                                "status" : {"type":"string", "enum":["waiting", "in_progress", "completed"]},
                            },
                            "required" : ["content", "status"]
                        },
                    },
                },
                "required" : ["todos"],
            },
        },
    },
    {
        "type" : "function",
        "function" : {
            "name" : "load_skill",
            "description" : "Load the full SKILL.md content by skill name.",
            "parameters" : {
                "type" : "object",
                "properties" : {
                    "name" : {"type" : "string"},
                },
                "required" : ["name"],
            },
        },
    },
    {
        "type" : "function",
        "function" : {
            "name" : "compact",
            "description" : (
                "Compact the conversation so far into a concise summary, "
                "dropping early exploration details and tool chatter. "
                "Call this after finishing a phase of work, or when the "
                "user asks to summarize / start fresh. The current "
                "conversation will be replaced by [system, summary]."
            ),
            "parameters" : {
                "type" : "object",
                "properties" : {
                    "reason" : {
                        "type" : "string",
                        "description" : "Why you want to compact right now."
                    }
                },
                "required" : []
            },
        },
    },
]
TOOL_HANDLERS = {
    "bash" : run_bash,
    "read_file" : run_read,
    "write_file" : run_write,
    "edit_file" : run_edit,
    "glob_file" : run_glob,
    "read_image" : run_read_image,
    "todo_write" : run_todo_write,
    "subagent": run_subagent,
    "load_skill": SKILL_LOADER.load
}
"""SubAgent不应该有todo_write, subagent和compact工具"""
SUBAGENT_TOOLS = [t for t in TOOLS
                  if t["function"]["name"] not in ("todo_write", "compact")]
# 定义SUBAGENT工具
SUBAGENT = {
    "type": "function",
    "function": {
        "name": "subagent",
        "description": (
            "Run a subagent with a fresh conversation context. "
            "Use this for focused exploration or a self-contained subtask "
            "that would otherwise clutter the main conversation. "
            "The subagent has its own tools and returns only its final text answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_prompt": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The task for the subagent. Be specific: state the goal, the files involved, and what the final answer should contain."
                }
            },
            "required": ["task_prompt"]
        }
    }
}
# 在主Agent的工具列表中加入SUBAGENT工具
TOOLS.append(SUBAGENT)

def build_system_prompt() -> str:
    return (
        f"You are a coding agent at {WORKDIR}. "
        "BEFORE calling any other tool, you MUST call todo_write to list the steps "
        "for any task that needs more than one action. "
        "Update the status as you complete each step. "
        "All destructive operations require user approval. "
        "If a tool call is denied, explain what you were trying to do "
        "and ask the user how to proceed. "
        "If you finish a phase and want to drop early exploration details, "
        "you may call `compact` to summarize the conversation. "
        "Do NOT call `compact` unless you are sure the early history "
        "is no longer needed.\n\n"
        f"Skills available:\n{SKILL_LOADER.catalog()}\n\n"
        "Use load_skill to read the full instructions when a skill applies."
    )
SYSTEM = build_system_prompt()
TODO_LIST = TodoManager()
"""
==================================
s08 new content: context compact
==================================
"""
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"
"""
ContextCompactor 骨架 + 消息类型识别
"""
class ContextCompactor:
    CONTEXT_CHAR_LIMIT = 50000
    TOOL_RESULT_BATCH_CHAR_LIMIT = 200000 # 最近一批工具调用结果的最大字符数 200000
    LARGE_RESULT_CHAR_LIMIT = 30000 # 单个"role":"tool"结果的最大字符数 30000
    SUMMARY_INPUT_CHAR_LIMIT = 80000
    KEEP_RECENT_RESULTS = 3
    KEEP_RECENT_MESSAGES = 5
    MAX_REACTIVE_RETRIES = 1

    def __init__(self, llm_client, model, transcript_dir, tool_results_dir):
        self.client = llm_client
        self.model = model
        self.transcript_dir = transcript_dir
        self.tool_results_dir = tool_results_dir
    @staticmethod
    def estimate_chars(messages: list) -> int:
        """估算整个对话窗口messages = []的总字符数"""
        # 用 json.dumps 序列化整个 messages，取长度
        # 关键点：default=str 处理 SDK 对象；ensure_ascii=False 让中文按 1 字符算
        return len(json.dumps(messages, default=str, ensure_ascii=False))
    @staticmethod
    def get_role(message) -> str | None:
        """返回msg的role，比如"user","tool","assistant"等"""
        # 兼容 dict 和 SDK 对象，返回 message 的 role
        if isinstance(message, dict):
            return message.get("role")
        return getattr(message, "role", None)
    @staticmethod
    def has_tool_calls(message) -> bool:
        """判断msg的"role" = "assistant"，且作为字典/SDK时是否包含"tool_calls"这个key"""
        # 判断是否是 assistant 消息，且含 tool_calls
        # dict：message.get("role") == "assistant" and message.get("tool_calls")
        # SDK：message.role == "assistant" and getattr(message, "tool_calls", None)
        if message is None:
            return False
        if isinstance(message, dict):
            if message.get("role") == "assistant" and bool(message.get("tool_calls")):
                return True
        else:
            if message.role == "assistant" and bool(getattr(message, "tool_calls", None)):
                return True
        return False
    @staticmethod
    def is_tool_result(message) -> bool:
        """判断msg的"role" = "tool" """
        # 判断是否是 role == "tool" 的消息
        # OpenAI 协议里，工具结果就是 role="tool" 的独立消息
        return ContextCompactor.get_role(message) == "tool"
    """
    工具持久化（磁盘读写）
    """
    def save_output(self, tool_use_id: str, output: str) -> Path:
        """把太长的工具输出写到.task_outputs/tool-results/tool_id.txt，并返回这个路径"""
        self.tool_results_dir.mkdir(parents=True, exist_ok=True)
        # 清洗 tool_use_id，只保留 [A-Za-z0-9._-]
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", tool_use_id or "")
        # 截断到 120 字符，空的话用 "unknown"
        safe_id = safe_id[:120]
        if not safe_id:
            safe_id = "unknown"
        # 写到 self.tool_results_dir / f"{safe_id}.txt"
        path = self.tool_results_dir / f"{safe_id}.txt"
        path.write_text(output, encoding="utf-8")
        # 返回 Path
        return path
    def persisted_preview(self, tool_use_id: str, output: str, preview_chars: int = 2000) -> str:
        """生成“路径 + 预览”字符串"""
        # 先把 output 写到磁盘（调用 save_output）
        path = self.save_output(tool_use_id=tool_use_id, output=output)
        # 返回一个格式化字符串：
        # <persisted-output>
        # Full output: <path>
        # Preview:
        # <前 preview_chars 个字符>
        # </persisted-output>
        return f"<persisted-output>\nFull output: {path}\nPreview:\n{output[:preview_chars]}\n</persisted-output>"
    def persist_large_output(self, tool_use_id: str, output: str) -> str:
        """
        判断当前工具输出内容的大小，决定是否持久化\n
        LARGE_RESULT_CHAR_LIMIT = 30000，是类的常量。\n
        如果没超限，内容不变；如果超限了，这个方法的返回值会替换掉原来的工具结果内容，替换成预览。\n
        """
        # 如果 len(output) <= self.LARGE_RESULT_CHAR_LIMIT，原样返回
        if len(output) <= self.LARGE_RESULT_CHAR_LIMIT:
            return output
        # 否则返回 persisted_preview(...) 的结果
        return self.persisted_preview(tool_use_id=tool_use_id, output=output, preview_chars= 2000)
    """
    tool_result_budget — 批量检查最后一批工具结果
    (每轮都做，首要维护的是整个上下文窗口的字符数，按长度降序遍历tool的content)
    (如果单个tool的content超限，就存储并引用它的预览)
    (一旦整个上下文窗口不超限，立即退出)
    """
    @staticmethod
    def get_content(message) -> str:
        """辅助获取"content"里的内容"""
        if isinstance(message, dict):
            return str(message.get("content", ""))
        return str(getattr(message, "content", "") or "")

    @staticmethod
    def set_content(message, new_content: str) -> None:
        """辅助替换"content"里的内容"""
        if isinstance(message, dict):
            message["content"] = new_content
        else:
            message.content = new_content  # 注意：SDK 对象可能不允许这样改
    @staticmethod
    def get_tool_call_id(message) -> str:
        """辅助获取tool_call_id"""
        if isinstance(message, dict):
            return str(message.get("tool_call_id", "unknown"))
        return str(getattr(message, "tool_call_id", "unknown"))
    def last_tool_batch_range(self, messages: list) -> tuple[int, int]:
        """找到最后一批 tool 消息的索引范围"""
        # 返回 [start, end)，end 是开区间
        # 从末尾往前扫，连续的 tool 消息就是最后一批
        # 如果这批 tool 消息前面不是 assistant(tool_calls)，说明是孤儿，返回 (len, len) 空范围
        end = len(messages)
        cursor = end - 1
        # 从末尾往前扫，跳过所有 tool 消息
        while cursor >= 0 and self.is_tool_result(messages[cursor]):
            cursor -= 1
        # cursor 停在最后一个非 tool 消息上，tool 批次从 cursor + 1 开始
        batch_start = cursor + 1
        # 没有 tool 消息
        if batch_start >= end:
            return (end, end)
        # 这批 tool 消息前面不是 assistant(tool_calls)，视为孤儿
        if cursor < 0 or not self.has_tool_calls(messages[cursor]):
            return (end, end)
        return (batch_start, end)
    def tool_result_budget(self, messages: list, max_chars: int | None = None) -> list:
        """处理最后一批工具结果的主逻辑"""
        start, end = self.last_tool_batch_range(messages)
        if start >= end:
            return messages
        # 最近一批工具调用结果的最大字符限制
        limit = max_chars if max_chars is not None else self.TOOL_RESULT_BATCH_CHAR_LIMIT
        # 收集这批 tool 消息
        batch = messages[start:end]
        # 计算总字符数（只统计 content 长度）
        def total_chars():
            return sum(len(str(self.get_content(m))) for m in batch)
        # 如果未超限，就返回上下文窗口 messages = []
        if total_chars() <= limit:
            return messages
        # 否则超限
        # 按 content 长度降序，逐个处理超过 LARGE_RESULT_CHAR_LIMIT 的
        for message in sorted(batch, key=lambda m: len(str(self.get_content(m))), reverse=True):
            if total_chars() <= limit: # 处理过程中，一旦总字符数未超限，马上退出，尽可能保留工具调用结果
                break
            content = str(self.get_content(message))
            if len(content) <= self.LARGE_RESULT_CHAR_LIMIT:
                continue
            # 如果单个tool的内容超限了，就将结果存入磁盘，并在messages中替换为预览
            new_content = self.persist_large_output(
                self.get_tool_call_id(message), content
            )
            self.set_content(message, new_content)
        return messages
    """
    snip_compact —— 归档旧消息
    当消息数超过 max_messages 时，把中间一段旧消息存档到 .transcripts/，用一条标记消息替代。
    (每轮都做)
    """
    def _extract_tool_call(self, tc):
        fn = getattr(tc, "function", None)
        return {
            "id": str(getattr(tc, "id", "")) or None,
            "type": "function",
            "function": {
                "name": str(getattr(fn, "name", "")) or None,
                "arguments": str(getattr(fn, "arguments", "")) or None,
            },
        }
    def normalize_message(self, message):
        """把 SDK 对象转成可 JSON 序列化的 dict"""
        if isinstance(message, dict):
            return message
        # openai SDK 的 ChatCompletionMessage 有 model_dump()
        if hasattr(message, "model_dump"):
            # model_dump(exclude_none=True) 会删掉值为 None 的字段。
            # 这对 OpenAI 消息是合适的——tool_calls=None 和“字段不存在”在 API 语义上等价
            try:
                dumped = message.model_dump(exclude_none=True)
            except Exception:
                dumped = None
            if isinstance(dumped, dict):
                return dumped
        # 兜底：把属性提取出来，所有字段强制转基本类型，防止 MagicMock 混进来
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        tool_calls = getattr(message, "tool_calls", None) or []
        return {
            "role": str(role) if role is not None else None,
            "content": str(content) if content is not None else None,
            "tool_calls": [self._extract_tool_call(tc) for tc in tool_calls] or None,
        }
    def write_transcript(self, messages: list) -> Path:
        """把完整历史写到 JSONL"""
        # 创建 self.transcript_dir
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        # 文件名：transcript_<uuid4().hex>.jsonl
        path = self.transcript_dir / f"transcript_{uuid.uuid4().hex}.jsonl"
        # 每行一条消息，json.dumps(message, default=str, ensure_ascii=False)
        # 用 path.open("x", encoding="utf-8") 独占创建，避免覆盖
        # 返回 Path
        with path.open("x", encoding="utf-8") as f:
            for message in messages:
                normalized = self.normalize_message(message) # 将message标准化为dict
                line = json.dumps(normalized, ensure_ascii=False) # 将message转为str，ensure_ascii=False保持中文文本
                f.write(line + "\n") # 每个message写入后换行
        return path
    def is_archive_marker(self, message) -> bool:
        """识别之前生成的标记消息，标记消息的格式是：[N messages archived at <path>]"""
        # 只处理 dict 类型、content 是字符串的消息
        if not isinstance(message, dict):
            return False
        content = message.get("content")
        if not isinstance(content, str):
            return False
        # 用 re.fullmatch 匹配 r"\[\d+ messages archived at (.+)\]"
        m = re.fullmatch(r"\[\d+ messages archived at (.+)\]", content)
        if not m:
            return False
        # 如果匹配，检查路径 1) 在 transcript_dir 内； 2) 是真实文件
        path = Path(m.group(1))
        try:
            if not path.resolve().is_relative_to(self.transcript_dir.resolve()):
                return False
        except (OSError, ValueError):
            return False
        if not path.is_file():
            return False
        # 都满足才返回 True
        return True
    def snip_compact(self, messages: list, max_messages: int = 50) -> list:
        """主逻辑：消息数超过 50 时，保留头 3 条 + 尾 46 条 + 中间 1 条标记，总数正好 50"""
        if len(messages) <= max_messages: # 最大消息数合法，直接返回上下文窗口messages = []
            return messages
        head_end = 3 # 保留前三条
        tail_start = len(messages) - (max_messages - head_end - 1) # 保留尾部消息的起始点
        # 保护头部 tool 对：如果 head_end-1 是 assistant(tool_calls)，
        # 就往后跳过紧跟的 tool 消息
        if self.has_tool_calls(messages[head_end - 1]):
            while head_end < tail_start and self.is_tool_result(messages[head_end]):
                head_end += 1
        # 保护尾部 tool 对：如果 tail_start 是 tool 消息，
        # 且它前面是 assistant(tool_calls)，就把 tail_start 往前挪一位
        if (tail_start > 0 and self.is_tool_result(messages[tail_start])
                and self.has_tool_calls(messages[tail_start - 1])):
            tail_start -= 1
        # 保护性挪动可能导致 head_end 追上 tail_start（消息太少，没法切）。直接返回。
        if head_end >= tail_start:
            return messages
        # 幂等性保护：如果中间已经只剩一条 archive marker（上次切过），再切一遍会得到 [head, 新marker, tail]，
        # 其中“tail”包含了上次的 marker——结果雪上加霜。所以检测到就跳过。
        # 保证写盘的永远是完整历史，不是“中间那段”，不存在历史再被写为历史。这样 recovery 路径指向的是完整数据
        middle = messages[head_end:tail_start]
        if len(middle) == 1 and self.is_archive_marker(middle[0]):
            return messages
        # 写 transcript，生成 marker，返回新列表
        transcript_path = self.write_transcript(messages)
        marker = {"role": "user",
                "content": f"[{tail_start - head_end} messages archived at {transcript_path}]"}
        return [*messages[:head_end], marker, *messages[tail_start:]]
    """
    micro_compact — 缩短已消费的旧工具结果
    把模型已经看过的旧工具结果缩短，只保留磁盘路径引用。保留最近 KEEP_RECENT_RESULTS 个，不动刚追加的、模型还没消费的结果。
    (每轮都做)
    """
    def unseen_tool_result_indices(self, messages: list) -> set[int]:
        """
        返回模型还没消费过的 tool 消息的索引集合。
        定义：出现在最后一条 assistant 消息之后的 tool 消息，都属于未消费。
        因为模型刚请求工具、还没对结果做出回应。
        """
        last_assistant = -1
        for index in range(len(messages) - 1, -1, -1):
            if(self.has_tool_calls(messages[index])):
                last_assistant = index
                break;
        return{
            index for index in range(last_assistant + 1, len(messages))
            if self.is_tool_result(messages[index])
        }
    def persisted_output_path(self, output: str) -> str | None:
        """
        从持久化字符串中提取文件路径。
        支持两种格式：
        1. <persisted-output>\nFull output: <path>\n...   （由 persisted_preview 生成）
        2. [Earlier tool result saved at <path>]           （由 micro_compact 生成）
        返回的路径必须：
            1) 在 tool_results_dir 内 2) 是真实文件。否则返回 None。
        """
        candidate = None
        if output.startswith("<persisted-output>\n"):
            for line in output.splitlines():
                if line.startswith("Full output: "):
                    candidate = line.removeprefix("Full output: ")
                    break
        elif output.startswith("[Earlier tool result saved at ") and output.endswith("]"):
            candidate = output.removeprefix("[Earlier tool result saved at ").removesuffix("]")
        if not candidate:
            return None
        path = Path(candidate)
        try:
            if not path.resolve().is_relative_to(self.tool_results_dir.resolve()):
                return None
        except (OSError, ValueError):
            return None
        if not path.is_file():
            return None
        return str(path)
    def micro_compact(self, messages: list, target_chars: int | None = None) -> list:
        """
        已消费的旧工具结果中，只保留最近 KEEP_RECENT_RESULTS 个不动，其余全部缩短
        参数 target_chars：如果给了，在整个上下文窗口字符数降到目标以下时提前停止。
        不传则处理所有符合条件的结果。
        """
        # 1. 收集所有 tool 消息的索引
        tool_indices = [
            i for i, m in enumerate(messages) if self.is_tool_result(m)
        ]
        # 2. 排除未消费的
        unseen = self.unseen_tool_result_indices(messages)
        consumed = [i for i in tool_indices if i not in unseen]
        # 3. 保留最近 KEEP_RECENT_RESULTS 个不动
        candidates = consumed[:-self.KEEP_RECENT_RESULTS] if len(consumed) > self.KEEP_RECENT_RESULTS else []
        # 4. 逐个缩短
        for index in candidates:
            if target_chars is not None and self.estimate_chars(messages) <= target_chars:
                break
            message = messages[index]
            content = self.get_content(message)
            if len(content) <= 120:
                continue
            # 已经缩短过的（以 "[Earlier tool result saved at" 开头）不重复处理
            if content.startswith("[Earlier tool result saved at "):
                continue
            # 如果已经被持久化为预览（<persisted-output>），复用其路径
            saved_path = self.persisted_output_path(content)
            if not saved_path:
                saved_path = str(self.save_output(self.get_tool_call_id(message), content))
            self.set_content(message, f"[Earlier tool result saved at {saved_path}]")
        return messages
    """
    compact_history — 调模型摘要整个历史
    当前三步都不够时，把整段历史发给模型，让它生成一段结构化摘要，替换原来的 messages。
    这是唯一有损的步骤，也是唯一需要 API 调用的步骤。
    """
    SUMMARIZE_SYSTEM = (
        "You summarize a coding-agent conversation for context compaction. "
        "Given the full conversation as JSON, produce a structured summary:\n"
        "1. Original user goal(s)\n"
        "2. What has been done so far (with concrete file paths, commands, findings)\n"
        "3. Current state of the workspace\n"
        "4. Pending work / open questions\n"
        "5. Images already loaded: list every '[Image was loaded earlier: ...]' "
        "marker you see, with its path. The model does NOT need to reload these.\n"
        "Be concise and factual. Do not call tools. Do not ask questions."
    )
    def _strip_images(self, messages: list) -> list:
        """
        把带 image_url 的 user 消息替换成纯文本占位符。\n
        保留图片路径信息，丢掉 base64 内容。\n
        找到所有 content 是 list、且里面有 image_url 片段的消息\n
        把文字部分（type: "text"）抠出来拼成一句 \n
        把整条消息替换成 {"role": "user", "content": "[Image was loaded earlier: Here is the image: test.jpg]"}
        """
        out = []
        for m in messages:
            # 只处理 dict 类型 + content 是 list 的消息
            content = m.get("content") if isinstance(m, dict) else None
            if isinstance(content, list) and any(
                isinstance(part, dict) and part.get("type") == "image_url"
                for part in content
            ):
                # 抠出所有 text 部分，拼成一个占位符
                texts = [
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                label = " / ".join(t for t in texts if t) or "image"
                out.append({
                    "role": m.get("role", "user"),
                    "content": f"[Image was loaded earlier: {label}]"
                })
            else:
                out.append(m)
        return out
    def summary_input(self, messages: list) -> str:
        """构造摘要用的文本，超长时截头尾"""
        # 把 messages 序列化成 JSON 字符串
        # 用 normalize_message 处理每条消息，确保 SDK 对象可序列化
        normalized_messages = json.dumps([self.normalize_message(m) for m in messages], ensure_ascii=False)
        # 如果总长 <= SUMMARY_INPUT_CHAR_LIMIT，直接返回
        if len(normalized_messages) <= self.SUMMARY_INPUT_CHAR_LIMIT:
            return normalized_messages
        # 否则：取头 1/4 + 一条“中间省略”标记 + 尾 3/4
        else:
            head = self.SUMMARY_INPUT_CHAR_LIMIT // 4
            tail = self.SUMMARY_INPUT_CHAR_LIMIT - head
            return normalized_messages[:head] + "\n...[middle omitted]...\n" + normalized_messages[-tail:]
    def summarize_history(self, messages: list) -> str:
        """调用 LLM 生成摘要"""
        # 用 OpenAI 协议调用 client.chat.completions.create
        # messages 第一条是 system prompt，用于约束摘要行为
        # messages 第二条是 user 消息，内容是 summary_input(messages)
        # 取 response.choices[0].message.content
        # 空的话返回 "(empty summary)"
        if not messages:
            return "(empty summary)"
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.SUMMARIZE_SYSTEM},   # 上面的指令
                {"role": "user", "content": self.summary_input(messages)},
            ],
            max_tokens=2000,
        )
        summary = response.choices[0].message.content or ""
        return summary.strip() or "(empty summary)"
    @staticmethod
    def summary_message(label: str, request: str, summary: str, transcript: Path) -> dict:
        """构造压缩后的消息"""
        # 返回一条 dict 消息：
        # {
        #   "role": "user",
        #   "content": f"[{label}]\n\nCurrent user request:\n{request}\n\n"
        #              f"Conversation summary (reference only):\n{summary}\n\n"
        #              f"Full transcript: {transcript}"
        # }
        return {
            "role": "user",
            "content": f"[{label}]\n\nCurrent user request:\n{request}\n\n"
                        f"Conversation summary (reference only):\n{summary}\n\n"
                        f"Full transcript: {transcript}"
        }
    def compact_history(self, messages: list, active_request: str) -> list:
        """摘要历史主逻辑"""
        # 1. 写 transcript（保留完整备份）
        transcript = self.write_transcript(messages)
        print(f"[transcript saved: {transcript}]")
        # 2. 图片降级：把 base64 抠掉，只留路径占位符
        stripped = self._strip_images(messages)
        # 3. 调模型生成摘要（用降级版，避免把 12 万字符的 base64 塞给 LLM）
        summary = self.summarize_history(stripped)
        # 4. 返回一条压缩消息
        compacted = self.summary_message("Compacted", active_request, summary, transcript)
        # 保留所有 system message（通常只有 messages[0] 一条）
        system_msgs = [m for m in messages if self.get_role(m) == "system"]
        return [*system_msgs, compacted]
    def reactive_compact(self, messages: list, active_request: str) -> list:
        """
        API 返回 prompt_too_long 时的补救：
        保存 transcript，摘要较早期的历史，但保留最近 KEEP_RECENT_MESSAGES 条消息。
        注意：reactive_compact 不保留 system message
        """
        transcript = self.write_transcript(messages)
        print(f"\033[33m[transcript saved: {transcript}]\033[0m")
        # 找到"最近 KEEP_RECENT_MESSAGES 条"的起点
        tail_start = max(0, len(messages) - self.KEEP_RECENT_MESSAGES)
        # 保护 tool 对：如果 tail_start 落在 tool 结果上，
        # 且它前面是 assistant(tool_calls)，就把 tail_start 往前挪一位
        if (tail_start > 0
                and self.is_tool_result(messages[tail_start])
                and self.has_tool_calls(messages[tail_start - 1])):
            tail_start -= 1
        old_history = messages[:tail_start] if tail_start else messages
        # 只有 old_history 非空才值得摘要
        if old_history:
            stripped = self._strip_images(old_history)         # ← 将图片降级
            summary = self.summarize_history(stripped)         # ← 再生成摘要
            message = self.summary_message(
                "Reactive compact", active_request, summary, transcript
            )
            return [message, *messages[tail_start:]] if tail_start else [message]
        else:
            # 消息本来就很少，摘要也没用，原样返回
            return messages

COMPACTOR = ContextCompactor(client, MODEL, TRANSCRIPT_DIR, TOOL_RESULTS_DIR)
def process_context(messages: list, active_request: str) -> None:
    """
    每轮 LLM 调用前，检查并压缩上下文。
    """
    # 提前算好 batch 信息，避免 snip 之后索引失效
    before = ContextCompactor.estimate_chars(messages)
    start, end = COMPACTOR.last_tool_batch_range(messages)
    batch_msgs = messages[start:end]
    batch_chars = sum(len(str(COMPACTOR.get_content(m))) for m in batch_msgs)
    # 处理最后一批工具结果（超长 -> 存盘 + 预览替换）
    COMPACTOR.tool_result_budget(messages)
    mid = ContextCompactor.estimate_chars(messages)
    # 缩短已消费的旧工具结果（保留最近 KEEP_RECENT_RESULTS 条）
    COMPACTOR.micro_compact(messages, target_chars=ContextCompactor.CONTEXT_CHAR_LIMIT)
    # 处理消息数量是否超额
    messages = COMPACTOR.snip_compact(messages)
    after = ContextCompactor.estimate_chars(messages)
    if batch_msgs:
        print(f"\033[90m[context] last batch: {len(batch_msgs)} msgs, "
              f"{batch_chars} chars\033[0m")
    if mid < before:
        print(f"\033[90m[context] tool_result_budget: {before} -> {mid} chars\033[0m")
    if after < mid:
        print(f"\033[90m[context] micro_compact+snip: {mid} -> {after} chars\033[0m")
    # 最后一道防线：前三步都救不回来 → 调模型摘要
    if ContextCompactor.estimate_chars(messages) > ContextCompactor.CONTEXT_CHAR_LIMIT:
        before_h = ContextCompactor.estimate_chars(messages)
        print(f"\033[33m[context] compact_history triggered: "
              f"{before_h} > {ContextCompactor.CONTEXT_CHAR_LIMIT}\033[0m")
        messages = COMPACTOR.compact_history(messages, active_request=active_request)
        after_h = ContextCompactor.estimate_chars(messages)
        print(f"\033[33m[context] compact_history: {before_h} -> {after_h} chars\033[0m")
    return messages

def Register():
    register_hook("beforeLLM", getUserPrompt)

    register_hook("beforeToolUse", log_before_tool_use)
    register_hook("beforeToolUse", check_permission)

    register_hook("afterToolUse", large_output)

    register_hook("afterLLM", summary)

def testContextCompactor():
    from types import SimpleNamespace

    # 用一个假的 client/model 初始化，测试只需要静态方法
    compactor = ContextCompactor(None, None, None, None)

    # --- 1. get_role ---
    assert compactor.get_role({"role": "user"}) == "user"
    assert compactor.get_role(SimpleNamespace(role="assistant")) == "assistant"
    assert compactor.get_role({"content": "no role"}) is None
    print("[PASS] get_role")

    # --- 2. has_tool_calls ---
    # dict 形式，带 tool_calls
    assert compactor.has_tool_calls({
        "role": "assistant",
        "tool_calls": [{"id": "x", "function": {"name": "bash", "arguments": "{}"}}]
    }) is True
    # SDK 对象形式，带 tool_calls
    assert compactor.has_tool_calls(SimpleNamespace(
        role="assistant",
        tool_calls=[SimpleNamespace(id="x")]
    )) is True
    # assistant 但没 tool_calls
    assert compactor.has_tool_calls({"role": "assistant", "content": "hi"}) is False
    # user 消息
    assert compactor.has_tool_calls({"role": "user", "content": "hi"}) is False
    # None 消息
    assert compactor.has_tool_calls(None) is False
    # 空列表（边界）
    assert compactor.has_tool_calls({"role": "assistant", "tool_calls": []}) is False
    print("[PASS] has_tool_calls")

    # --- 3. is_tool_result ---
    assert compactor.is_tool_result({"role": "tool", "content": "x"}) is True
    assert compactor.is_tool_result(SimpleNamespace(role="tool")) is True
    assert compactor.is_tool_result({"role": "user", "content": "hi"}) is False
    assert compactor.is_tool_result({"role": "assistant", "content": "hi"}) is False
    print("[PASS] is_tool_result")

    # --- 4. estimate_chars ---
    en_msgs = [{"role": "user", "content": "hello world"}]
    zh_msgs = [{"role": "user", "content": "你好世界"}]
    mixed = [
        {"role": "system", "content": "you are a bot"},
        {"role": "user", "content": "read a file"},
        SimpleNamespace(role="assistant", content="ok"),
    ]
    print(f"  英文长度: {compactor.estimate_chars(en_msgs)}")
    print(f"  中文长度: {compactor.estimate_chars(zh_msgs)}")
    print(f"  混合长度: {compactor.estimate_chars(mixed)}")

    # 关键验证：ensure_ascii=False 生效后，中文字符按 1 个字符算
    # 如果没生效，中文会被转义成 \uXXXX，长度会膨胀约 6 倍
    zh_len = compactor.estimate_chars(zh_msgs)
    assert zh_len < 100, f"中文估算异常偏大: {zh_len}（可能 ensure_ascii 没生效）"
    print("[PASS] estimate_chars")
    print("\n所有测试通过。")

    compactor = ContextCompactor(client, MODEL, TRANSCRIPT_DIR, TOOL_RESULTS_DIR)

    # 1. 小输出不持久化
    small = "hello world"
    assert compactor.persist_large_output("id1", small) == small
    print("[PASS] small output unchanged")

    # 2. 大输出被持久化
    big = "A" * 40000
    result = compactor.persist_large_output("id2", big)
    assert result.startswith("<persisted-output>")
    assert "Full output:" in result
    assert len(result) < len(big)      # 压缩效果
    print("[PASS] large output persisted")
    print(result[:200])

    # 3. 文件真的存在
    path_line = [l for l in result.splitlines() if l.startswith("Full output:")][0]
    saved_path = Path(path_line.removeprefix("Full output: ").strip())
    assert saved_path.is_file()
    assert saved_path.read_text(encoding="utf-8") == big
    print(f"[PASS] file exists at {saved_path}")

    # 4. 文件名清洗
    weird_id = "a/b:c d\\e"
    p = compactor.save_output(weird_id, "x")
    assert "/" not in p.name and "\\" not in p.name and ":" not in p.name
    print(f"[PASS] sanitized filename: {p.name}")

     # --- 5. last_tool_batch_range ---
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        SimpleNamespace(role="assistant", tool_calls=[SimpleNamespace(id="c1")]),
        {"role": "tool", "tool_call_id": "c1", "content": "result1"},
        {"role": "tool", "tool_call_id": "c2", "content": "result2"},
        {"role": "tool", "tool_call_id": "c3", "content": "result3"},
    ]
    start, end = compactor.last_tool_batch_range(msgs)
    assert (start, end) == (3, 6), f"got ({start}, {end})"
    print("[PASS] last_tool_batch_range")

    # 孤儿 tool 消息（前面不是 assistant with tool_calls）
    orphan = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "c1", "content": "result1"},
    ]
    start, end = compactor.last_tool_batch_range(orphan)
    assert start == end, f"orphan should be empty, got ({start}, {end})"
    print("[PASS] orphan detection")

    # --- 6. tool_result_budget ---
    # 构造一批超大结果，总量超过限制
    big = "X" * 40000
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        SimpleNamespace(role="assistant", tool_calls=[
            SimpleNamespace(id=f"c{i}") for i in range(6)
        ]),
        *[{"role": "tool", "tool_call_id": f"c{i}", "content": big} for i in range(6)],
    ]
    # 6 × 40000 = 240000 字符，超过 TOOL_RESULT_BATCH_CHAR_LIMIT = 200000
    before = sum(len(str(m["content"])) for m in msgs
             if compactor.get_role(m) == "tool" and isinstance(m, dict))
    compactor.tool_result_budget(msgs, max_chars=100000)
    after = sum(len(str(m["content"])) for m in msgs
            if compactor.get_role(m) == "tool" and isinstance(m, dict))
    assert after < before, f"after={after} should be < before={before}"
    # 验证被持久化的消息长这样
    persisted = [m for m in msgs
             if compactor.get_role(m) == "tool"
             and isinstance(m, dict)
             and str(m["content"]).startswith("<persisted-output>")]
    assert persisted, "at least one message should be persisted"
    print(f"[PASS] tool_result_budget: {before} -> {after}, {len(persisted)} persisted")

    # 小批次不动
    msgs_small = [
        {"role": "user", "content": "hi"},
        SimpleNamespace(role="assistant", tool_calls=[SimpleNamespace(id="c1")]),
        {"role": "tool", "tool_call_id": "c1", "content": "small"},
    ]
    compactor.tool_result_budget(msgs_small, max_chars=100000)
    assert msgs_small[2]["content"] == "small"
    print("[PASS] small batch unchanged")
     # --- 7. snip_compact: 消息数不够，不动 ---
    short = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
    result = compactor.snip_compact(short, max_messages=50)
    assert result is short or result == short
    print("[PASS] snip_compact: under limit unchanged")

    # --- 8. snip_compact: 超过限制，正确切割 ---
    long_msgs = [{"role": "system", "content": "sys"}]
    long_msgs.append({"role": "user", "content": "start"})
    long_msgs.append(SimpleNamespace(role="assistant", content="ok"))
    for i in range(100):
        long_msgs.append({"role": "user", "content": f"filler {i}"})
    before_len = len(long_msgs)
    result = compactor.snip_compact(long_msgs, max_messages=50)
    after_len = len(result)
    # 切完后应该 ≤ 51（50 条保留 + 1 条 marker，但 head 3 条 + tail 48 条 + marker 1 条 = 52 左右）
    assert after_len < before_len, f"len {before_len} -> {after_len}"
    assert after_len <= 52, f"after too long: {after_len}"
    # 中间应该有一条 archive marker
    markers = [m for m in result if isinstance(m, dict)
               and str(m.get("content", "")).startswith("[")
               and "messages archived at" in str(m.get("content", ""))]
    assert len(markers) == 1, f"expected 1 marker, got {len(markers)}"
    print(f"[PASS] snip_compact: {before_len} -> {after_len}, marker present")
    print(f"  marker: {markers[0]['content'][:120]}...")

    # --- 9. 重复调用不会再次归档 ---
    result2 = compactor.snip_compact(result, max_messages=50)
    assert len(result2) <= len(result), "second call should not grow"
    print("[PASS] snip_compact: idempotent")

    # --- 10. 保护 tool 对 ---
    tool_msgs = [{"role": "system", "content": "sys"}]
    tool_msgs.append({"role": "user", "content": "start"})
    tool_msgs.append(SimpleNamespace(role="assistant", content="ok"))
    # 构造一个 tool 对正好落在切割边界
    for i in range(30):
        tool_msgs.append({"role": "user", "content": f"filler {i}"})
    tool_msgs.append(SimpleNamespace(role="assistant",
                                     tool_calls=[SimpleNamespace(id="x")]))
    tool_msgs.append({"role": "tool", "tool_call_id": "x", "content": "result"})
    for i in range(20):
        tool_msgs.append({"role": "user", "content": f"more {i}"})
    result = compactor.snip_compact(tool_msgs, max_messages=50)
    # 验证没有孤立的 tool 消息（前面必须紧跟 assistant(tool_calls)）
    for i, m in enumerate(result):
        if compactor.is_tool_result(m):
            assert i > 0 and compactor.has_tool_calls(result[i - 1]), \
                f"orphan tool at index {i}"
    print("[PASS] snip_compact: tool pairs protected")
    # --- 11. unseen_tool_result_indices ---
    msgs = [
        {"role": "user", "content": "start"},
        SimpleNamespace(role="assistant", tool_calls=[SimpleNamespace(id="a")]),
        {"role": "tool", "tool_call_id": "a", "content": "old result"},
        SimpleNamespace(role="assistant", content="I saw it"),
        SimpleNamespace(role="assistant", tool_calls=[SimpleNamespace(id="b")]),
        {"role": "tool", "tool_call_id": "b", "content": "fresh result"},
    ]
    unseen = compactor.unseen_tool_result_indices(msgs)
    assert unseen == {5}, f"expected {{5}}, got {unseen}"
    print("[PASS] unseen_tool_result_indices")

    # --- 12. micro_compact: 缩短旧的，保留新的 ---
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "start"},
    ]
    # 4 个旧的 tool 对
    for i in range(4):
        msgs.append(SimpleNamespace(role="assistant",
                                     tool_calls=[SimpleNamespace(id=f"old{i}")]))
        msgs.append({"role": "tool", "tool_call_id": f"old{i}",
                     "content": "X" * 200})
    # 模型消费过的证明
    msgs.append(SimpleNamespace(role="assistant", content="seen"))
    # 1 个未消费的（最后是 assistant 请求 + tool 结果）
    msgs.append(SimpleNamespace(role="assistant",
                                 tool_calls=[SimpleNamespace(id="fresh")]))
    msgs.append({"role": "tool", "tool_call_id": "fresh",
                 "content": "Y" * 200})

    compactor.micro_compact(msgs)

    # 检查：旧的 4 个中，前 1 个被缩短（KEEP_RECENT_RESULTS=3 保留后 3 个）
    old_tool = [m for m in msgs if isinstance(m, dict)
                and m.get("role") == "tool" and m["tool_call_id"].startswith("old")]
    shortened = [m for m in old_tool
                 if m["content"].startswith("[Earlier tool result saved at ")]
    kept = [m for m in old_tool if m["content"].startswith("X")]
    fresh = [m for m in msgs if isinstance(m, dict)
             and m.get("role") == "tool" and m["tool_call_id"] == "fresh"]

    assert len(shortened) == 1, f"expected 1 shortened, got {len(shortened)}"
    assert len(kept) == 3, f"expected 3 kept, got {len(kept)}"
    assert fresh[0]["content"].startswith("Y"), "fresh result must not be shortened"
    print("[PASS] micro_compact: old shortened, recent kept, fresh untouched")

    # --- 13. micro_compact: 幂等（重复调用不重复缩短） ---
    compactor.micro_compact(msgs)
    already_shortened = [m for m in msgs if isinstance(m, dict)
                         and m.get("role") == "tool"
                         and m["content"].startswith("[Earlier tool result saved at ")]
    # 第一次跑了 1 个，第二次不应产生新的
    assert len(already_shortened) == 1, f"got {len(already_shortened)}"
    print("[PASS] micro_compact: idempotent")

    # --- 14. persisted_output_path 提取路径 ---
    compactor2 = ContextCompactor(client, MODEL, TRANSCRIPT_DIR, TOOL_RESULTS_DIR)
    big = "Z" * 40000
    preview = compactor2.persisted_preview("test-id", big)
    extracted = compactor2.persisted_output_path(preview)
    assert extracted is not None and Path(extracted).is_file()
    # 假的路径应返回 None
    assert compactor2.persisted_output_path(
        "[Earlier tool result saved at C:/Windows/System32/evil.dll]"
    ) is None
    print("[PASS] persisted_output_path")
def test_compact_history_fixed():
    compactor = ContextCompactor(client, MODEL, TRANSCRIPT_DIR, TOOL_RESULTS_DIR)
    fake = [
        {"role": "system", "content": "You are a bot."},
        {"role": "user", "content": "Fix the login bug in auth.py."},
    ] + [{"role": "user", "content": f"filler {i}: " + "X"*500} for i in range(50)]

    result = compactor.compact_history(fake, active_request="fix the login bug")

    # ① system 保留
    assert result[0]["role"] == "system"
    assert result[0]["content"] == "You are a bot."

    # ② 压缩消息紧随其后
    assert result[1]["role"] == "user"
    assert "Compacted" in result[1]["content"]

    # ③ active_request 被保留
    assert "fix the login bug" in result[1]["content"]

    # ④ transcript 路径被写进压缩消息
    assert "Full transcript:" in result[1]["content"]

    # ⑤ 整体变短
    assert compactor.estimate_chars(result) < compactor.estimate_chars(fake)

    # ⑥ 空历史不打 API
    assert compactor.summarize_history([]) == "(empty summary)"

    print("[PASS] compact_history fixed")
def test_process_context():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        SimpleNamespace(role="assistant",
                        tool_calls=[SimpleNamespace(id="c1")]),
        {"role": "tool", "tool_call_id": "c1", "content": "X" * 5000},
    ]
    print("before:", ContextCompactor.estimate_chars(msgs))
    process_context(msgs)
    print("after :", ContextCompactor.estimate_chars(msgs))
    print("tool content starts with:",
          msgs[3]["content"][:60])
    # 断言真的被替换了
    assert msgs[3]["content"].startswith("<persisted-output>"), \
        "预期被持久化为预览，但没有"
    print("[PASS] process_context persists large tool result")
def test_process_context_step2():
    from types import SimpleNamespace

    # 造 5 个工具对：前 4 个是"已消费"，最后一个"未消费"
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "start"},
    ]
    for i in range(4):
        msgs.append(SimpleNamespace(
            role="assistant",
            tool_calls=[SimpleNamespace(id=f"old{i}")]
        ))
        msgs.append({
            "role": "tool",
            "tool_call_id": f"old{i}",
            "content": "X" * 20000     # 每条 1000 字符，超过 120 阈值
        })
    # 模型已经"看过"了上面 4 条
    msgs.append(SimpleNamespace(role="assistant", content="I saw them"))

    # 最后 1 条：模型刚请求、还没消费
    msgs.append(SimpleNamespace(
        role="assistant",
        tool_calls=[SimpleNamespace(id="fresh")]
    ))
    msgs.append({
        "role": "tool",
        "tool_call_id": "fresh",
        "content": "Y" * 1000
    })

    print(f"before: {ContextCompactor.estimate_chars(msgs)}")
    process_context(msgs, active_request="")
    print(f"after : {ContextCompactor.estimate_chars(msgs)}")

    old_tools = [m for m in msgs
                 if isinstance(m, dict) and m.get("role") == "tool"
                 and m["tool_call_id"].startswith("old")]
    fresh_tool = [m for m in msgs
                  if isinstance(m, dict) and m.get("role") == "tool"
                  and m["tool_call_id"] == "fresh"][0]

    shortened = [m for m in old_tools
                 if m["content"].startswith("[Earlier tool result saved at ")]
    kept = [m for m in old_tools if m["content"].startswith("X")]

    # KEEP_RECENT_RESULTS = 3，4 条旧结果里应缩短 1 条、保留 3 条
    assert len(shortened) == 1, f"expected 1 shortened, got {len(shortened)}"
    assert len(kept) == 3, f"expected 3 kept, got {len(kept)}"
    # 未消费的那条必须原样保留
    assert fresh_tool["content"].startswith("Y"), \
        "fresh tool result must NOT be shortened"

    print("[PASS] process_context step2: micro_compact works")
def test_process_context_step3():
    # 构造 60 条消息，让 snip_compact 触发
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "start"},
    ]
    for i in range(60):
        msgs.append({"role": "user", "content": f"msg{i}"})

    before = len(msgs)
    print(f"before: {before} messages, {ContextCompactor.estimate_chars(msgs)} chars")

    msgs = process_context(msgs, active_request="")

    after = len(msgs)
    print(f"after : {after} messages, {ContextCompactor.estimate_chars(msgs)} chars")

    # 消息数明显下降
    assert after < before, f"expected fewer messages, got {before} -> {after}"

    # 头 3 条保留
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[1] == {"role": "user", "content": "start"}

    # 中间有一条 archive marker
    markers = [m for m in msgs
               if isinstance(m, dict)
               and str(m.get("content", "")).startswith("[")
               and "messages archived at" in str(m.get("content", ""))]
    assert len(markers) == 1, f"expected 1 marker, got {len(markers)}"
    print(f"[PASS] process_context step3: snip_compact works")
    print(f"  marker: {markers[0]['content'][:120]}")
def test_process_context_step4():
    """用小阈值 + 假 client 验证 compact_history 触发链路"""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    # 临时把阈值调低，让触发容易
    old_limit = ContextCompactor.CONTEXT_CHAR_LIMIT
    ContextCompactor.CONTEXT_CHAR_LIMIT = 500      # ← 500 字符就触发

    # 替换 COMPACTOR.client，避免真打 API
    old_client = COMPACTOR.client
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock()]
    fake_resp.choices[0].message.content = (
        "Summary: user asked about X. Nothing done yet."
    )
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp
    COMPACTOR.client = fake_client

    try:
        # 构造一段超限历史
        msgs = [
            {"role": "system", "content": "You are a bot."},
            {"role": "user", "content": "start"},
        ] + [{"role": "user", "content": f"filler {i}: " + "X"*200}
             for i in range(10)]

        before = ContextCompactor.estimate_chars(msgs)
        assert before > ContextCompactor.CONTEXT_CHAR_LIMIT

        msgs = process_context(msgs, active_request="fix the bug")
        after = ContextCompactor.estimate_chars(msgs)

        # 压缩后应该是 [system, summary] 两条
        assert len(msgs) == 2, f"expected 2 messages, got {len(msgs)}"
        assert msgs[0]["role"] == "system"
        assert "You are a bot." in msgs[0]["content"]
        assert msgs[1]["role"] == "user"
        assert "Compacted" in msgs[1]["content"]
        assert "fix the bug" in msgs[1]["content"]
        assert after < before

        # 假 client 确实被调过一次
        assert fake_client.chat.completions.create.call_count == 1

        print(f"[PASS] process_context step4: compact_history triggered "
              f"({before} -> {after})")
        print(f"  summary msg: {msgs[1]['content'][:150]}")
    finally:
        ContextCompactor.CONTEXT_CHAR_LIMIT = old_limit
        COMPACTOR.client = old_client
def test_reactive_compact():
    from unittest.mock import MagicMock

    # 两个 client 要分开数：主循环的、以及 summarize 用的
    main_calls = {"n": 0}
    summarize_calls = {"n": 0}

    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = "Done."
    fake_response.choices[0].message.tool_calls = None

    def fake_main_create(**kwargs):
        main_calls["n"] += 1
        if main_calls["n"] == 1:
            raise Exception("prompt_too_long: context exceeds limit")
        return fake_response

    def fake_summarize_create(**kwargs):
        summarize_calls["n"] += 1
        return fake_response

    fake_main_client = MagicMock()
    fake_main_client.chat.completions.create.side_effect = fake_main_create

    fake_summarize_client = MagicMock()
    fake_summarize_client.chat.completions.create.side_effect = fake_summarize_create

    # 关键：改 __main__ 里的这两个
    saved_main = globals()["client"]
    saved_compactor = COMPACTOR.client
    globals()["client"] = fake_main_client
    COMPACTOR.client = fake_summarize_client   # summarize_history 用的是 self.client

    try:
        messages = [
            {"role": "system", "content": "You are a bot."},
            {"role": "user", "content": "hello"},
        ] + [{"role": "user", "content": f"msg{i}"} for i in range(10)]

        messages = agent_loop(messages, active_request="hello")

        # 主 client：1 失败 + 1 成功 = 2
        assert main_calls["n"] == 2, f"main calls: {main_calls['n']}"
        # 摘要 client：1 次
        assert summarize_calls["n"] == 1, f"summary calls: {summarize_calls['n']}"

        # 消息里出现 reactive_compact 标记
        contents = " ".join(
            str(m.get("content", "") if isinstance(m, dict)
                else getattr(m, "content", ""))
            for m in messages
        )
        assert "Reactive compact" in contents, "reactive_compact marker missing"
        print("[PASS] reactive_compact: API error recovered")
    finally:
        globals()["client"] = saved_main
        COMPACTOR.client = saved_compactor
def test_compact_tool():
    from unittest.mock import MagicMock

    # 假主 client：第一轮返回带 tool_call="compact" 的 message
    # 第二轮返回正常的结束消息
    fake_compact_tool_call = MagicMock()
    fake_compact_tool_call.id = "call_compact_1"
    fake_compact_tool_call.function.name = "compact"
    fake_compact_tool_call.function.arguments = json.dumps(
        {"reason": "phase done"}
    )

    fake_msg_1 = MagicMock()
    fake_msg_1.tool_calls = [fake_compact_tool_call]
    fake_msg_1.content = None

    fake_msg_2 = MagicMock()
    fake_msg_2.tool_calls = None
    fake_msg_2.content = "Done after compact."

    resp_1 = MagicMock()
    resp_1.choices = [MagicMock()]
    resp_1.choices[0].message = fake_msg_1

    resp_2 = MagicMock()
    resp_2.choices = [MagicMock()]
    resp_2.choices[0].message = fake_msg_2

    main_calls = {"n": 0}
    def fake_main_create(**kwargs):
        main_calls["n"] += 1
        return resp_1 if main_calls["n"] == 1 else resp_2

    fake_main_client = MagicMock()
    fake_main_client.chat.completions.create.side_effect = fake_main_create

    # 假摘要 client
    fake_sum_resp = MagicMock()
    fake_sum_resp.choices = [MagicMock()]
    fake_sum_resp.choices[0].message.content = "Summary of early work."
    fake_sum_client = MagicMock()
    fake_sum_client.chat.completions.create.return_value = fake_sum_resp

    saved_main = globals()["client"]
    saved_comp = COMPACTOR.client
    globals()["client"] = fake_main_client
    COMPACTOR.client = fake_sum_client
    try:
        messages = [
            {"role": "system", "content": "You are a bot."},
            {"role": "user", "content": "do something big"},
        ] + [{"role": "user", "content": f"filler {i}"} for i in range(5)]

        messages = agent_loop(messages, active_request="do something big")

        # 主 client 调了 2 次（第 1 次返回 compact tool_call，第 2 次结束）
        assert main_calls["n"] == 2, f"main calls: {main_calls['n']}"
        # 摘要 client 调了 1 次
        assert fake_sum_client.chat.completions.create.call_count == 1

        # 结果应该是 [system, summary] 或包含 summary
        contents = " ".join(
            str(m.get("content", "") if isinstance(m, dict)
                else getattr(m, "content", ""))
            for m in messages
        )
        assert "Compacted" in contents, "compacted message missing"
        assert "Summary of early work" in contents, "summary content missing"
        print("[PASS] compact tool: model-triggered compaction works")
    finally:
        globals()["client"] = saved_main
        COMPACTOR.client = saved_comp
def test_strip_images():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "user", "content": [
            {"type": "text", "text": "Here is the image: test.jpg"},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + "A" * 100000}},
        ]},
    ]
    before = ContextCompactor.estimate_chars(msgs)
    stripped = COMPACTOR._strip_images(msgs)
    after = ContextCompactor.estimate_chars(stripped)
    print(f"before: {before}, after: {after}")
    assert after < before / 100, "should shrink by > 100x"
    # 图片消息应该变成纯文本占位符
    assert stripped[2]["content"].startswith("[Image was loaded earlier:")
    assert "test.jpg" in stripped[2]["content"]
    # 其他消息不动
    assert stripped[0] == msgs[0]
    assert stripped[1] == msgs[1]
    print("[PASS] _strip_images")

def agent_loop(messages : list, active_request: str) -> list:
    rounds_since_todo = 0 # 统计 todo 空闲的轮次
    reactive_retries = 0 
    while True:
        messages = process_context(messages, active_request=active_request)   # 调用模型前，压缩上下文
        try:                                   # 先用try尝试获取模型输出，避免报错后直接崩溃
            response = client.chat.completions.create(
                model = MODEL,
                messages = messages,
                tools = TOOLS,
            )
        except Exception as error:             # 捕获上下文窗口 + 本次输入是否超出API token限制
            message = str(error).lower()
            too_long = (
                "prompt_too_long" in message
                or "too many tokens" in message
                or "context_length_exceeded" in message   # OpenAI 系常用这个
            )
            if too_long and reactive_retries < ContextCompactor.MAX_REACTIVE_RETRIES:
                print(f"\033[33m[context] reactive_compact triggered "
                      f"(retry {reactive_retries + 1}/"
                      f"{ContextCompactor.MAX_REACTIVE_RETRIES})\033[0m")
                messages = COMPACTOR.reactive_compact(messages, active_request)
                reactive_retries += 1
                continue                       # ← 重试
            raise                              # ← 不是 too_long 或重试用完，往外抛
        msg = response.choices[0].message
        messages.append(msg)
        # 若无工具调用，直接输出最后一次结果，并返回
        if not msg.tool_calls:
            if msg.content:
                print(msg.content)
            trigger_hook("afterLLM", short_circuit=False, messages=messages)
            return messages
        # 若有工具调用，需要将工具信息重新回传到模型
        image_loads = []  # 收集本轮所有图像
        used_todo = False # 判断模型在本轮中是否做了 todo_write
        compact_requested = False # 判断本轮是否需要做上下文compact
        for tool_call in msg.tool_calls:
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)
            # compact 是特殊工具：不执行，只打标记
            if tool_name == "compact":
                # 用一个普通的 tool 消息"回应"这次调用，
                # 让 OpenAI API 不会因为"tool_call 没有对应 tool 结果"报错
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": "Compacting conversation...",
                })
                compact_requested = True
                continue
            # 其他工具照旧
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
                # 模型调用了 todo_write
                if tool_name == "todo_write":
                    used_todo = True
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
        # 如果模型主动请求压缩
        if compact_requested:
            print(f"\033[33m[context] model requested compact\033[0m")
            messages = COMPACTOR.compact_history(messages, active_request=active_request)
            # 压缩后 image_loads 引用的是旧消息，丢弃
            image_loads = []
        # 处理todo空闲的轮次，如果模型连续3轮没有给出任何计划，提醒模型做计划
        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        has_work_to_do = (not TODO_LIST.items) or any(todo["status"] != "completed" for todo in TODO_LIST.items)
        if rounds_since_todo >= 3 and has_work_to_do:
            messages.append({
                "role": "user",
                "content": "<reminder>Update your todos.</reminder>"
            })
            rounds_since_todo = 0

if __name__ == "__main__":
    # testContextCompactor()
    # test_compact_history_fixed()
    # test_process_context()
    # test_process_context_step2()
    # test_process_context_step3()
    # test_process_context_step4()
    # test_reactive_compact()
    # test_compact_tool()
    # test_strip_images()
    Register()
    print("- Agent loop started.\n- Enter your question, press Enter to send.\n- Type q to quit.\n")
    messages = [{"role" : "system", "content" : SYSTEM}]
    while True:
        query = input(f"\001\033[36m\002{DISPLAY} >> \001\033[0m\002")
        if query.strip().lower() in ("q", "exit", ""):
            break
        trigger_hook("beforeLLM", short_circuit=False)
        messages.append({"role" : "user", "content" : query})
        messages = agent_loop(messages, active_request=query)   # ← 接住
        print()
    

    