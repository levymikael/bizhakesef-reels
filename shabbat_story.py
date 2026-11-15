"""Story du vendredi matin : horaires d'entrée et de sortie de Shabbat (Jérusalem, Tel-Aviv, Paris).

Horaires Hebcal (allumage des bougies : 40 min avant le coucher du soleil à Jérusalem, 18 min ailleurs ;
sortie : tombée de la nuit, soleil à 8,5° sous l'horizon). Visuel 1080x1920 rendu dans Chromium, hébergé chez
Cloudinary, publié en story sur @bizhakesef, puis confirmation sur Telegram.

Usage :
  python shabbat_story.py                -> publie (workflow du vendredi)
  python shabbat_story.py --apercu       -> envoie seulement l'image sur Telegram
  python shabbat_story.py --local x.jpg  -> écrit l'image en local
Variables : IG_ACCESS_TOKEN, CLOUDINARY_*, TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID ; PW_CHANNEL ;
GARDE_9H=1 : ne publie que s'il est entre 9 h et 10 h 59 à Jérusalem (le cron GitHub est en UTC, été comme hiver).
"""
import datetime as dt, html, json, os, sys, tempfile, time, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
VILLES = [("Jérusalem", 281184), ("Tel-Aviv", 293397), ("Paris", 2988507)]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def horaires():
    """[(ville, entrée, sortie)], la paracha et la date du vendredi, pour le Shabbat à venir."""
    lignes, paracha, vendredi = [], ("", ""), None
    for nom, gid in VILLES:
        url = f"https://www.hebcal.com/shabbat?cfg=json&geonameid={gid}&M=on&lg=fr"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
            d = json.load(r)
        it = {i["category"]: i for i in d["items"] if i["category"] in ("candles", "havdalah", "parashat")}
        entree, sortie = it["candles"]["date"], it["havdalah"]["date"]
        lignes.append((nom, entree[11:16], sortie[11:16]))
        vendredi = vendredi or dt.date.fromisoformat(entree[:10])
        if not paracha[0] and it.get("parashat"):  # (nom français sans « Parachah », nom hébreu)
            pa = it["parashat"]
            paracha = (pa.get("title", "").replace("Parachah", "").strip(), pa.get("hebrew", "").replace("פרשת", "").strip())
    return lignes, paracha, vendredi


def page(lignes, paracha, vendredi):
    e = lambda s: html.escape(s, quote=True)
    cartes = "".join(
        f'<div class="ville"><div class="nom">{e(n)}</div>'
        f'<div class="h"><span>Entrée</span><b>{a}</b></div><div class="h"><span>Sortie</span><b>{b}</b></div></div>'
        for n, a, b in lignes)
    date = f"Vendredi {vendredi.day} {MOIS[vendredi.month - 1]}"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;800;900&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: 1080px; height: 1920px; background: #FFFFFF; }}
body {{ position: relative; overflow: hidden; color: #0B1B3F; font-family: 'Inter', sans-serif;
  display: flex; flex-direction: column; padding: 260px 90px 300px; }}
body::before, body::after {{ content: ""; position: absolute; left: 0; right: 0; height: 40px; background: #0038B8; }}
body::before {{ top: 120px; }} body::after {{ bottom: 120px; }}
.etoile {{ position: absolute; width: 900px; height: 900px; right: -300px; top: 520px; opacity: .05;
  background: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><g fill='none' stroke='%230038B8' stroke-width='4'><polygon points='50,8 86.4,71 13.6,71'/><polygon points='50,92 13.6,29 86.4,29'/></g></svg>") center / contain no-repeat; }}
.marque {{ font-family: 'Archivo', sans-serif; font-weight: 900; font-size: 38px; letter-spacing: .14em; color: #0038B8;
  display: flex; align-items: center; gap: 18px; }}
.mark {{ display: inline-grid; place-items: center; width: 62px; height: 62px; border-radius: 16px; background: #0038B8; color: #fff; font-size: 36px; letter-spacing: 0; }}
.kicker {{ align-self: flex-start; margin-top: 70px; font-size: 30px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase;
  color: #fff; background: #0038B8; border-radius: 99px; padding: 14px 32px; }}
h1 {{ margin-top: 30px; font-family: 'Archivo', sans-serif; font-weight: 900; font-size: 128px; line-height: .95; letter-spacing: -.03em; }}
.sous {{ margin-top: 26px; font-size: 38px; font-weight: 500; color: #5B6B8C; }}
.paracha {{ margin-top: 50px; display: grid; grid-template-columns: 1fr auto; align-items: end; row-gap: 6px;
  border-left: 10px solid #0038B8; padding: 8px 0 8px 34px; }}
.paracha span {{ grid-column: 1 / -1; font-size: 26px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; color: #5B6B8C; }}
.paracha b {{ font-family: 'Archivo', sans-serif; font-weight: 900; font-size: 76px; line-height: 1; color: #0038B8; letter-spacing: -.02em; }}
.paracha i {{ font-style: normal; font-size: 58px; font-weight: 600; color: #0B1B3F; direction: rtl; }}
.villes {{ margin-top: 56px; display: flex; flex-direction: column; gap: 28px; }}
.ville {{ display: grid; grid-template-columns: 1fr auto auto; align-items: center; gap: 44px;
  background: #EEF3FF; border-radius: 28px; padding: 34px 44px; }}
.nom {{ font-family: 'Archivo', sans-serif; font-weight: 800; font-size: 48px; }}
.h {{ display: flex; flex-direction: column; align-items: flex-end; }}
.h span {{ font-size: 24px; font-weight: 600; letter-spacing: .1em; text-transform: uppercase; color: #5B6B8C; }}
.h b {{ font-family: 'Archivo', sans-serif; font-weight: 900; font-size: 64px; color: #0038B8; letter-spacing: -.02em; }}
.note {{ margin-top: 34px; font-size: 26px; color: #5B6B8C; line-height: 1.4; }}
</style></head><body><div class="etoile"></div>
<div class="marque"><span class="mark">₪</span>BIZHAKESEF</div>
<div class="kicker">Horaires de Shabbat</div>
<h1>Shabbat<br>Shalom</h1>
<div class="sous">{e(date)}</div>
<div class="paracha"><span>Paracha de la semaine</span><b>{e(paracha[0])}</b><i>{e(paracha[1])}</i></div>
<div class="villes">{cartes}</div>
<div class="note">Heures locales. Source : Hebcal.</div>
</body></html>"""


def rendre(out):
    from playwright.sync_api import sync_playwright
    f = Path(tempfile.mkdtemp()) / "shabbat.html"
    f.write_text(page(*horaires()), encoding="utf-8")
    with sync_playwright() as pw:
        kw = {"channel": os.environ["PW_CHANNEL"]} if os.environ.get("PW_CHANNEL") else {}
        br = pw.chromium.launch(**kw)
        p = br.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
        p.goto(f.resolve().as_uri(), wait_until="networkidle")
        p.evaluate("document.fonts.ready")
        p.wait_for_timeout(200)
        p.screenshot(path=str(out), type="jpeg", quality=92)
        br.close()
    return Path(out)


def main():
    sys.path.insert(0, str(HERE))
    import photo_choice as PC
    if "--local" in sys.argv:
        print(rendre(sys.argv[sys.argv.index("--local") + 1]))
        return
    if os.environ.get("GARDE_9H") == "1":
        from zoneinfo import ZoneInfo
        h = dt.datetime.now(ZoneInfo("Asia/Jerusalem")).hour
        if not 9 <= h < 11:
            print("Pas l'heure (", h, "h à Jérusalem) : l'autre déclenchement s'en charge.")
            return
    img = rendre(Path(tempfile.mkdtemp()) / "shabbat.jpg")
    if "--apercu" in sys.argv:
        PC._tg("sendPhoto", {"chat_id": os.environ["ADMIN_CHAT_ID"].strip(),
                             "caption": "👀 Aperçu de la story Shabbat du vendredi 9 h (rien n'est publié)."},
               {"photo": ("shabbat.jpg", img.read_bytes(), "image/jpeg")})
        return
    import render_slides, story
    try:
        url = render_slides.upload_image(img, f"shabbat-{dt.date.today():%Y%m%d}")
        cid = story.graph("POST", f"{story.IG}/media", media_type="STORIES", image_url=url)["id"]
        for _ in range(20):
            st = story.graph("GET", cid, fields="status_code").get("status_code")
            if st == "FINISHED":
                break
            if st in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"image refusée par Instagram ({st})")
            time.sleep(3)
        sid = story.graph("POST", f"{story.IG}/media_publish", creation_id=cid)["id"]
        print("Story Shabbat publiée :", sid)
        PC.send_text("🕯 Story Shabbat publiée (horaires Jérusalem, Tel-Aviv, Paris).")
    except Exception as ex:
        PC.send_text(f"⚠️ Story Shabbat non publiée : {ex}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
