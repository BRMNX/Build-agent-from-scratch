import os
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
response = client.chat.completions.create(
    model = MODEL,
    messages=[{"role":"user", "content" : "Say hello in one sentence."}],
)
print(response.choices[0].message.content)