"""Story automatique : republie la vidéo d'un reel qui vient d'être publié en story sur @bizhakesef.

Appelé par Make juste après la publication d'un reel (repository_dispatch « story », payload {video, titre}).
API Instagram Graph (connexion Facebook) : conteneur STORIES, attente du traitement vidéo, puis publication.
Variables : IG_ACCESS_TOKEN (clé de page, permanente), TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID, JOB (payload JSON)
"""
import json, os, time, urllib.error, urllib.parse, urllib.request

import photo_choice as PC

IG = "17841471409771872"  # @bizhakesef
API = "https://graph.facebook.com/v24.0"


def graph(method, path, **q):
    q["access_token"] = os.environ["IG_ACCESS_TOKEN"].strip()
    data = urllib.parse.urlencode(q).encode()
    url = f"{API}/{path}" + ("" if method == "POST" else "?" + data.decode())
    req = urllib.request.Request(url, data=data if method == "POST" else None, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(json.load(e).get("error", {}).get("message", str(e)))


def main():
    job = json.loads(os.environ["JOB"])
    titre = job.get("titre", "")
    try:
        cid = graph("POST", f"{IG}/media", media_type="STORIES", video_url=job["video"])["id"]
        for _ in range(40):  # Instagram traite la vidéo (souvent 20 à 60 s)
            st = graph("GET", cid, fields="status_code,status").get("status_code")
            if st == "FINISHED":
                break
            if st in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"traitement de la vidéo refusé par Instagram ({st})")
            time.sleep(6)
        else:
            raise RuntimeError("traitement de la vidéo trop long")
        sid = graph("POST", f"{IG}/media_publish", creation_id=cid)["id"]
        print("Story publiée :", sid)
        PC.send_text(f"📲 Story publiée : {titre}")
    except Exception as e:
        print("Échec story :", e)
        PC.send_text(f"⚠️ Le reel est bien publié, mais pas sa story ({titre}) : {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
