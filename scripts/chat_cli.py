"""Test harness: chat with the API as a website visitor or as staff.

    python scripts/chat_cli.py                       # visitor (web channel)
    python scripts/chat_cli.py --staff consultant.dev --password dev-consultant-123

Staff mode gets a token from local Keycloak (dev users from infra/keycloak/realm-assistant.json).
"""

import argparse
import sys

import httpx


def staff_token(kc: str, username: str, password: str) -> str:
    r = httpx.post(
        f"{kc}/realms/assistant/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "assistant-dashboard",
              "username": username, "password": password},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--keycloak", default="http://localhost:8080")
    ap.add_argument("--staff", help="Keycloak username (internal channel)")
    ap.add_argument("--password")
    a = ap.parse_args()

    c = httpx.Client(base_url=a.api, timeout=120)
    if a.staff:
        headers = {"Authorization": f"Bearer {staff_token(a.keycloak, a.staff, a.password)}"}
        cid = c.post("/v1/internal/conversations", headers=headers).raise_for_status().json()[
            "conversation_id"]
        url = f"/v1/internal/conversations/{cid}/messages"
        print(f"[internal conversation {cid}]")
    else:
        s = c.post("/v1/chat/sessions", json={"consent": True}).raise_for_status().json()
        headers = {"Authorization": f"Bearer {s['token']}"}
        url = "/v1/chat/messages"
        print(f"[conversation {s['conversation_id']}]\nassistant> {s['greeting']}")

    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        r = c.post(url, json={"text": text}, headers=headers)
        if r.status_code != 200:
            print(f"[{r.status_code}] {r.text}", file=sys.stderr)
            continue
        body = r.json()
        print(f"assistant> {body['reply']}" + ("  [degraded]" if body.get("degraded") else ""))


if __name__ == "__main__":
    main()
