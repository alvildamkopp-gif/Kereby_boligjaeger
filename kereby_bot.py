import requests
from bs4 import BeautifulSoup

URL = "https://kereby.dk/bolig/"

response = requests.get(URL, timeout=20)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

print("Kereby blev hentet!")
print("Antal links fundet:", len(soup.find_all("a")))

for link in soup.find_all("a", href=True):
    href = link["href"]
    text = link.get_text(" ", strip=True)

    if "/bolig/" in href and text:
        print(text[:100], "→", href)
