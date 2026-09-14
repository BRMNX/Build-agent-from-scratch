import os
import json
import subprocess
from openai import OpenAI
from dotenv import load_dotenv, find_dotenv

if not find_dotenv():
    raise FileNotFoundError(f".env file not found")
load_dotenv()
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
    base_url=os.environ.get("OPENAI_BASE_URL"),
)
MODEL = os.environ.get("MODEL_ID")
DISPLAY = os.environ.get("DISPLAY_NAME", MODEL)
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
]

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
        for tool_call in msg.tool_calls:
            args = json.loads(tool_call.function.arguments)
            output = run_bash(args["command"])
            messages.append({
                "role" : "tool",
                "tool_call_id" : tool_call.id,
                "content" : output,
            })



if __name__ == "__main__":
    print("- Agent loop started.\n- Enter your question, press Enter to send.\n- Type q to quit.\n")
    messages = []
    while True:
        query = input(f"\001\033[36m\002{DISPLAY} >> \001\033[0m\002")
        if query.strip().lower() in ("q", "exit", ""):
            break
        messages.append({"role" : "user", "content" : query})
        agent_loop(messages=messages)
        print()

    