from google import genai
from config.ajustes import ajustes

client = genai.Client(api_key=ajustes.clave_api_gemini)

for m in client.models.list():
    print(m.name)