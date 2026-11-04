"""Récupère une photo envoyée au bot Telegram, l'héberge chez Cloudinary et renvoie son adresse au scénario Make.
(Make ne sait pas réutiliser le contenu d'un fichier téléchargé depuis Telegram.)

Usage : python fetch_photo.py job.json        job.json : {"file_id": "...", "unique": "...", "caption": "..."}
Variables : TELEGRAM_BOT_TOKEN, CLOUDINARY_*, MAKE_HOOK_URL, ADMIN_CHAT_ID
"""
import json, os, sys, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from render_slides import upload_image
from notify import tell_make

job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
tok = os.environ["TELEGRAM_BOT_TOKEN"].strip()
with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getFile?" + urllib.parse.urlencode({"file_id": job["file_id"]}), timeout=30) as r:
    path = json.load(r)["result"]["file_path"]
with urllib.request.urlopen(f"https://api.telegram.org/file/bot{tok}/{path}", timeout=60) as r:
    Path("photo.jpg").write_bytes(r.read())
url = upload_image("photo.jpg", f"photo-{job['unique']}")
caption = (job.get("caption") or "").replace("|", "/")
tell_make(f"PHOTO|{job['unique']}|{url}|{caption}")
print(url)
