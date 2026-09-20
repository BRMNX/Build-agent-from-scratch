import ast
import os
import re
import json
import subprocess
import base64
from pathlib import Path
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
        data = base64.b64encode(file_path.read_bytes()).decode()
        return f"__IMAGE__:{suffix_map[suffix]}:{data}"
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

"""
==================================
s07 new content : skills_loading
==================================
"""

"""
启动时,scan()函数扫描整个skills/目录,把每个SKILL.md的 "名字"+"简短介绍" 加进SYSTEM_PROMPT,
当模型真正需要这个skill时,再通过load_skill()读取完整内容

每个skill形如:
{
    "code-review": {
        "name": "code-review",
        "description": "Review code for bugs and style issues.",
        "content": "---\nname: code-review\n...整个 SKILL.md 的原文..."
    },
    "pdf": {
        "name": "pdf",
        "description": "Extract text from PDF files.",
        "content": "..."
    }
}
"""
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
"""SubAgent不应该有todo_write和subagent工具"""
SUBAGENT_TOOLS = [t for t in TOOLS if t["function"]["name"] != "todo_write"]
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
        "and ask the user how to proceed.\n\n"
        f"Skills available:\n{SKILL_LOADER.catalog()}\n\n"
        "Use load_skill to read the full instructions when a skill applies."
    )
SYSTEM = build_system_prompt()
def agent_loop(messages : list):
    # 统计 todo 空闲的轮次
    rounds_since_todo = 0
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
        used_todo = False # 判断模型在本轮中是否做了 todo_write
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
        # 处理todo空闲的轮次，如果模型连续3轮没有给出任何计划，提醒模型做计划
        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        has_work_to_do = (not TODO_LIST.items) or any(todo["status"] != "completed" for todo in TODO_LIST.items)
        if rounds_since_todo >= 3 and has_work_to_do:
            messages.append({
                "role": "user",
                "content": "<reminder>Update your todos.</reminder>"
            })
            rounds_since_todo = 0

TODO_LIST = TodoManager()


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

    