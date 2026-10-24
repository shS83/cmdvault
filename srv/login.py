import getpass
import json
import urllib.request
import urllib.error
from pathlib import Path

device_name = Path("/etc/hostname").read_text().strip()

url = "https://sync.prometheus-alexander.fi/api/v1/auth/login"

username = input("cmdvault username: ")
password = getpass.getpass("Password: ")

payload = json.dumps({
    "schema_version": 1,
    "username": username,
    "password": password,
    "display_name": device_name,
}).encode("utf-8")

request = urllib.request.Request(
    url,
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)

try:
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.load(response)

    print("Login successful!")
    print("Device ID:", data["device_id"])
    print("Token expires:", data["expires_at"])

    # Älä tulosta tokenia ruudulle.
    import pathlib
    path = pathlib.Path.home() / ".config/cmdvault/device.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    import os
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({
            "device_id": data["device_id"],
            "token": data["token"],
            "expires_at": data["expires_at"],
        }, f)

    print("Credentials saved:", path)

except urllib.error.HTTPError as exc:
    print("Login failed:", exc.code)
    print(exc.read().decode()[:500])
