import requests
import re

URL = "https://kereby.dk/bolig/"

response = requests.get(URL, timeout=20)
response.raise_for_status()

print("Kereby blev hentet!")
print("Status:", response.status_code)

links = re.findall(r'href=["\']([^"\']+)["\']', response.text)

print("Antal links fundet:", len(links))

for link in links:
    if "/bolig/" in link:
        print(link)
