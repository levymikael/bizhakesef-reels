"""Rend les slides d'un carrousel Bizhakesef (1080x1350) dans Chromium, les héberge chez Cloudinary
et renvoie leurs adresses au scénario Make. Remplace l'API HTML/CSS to Image (quota gratuit trop faible).

Usage : python render_slides.py job.json
job.json : {"id": "...", "source": "Globes", "date": "04/11/2026",
            "slides": [{"type": "cover", "kicker": "...", "titre": "...", "texte": "...", "chiffre": "...", "tendance": "..."}, ...]}
Variables : CLOUDINARY_*, MAKE_HOOK_URL, ADMIN_CHAT_ID ; PW_CHANNEL (« chrome » sur GitHub, « msedge » en local).
Option --local DOSSIER : écrit les images dans DOSSIER sans les envoyer (test).
"""
import hashlib, html, json, os, re, sys, time, urllib.request, uuid
from pathlib import Path

HERE = Path(__file__).parent
TEMPLATE = (HERE / "slides" / "slide.html").read_text(encoding="utf-8")
CSS = (HERE / "slides" / "slide.css").read_text(encoding="utf-8")


def page_html(values):
    body = re.sub(r"\{\{(\w+)\}\}", lambda m: html.escape(str(values.get(m.group(1), "") or ""), quote=True), TEMPLATE)
    return f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{body}</body></html>'


def serie(job):
    """Habillage propre à chaque série, reconnu au sur-titre de la couverture."""
    k = ((job.get("slides") or [{}])[0].get("kicker") or "").upper()
    return "portrait" if "PORTRAIT" in k else "savoir" if "SAVIEZ" in k else "actu"


def render_all(job, out_dir):
    from playwright.sync_api import sync_playwright
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    slides, files = job["slides"], []
    with sync_playwright() as pw:
        kw = {"channel": os.environ["PW_CHANNEL"]} if os.environ.get("PW_CHANNEL") else {}
        br = pw.chromium.launch(**kw)
        page = br.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
        for i, s in enumerate(slides):
            values = {**s, "num": i + 1, "total": len(slides), "source": job.get("source", ""), "date": job.get("date", ""),
                      "serie": serie(job)}
            f = out_dir / f"slide-{i + 1}.html"
            f.write_text(page_html(values), encoding="utf-8")
            page.goto(f.resolve().as_uri(), wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(150)
            img = out_dir / f"slide-{i + 1}.jpg"
            page.screenshot(path=str(img), type="jpeg", quality=92, clip={"x": 0, "y": 0, "width": 1080, "height": 1350})
            files.append(img)
        br.close()
    return files


def upload_image(path, public_id):
    cloud, key, secret = (os.environ[k] for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"))
    ts = str(int(time.time()))
    params = {"folder": "bizhakesef", "public_id": public_id, "timestamp": ts}
    sig = hashlib.sha1(("&".join(f"{k}={params[k]}" for k in sorted(params)) + secret).encode()).hexdigest()
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in {**params, "api_key": key, "signature": sig}.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"slide.jpg\"\r\n"
             "Content-Type: image/jpeg\r\n\r\n").encode() + Path(path).read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.cloudinary.com/v1_1/{cloud}/image/upload", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["secure_url"]


if __name__ == "__main__":
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if isinstance(job.get("slides"), str):  # Make peut transmettre le tableau sous forme de texte JSON
        job["slides"] = json.loads(job["slides"])
    if "--local" in sys.argv:
        print([str(p) for p in render_all(job, sys.argv[sys.argv.index("--local") + 1])])
        sys.exit()
    files = render_all(job, "slides_out")
    urls = [upload_image(p, f"slide-{job['id']}-{i + 1}") for i, p in enumerate(files)]
    print("\n".join(urls))
    sys.path.insert(0, str(HERE))
    from notify import tell_make
    tell_make(f"SLIDES|{job['id']}|" + ",".join(urls))
