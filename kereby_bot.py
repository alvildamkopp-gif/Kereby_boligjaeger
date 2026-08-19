import os
import json
import requests
import re

KEREBY_URL = "https://kereby.dk/bolig/"
STATE_FILE = "seen_boliger.json"

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def hent_boliger():
    response = requests.get(KEREBY_URL, timeout=20)
    response.raise_for_status()

    links = re.findall(r'href=["\']([^"\']+)["\']', response.text)

    boliger = set()

    for link in links:
        if link.startswith("/bolig/"):
            link = "https://kereby.dk" + link

        if link.startswith("https://kereby.dk/bolig/") and link != KEREBY_URL:
            boliger.add(link)

    return boliger


def hent_set():
    if not os.path.exists(STATE_FILE):
        return set()

    with open(STATE_FILE, "r") as f:
        return set(json.load(f))


def gem_set(boliger):
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(boliger), f, indent=2)


def send_telegram(link):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    message = (
        "🏠 NY KEREBY-BOLIG!\n\n"
        f"🔗 {link}"
    )

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message,
        },
        timeout=20,
    )

    response.raise_for_status()


print("🔎 Tjekker Kereby...")

boliger = hent_boliger()
gamle_boliger = hent_set()

print(f"Fandt {len(boliger)} boliger.")

nye_boliger = boliger - gamle_boliger

if not gamle_boliger:
    print("Første kørsel – gemmer eksisterende boliger som udgangspunkt.")
else:
    print(f"Fandt {len(nye_boliger)} nye boliger.")

    for bolig in sorted(nye_boliger):
        print("NY:", bolig)
        send_telegram(bolig)

gem_set(boliger)

print("✅ Færdig!")
