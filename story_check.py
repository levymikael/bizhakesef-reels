"""Vérifie la clé IG_ACCESS_TOKEN (lecture seule, rien n'est publié) et affiche ce qu'elle permet."""
import json, os, urllib.parse, urllib.request

T = os.environ.get("IG_ACCESS_TOKEN", "").strip()
IG_ID = "17841471409771872"


def get(url, **q):
    q["access_token"] = T
    try:
        with urllib.request.urlopen(url + "?" + urllib.parse.urlencode(q), timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"erreur": json.load(e).get("error", {}).get("message")}


if not T:
    raise SystemExit("Secret IG_ACCESS_TOKEN absent")
print("Type de clé :", "Instagram (IGAA…)" if T.startswith("IG") else "Facebook", "| longueur", len(T))
if T.startswith("IG"):
    print("Compte :", get("https://graph.instagram.com/v24.0/me", fields="user_id,username,account_type"))
else:
    d = get("https://graph.facebook.com/v24.0/debug_token", input_token=T).get("data", {})
    print("Type :", d.get("type"), "| valide :", d.get("is_valid"), "| expire :", d.get("expires_at"),
          "| accès données jusqu'au :", d.get("data_access_expires_at"))
    print("Autorisations :", d.get("scopes"))
    pages = get("https://graph.facebook.com/v24.0/me/accounts", fields="name,instagram_business_account{id,username}")
    for p in pages.get("data", []):
        print("Page :", p.get("name"), "| Instagram :", p.get("instagram_business_account"))
    if "erreur" in pages:
        print("Pages :", pages)
