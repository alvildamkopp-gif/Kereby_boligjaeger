import os
import re
import sys
import json
import time
import html
import subprocess
from datetime import datetime, timezone

import requests

KEREBY_URL = "https://kereby.dk/bolig/"
STATE_FILE = "seen_boliger.json"

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Loop-opsætning (styres af workflow'et via env-variabler).
#   RUN_DURATION_SECONDS = 0  -> kør ét enkelt tjek og stop
#   RUN_DURATION_SECONDS > 0  -> bliv ved med at tjekke indtil tiden er gået
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "20"))
RUN_DURATION = int(os.environ.get("RUN_DURATION_SECONDS", "0"))

# Når =1 committer og pusher botten selv seen_boliger.json så snart der er
# en ny bolig (bruges i GitHub Actions, hvor et langt job ellers først ville
# gemme til sidst).
GIT_AUTOCOMMIT = os.environ.get("GIT_AUTOCOMMIT") == "1"

HTTP_TIMEOUT = 20
MAX_FETCH_ATTEMPTS = 4

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept-Language": "da,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
)

# Ét boligkort på oversigten. Ledige boliger er et <a> med link; reserverede
# er et <div> uden link. Vi splitter HTML'en op i kort og læser hvert for sig.
CARD_RE = re.compile(r'<article class="jorato-case-card[^"]*".*?</article>', re.S)
LINK_RE = re.compile(
    r'class="jorato-case-card__link"[^>]*href="(https://kereby\.dk/bolig/[^"#?]+)"'
)


def _now():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _attr(block, name):
    m = re.search(name + r'="([^"]*)"', block)
    return m.group(1).strip() if m else ""


def _text(block, css_class):
    m = re.search(r'class="' + re.escape(css_class) + r'"[^>]*>(.*?)<', block, re.S)
    if not m:
        return ""
    return html.unescape(m.group(1)).strip()


def fetch_html():
    """Hent oversigten med cache-busting og retry. Rejser en fejl hvis siden
    ikke ligner en gyldig boligoversigt, så vi aldrig gemmer/notificerer på
    et dårligt svar."""
    last_err = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            resp = SESSION.get(
                KEREBY_URL,
                params={"_": int(time.time() * 1000)},
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            text = resp.text
            if "jorato-case-catalog" not in text or "jorato-case-card" not in text:
                raise ValueError("uventet sideindhold (mangler boligkatalog)")
            return text
        except Exception as e:  # noqa: BLE001 - vi vil gerne prøve igen på alt
            last_err = e
            if attempt < MAX_FETCH_ATTEMPTS:
                wait = min(2 ** attempt, 15)
                print(f"  ! forsøg {attempt}/{MAX_FETCH_ATTEMPTS} fejlede: {e} "
                      f"- prøver igen om {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"kunne ikke hente {KEREBY_URL}: {last_err}")


def parse_ledige_boliger(html_text):
    """Returnér {url: info} for de boliger der er LEDIGE lige nu."""
    cards = CARD_RE.findall(html_text)
    if not cards:
        raise RuntimeError("kunne ikke finde nogen boligkort - markup ændret?")

    boliger = {}
    for block in cards:
        m = LINK_RE.search(block)
        if not m:
            continue  # reserveret/udlejet - intet link, ikke relevant
        url = m.group(1).rstrip("/") + "/"
        boliger[url] = {
            "url": url,
            "adresse": _text(block, "jorato-case-card__location-text"),
            "overskrift": _text(block, "jorato-case-card__headline"),
            "husleje": _text(block, "jorato-case-card__rent"),
            "vaerelser": _attr(block, "data-rooms"),
            "stoerrelse": _attr(block, "data-size"),
        }
    return boliger


def hent_set():
    if not os.path.exists(STATE_FILE):
        return set()
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return set(data)


def gem_set(seen):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def _git(*args, check=True):
    return subprocess.run(["git", *args], check=check,
                          capture_output=True, text=True)


def commit_state(antal_nye):
    """Commit + push seen_boliger.json med det samme (kun i Actions).
    Push-workflow'et sender så en Telegram-bekræftelse på committen."""
    if not GIT_AUTOCOMMIT:
        return
    try:
        _git("add", STATE_FILE)
        if _git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return  # intet at committe
        besked = f"Opdater sete boliger (+{antal_nye} ny{'e' if antal_nye != 1 else ''})"
        _git("commit", "-m", besked)
        for forsoeg in range(3):
            if _git("push", check=False).returncode == 0:
                print(f"  📦 committet og pushet: {besked}")
                return
            print(f"  ! git push fejlede (forsøg {forsoeg + 1}) - rebaser og prøver igen")
            _git("pull", "--rebase", "--autostash", check=False)
        print("  ⚠️  git push gav op")
    except Exception as e:  # noqa: BLE001 - git-fejl må aldrig vælte botten
        print(f"  ⚠️  git-fejl under commit: {e}")


def send_telegram(info):
    linjer = ["🏠 <b>NY KEREBY-BOLIG</b>"]
    if info.get("adresse"):
        linjer.append(f"📍 {html.escape(info['adresse'])}")
    if info.get("overskrift"):
        linjer.append(html.escape(info["overskrift"]))

    meta = []
    if info.get("vaerelser"):
        meta.append(f"{info['vaerelser']} vær.")
    if info.get("stoerrelse"):
        meta.append(f"{info['stoerrelse']} m²")
    if info.get("husleje"):
        meta.append(html.escape(info["husleje"]))
    if meta:
        linjer.append(" · ".join(meta))

    linjer.append(f"\n🔗 {info['url']}")
    message = "\n".join(linjer)

    for forsoeg in range(1, 4):
        try:
            resp = SESSION.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data={
                    "chat_id": CHAT_ID,
                    "text": message,
                    "parse_mode": "HTML",
                },
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            return
        except Exception as e:  # noqa: BLE001
            if forsoeg == 3:
                raise
            print(f"  ! Telegram-fejl (forsøg {forsoeg}): {e} - prøver igen")
            time.sleep(3)


def tjek_en_gang(seen):
    html_text = fetch_html()
    ledige = parse_ledige_boliger(html_text)

    # Første kørsel nogensinde: gem udgangspunktet uden at spamme.
    if not seen:
        seen.update(ledige)
        gem_set(seen)
        print(f"[{_now()}] Første kørsel - gemmer {len(seen)} boliger som udgangspunkt.")
        return

    nye = [u for u in ledige if u not in seen]
    if not nye:
        print(f"[{_now()}] {len(ledige)} ledige boliger, ingen nye.")
        return

    print(f"[{_now()}] {len(nye)} NY(E) bolig(er)!")
    antal_sendt = 0
    for url in sorted(nye):
        info = ledige[url]
        try:
            send_telegram(info)
        except Exception as e:  # noqa: BLE001
            # Ikke markeret som set -> prøves igen ved næste tjek.
            print(f"  ❌ kunne ikke sende besked for {url}: {e}")
            continue
        seen.add(url)
        gem_set(seen)
        antal_sendt += 1
        print(f"  ✅ sendt: {info.get('adresse') or url}")

    if antal_sendt:
        commit_state(antal_sendt)


def main():
    print(f"🔎 Kereby-bot starter ({_now()}), "
          f"loop={RUN_DURATION}s interval={POLL_INTERVAL}s")
    seen = hent_set()
    deadline = time.monotonic() + RUN_DURATION

    while True:
        try:
            tjek_en_gang(seen)
        except Exception as e:  # noqa: BLE001 - et enkelt fejlet tjek må ikke vælte loopet
            print(f"  ⚠️  tjek fejlede: {e}", file=sys.stderr)

        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_INTERVAL)

    print(f"✅ Færdig ({_now()}).")


if __name__ == "__main__":
    main()
