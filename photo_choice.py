"""Validation de la photo de couverture par l'administrateur, entre deux passages GitHub :
1. render_reel.py (mode « choix ») analyse le post, classe les photos candidates, enregistre le plan chez Cloudinary
   et envoie les photos sur Telegram avec un bouton par photo (« PH1|id », …, « PH0|id » = sans photo) ;
2. le clic relance render_reel.py avec « photo » = numéro choisi : le plan enregistré est repris tel quel.

Variables : CLOUDINARY_*, TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID
"""
import hashlib, io, json, os, time, urllib.parse, urllib.request, uuid


def _multipart(fields, files):
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, (fname, data, mime) in files.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\n"
                 f"Content-Type: {mime}\r\n\r\n").encode() + data + b"\r\n"
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def _cld():
    return tuple(os.environ[k] for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"))


def save_plan(job_id, plan):
    """Enregistre le plan (scènes, entité, photos candidates) en fichier brut chez Cloudinary."""
    cloud, key, secret = _cld()
    params = {"public_id": f"bizhakesef/plans/plan-{job_id}.json", "timestamp": str(int(time.time())),
              "overwrite": "true", "invalidate": "true"}
    sig = hashlib.sha1(("&".join(f"{k}={params[k]}" for k in sorted(params)) + secret).encode()).hexdigest()
    body, ctype = _multipart({**params, "api_key": key, "signature": sig},
                             {"file": ("plan.json", json.dumps(plan, ensure_ascii=False).encode(), "application/json")})
    req = urllib.request.Request(f"https://api.cloudinary.com/v1_1/{cloud}/raw/upload", data=body,
                                 headers={"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["secure_url"]


def load_plan(job_id):
    """Relit le plan enregistré (version la plus récente, via l'API d'administration), ou None."""
    cloud, key, secret = _cld()
    pid = urllib.parse.quote(f"bizhakesef/plans/plan-{job_id}.json", safe="")
    req = urllib.request.Request(f"https://api.cloudinary.com/v1_1/{cloud}/resources/raw/upload/{pid}")
    import base64
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            url = json.load(r)["secure_url"]
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except Exception:
        return None


def _flatten(data, mime):
    """Logos PNG transparents : posés sur fond blanc pour l'aperçu Telegram (sinon fond noir)."""
    if mime != "image/png":
        return data
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        bg = Image.new("RGBA", (im.width + 120, im.height + 120), "white")
        bg.alpha_composite(im, (60, 60))
        out = io.BytesIO()
        bg.convert("RGB").save(out, "JPEG", quality=92)
        return out.getvalue()
    except Exception:
        return data


def _tg(method, fields, files=None):
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN'].strip()}/{method}"
    body, ctype = _multipart(fields, files or {})
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers={"Content-Type": ctype}), timeout=60) as r:
        return json.load(r)


def send_choice(job_id, titre, cands):
    """Envoie les photos candidates (album numéroté) puis les boutons de choix."""
    chat = os.environ["ADMIN_CHAT_ID"].strip()
    media, files = [], {}
    for i, c in enumerate(cands):
        files[f"p{i}"] = (f"p{i}.jpg", _flatten(c["data"], c["mime"]), "image/jpeg")
        media.append({"type": "photo", "media": f"attach://p{i}", "caption": f"{i + 1}" + (" (logo)" if c.get("logo") else "")})
    if len(media) == 1:
        _tg("sendPhoto", {"chat_id": chat, "caption": "1"}, {"photo": files["p0"]})
    else:
        _tg("sendMediaGroup", {"chat_id": chat, "media": json.dumps(media)}, files)
    nums = [{"text": f"{i + 1}", "callback_data": f"PH{i + 1}|{job_id}"} for i in range(len(cands))]
    kb = {"inline_keyboard": [nums, [{"text": "🚫 Sans photo", "callback_data": f"PH0|{job_id}"}]]}
    _tg("sendMessage", {"chat_id": chat, "reply_markup": json.dumps(kb, ensure_ascii=False),
                        "text": f"📸 Quelle photo en couverture du reel « {titre} » ?\nLe montage démarre dès ton choix (1 à 2 minutes)."})
