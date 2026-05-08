import os
from google import genai
try:
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    for m in client.models.list():
        if "pro" in m.name.lower() or "gemini" in m.name.lower():
            print(m.name)
except Exception as e:
    print(e)
