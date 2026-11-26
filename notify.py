"""Après le montage : envoie la vidéo chez Cloudinary, puis prévient le scénario Make via son webhook Telegram.

Usage : python notify.py <id> <video.mp4>       (succès)
        python notify.py <id> --error "<message>" (échec)
Variables : CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET, MAKE_HOOK_URL, ADMIN_CHAT_ID
"""
import hashlib, json, os, sys, time, urllib.request, uuid


RELAIS = "https://bizhakesef-bot.levymikael.workers.dev/gh"


def post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode()


def upload_video(path, public_id):
    cloud, key, secret = (os.environ[k] for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"))
    ts = str(int(time.time()))
    params = {"folder": "bizhakesef/reels", "public_id": public_id, "timestamp": ts}
    sig = hashlib.sha1(("&".join(f"{k}={params[k]}" for k in sorted(params)) + secret).encode()).hexdigest()
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in {**params, "api_key": key, "signature": sig}.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"reel.mp4\"\r\n"
             "Content-Type: video/mp4\r\n\r\n").encode() + open(path, "rb").read() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.cloudinary.com/v1_1/{cloud}/video/upload", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["secure_url"]


def tell_make(text):
    chat = int(os.environ["ADMIN_CHAT_ID"])
    now = int(time.time())
    update = {"update_id": now, "message": {"message_id": 1, "date": now, "text": text,
                                            "from": {"id": chat, "is_bot": False, "first_name": "Reel"},
                                            "chat": {"id": chat, "type": "private"}}}
    # Passe par le relais Cloudflare (gratuit, sans crédit Make) ; Make en secours si le relais ne répond pas.
    # Authentification : SHA-256 de l'URL du webhook Make, que le relais connaît aussi.
    hook = os.environ["MAKE_HOOK_URL"]
    try:
        req = urllib.request.Request(RELAIS, data=json.dumps(update).encode(), headers={
            "Content-Type": "application/json", "User-Agent": "bizhakesef-github/1.0",
            "X-Relais": hashlib.sha256(hook.encode()).hexdigest()})
        with urllib.request.urlopen(req, timeout=90) as r:
            print("relais :", r.read().decode())
            return
    except Exception as e:
        print("relais indisponible, envoi direct à Make :", e)
    print(post_json(hook, update))


if __name__ == "__main__":
    job_id = sys.argv[1]
    if sys.argv[2] == "--error":
        tell_make(f"REELERR|{job_id}|{sys.argv[3][:300]}")
    else:
        url = upload_video(sys.argv[2], f"reel-{job_id}")
        print("vidéo :", url)
        tell_make(f"REEL|{job_id}|{url}")
