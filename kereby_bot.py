import os
import requests

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

message = "🏠 TEST! Kereby-botten virker! 🤖🔔"

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

response = requests.post(
    url,
    data={
        "chat_id": CHAT_ID,
        "text": message,
    },
)

print("Telegram svar:")
print(response.status_code)
print(response.text)

response.raise_for_status()
