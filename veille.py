"""Veille quotidienne de @bizhakesef, sur GitHub Actions (ne consomme plus de crédits Make).
Lit les flux d'actualité, fait choisir les meilleurs sujets par Gemini et les propose sur Telegram avec le bouton
« Créer le carrousel » (callback GEN|veille, traité par le scénario Make principal, qui relit l'URL sur la dernière ligne
et le titre sur la 3e ligne du message).

Usage : python veille.py semaine|shabbat [--sec] [--garde]
  --sec   : affiche la sélection sans rien envoyer ni enregistrer (test)
  --garde : ne tourne qu'à l'heure prévue en Israël (8 h en semaine, 21 h le samedi), une seule fois par jour
Variables : GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID, CLOUDINARY_*
"""
import datetime, json, os, re, sys, time, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import photo_choice as PC

FEEDS = [  # Substack bloque GitHub (403) : lu via le relais r.jina.ai
    "https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID=1725",
    "https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID=585",
    "https://www.ynet.co.il/Integration/StoryRss6.xml",
    "https://www.geektime.co.il/feed/",
    "https://r.jina.ai/https://www.calcalistech.com/ctechnews",
    "https://r.jina.ai/https://yaelleifrah.substack.com/api/v1/archive?sort=new&limit=5",
    "https://r.jina.ai/https://israelinvestmentreport.substack.com/api/v1/archive?sort=new&limit=5",
    "https://r.jina.ai/https://israeluncovered.substack.com/api/v1/archive?sort=new&limit=5",
    "https://r.jina.ai/https://steveornstein.substack.com/api/v1/archive?sort=new&limit=5",
    "https://r.jina.ai/https://kevich.substack.com/api/v1/archive?sort=new&limit=5",
    "https://t.me/s/calcalistcapitalmarkets",
    "https://t.me/s/globesnews",
    "https://t.me/s/TheMarkerOnline",
    "https://t.me/s/TradingIL",
    "https://t.me/s/SmartViewIL",
]
EDITIONS = {"semaine": {"nb": 5, "fenetre": "36 heures", "heure": 8},
            "shabbat": {"nb": 3, "fenetre": "48 heures, vendredi et samedi compris", "heure": 21}}
MODELS = ["gemini-flash-latest", "gemini-3.6-flash", "gemini-flash-lite-latest", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
WAITS = [1, 20, 40, 60, 120]
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["sujets"],
    "properties": {"sujets": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["url", "source", "rubrique", "titre_fr", "resume_fr", "score"],
        "properties": {
            "url": {"type": "string"}, "source": {"type": "string"},
            "rubrique": {"type": "string", "enum": ["MARCHÉS", "MACRO", "ENTREPRISES", "TECH", "IMMOBILIER", "STARTUP"]},
            "titre_fr": {"type": "string"}, "resume_fr": {"type": "string"},
            "score": {"type": "integer"}}}}}}
ETAT = "veille-etat"  # URLs déjà proposées et jours déjà traités (fichier brut chez Cloudinary)


def lire(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; BizhakesefVeille/1.0)",
                                               "X-Retain-Images": "none"})
    with urllib.request.urlopen(req, timeout=60) as r:
        t = r.read().decode("utf-8", "replace")
    t = t.replace("<![CDATA[", "").replace("]]>", "")
    t = re.sub(r'<div class="tgme_widget_message [^"]*" data-post="([^"]+)"', r"\nURL DU MESSAGE : https://t.me/\1?embed=1\n<", t)
    t = re.sub(r"<[^>]*>", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    return t[-14000:] if "t.me/s/" in url else t[:14000]  # Telegram : les messages récents sont à la fin


def gemini(body, cle="sujets"):
    for model, wait in zip(MODELS, WAITS):
        time.sleep(wait)
        try:
            req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                         data=json.dumps(body).encode(),
                                         headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.load(r)
            return json.loads("".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"]))[cle]
        except Exception as e:
            print(f"{model} : {e}", file=sys.stderr)
    return None


VERIF = {"type": "object", "additionalProperties": False, "required": ["sujet"], "properties": {"sujet": {
    "type": "object", "additionalProperties": False, "required": ["source", "rubrique", "titre_fr", "resume_fr", "score"],
    "properties": {k: v for k, v in SCHEMA["properties"]["sujets"]["items"]["properties"].items() if k != "url"}}}}


def verifier(s):
    """Relit l'article complet et corrige le titre et le résumé proposés (le flux ne donne qu'un extrait, et le modèle
    de secours peut confondre deux sociétés citées côte à côte, par exemple Discount et Hapoalim)."""
    try:
        art = lire("https://r.jina.ai/" + s["url"].strip())[:12000]
    except Exception as e:
        print("article illisible, proposition gardée telle quelle :", s["url"], e, file=sys.stderr)
        return s
    consigne = ("Tu vérifies une proposition de sujet pour @bizhakesef, média Instagram d'information en français sur l'économie "
                "israélienne (ton factuel et neutre, sans tutoiement ni question au lecteur). Compare-la à l'article complet "
                "ci-dessous et renvoie-la corrigée : la société, la personne ou l'institution principale doit être celle dont "
                "parle l'article (pas une autre citée en passant), chaque chiffre doit figurer dans l'article, rien n'est ajouté "
                "de mémoire. `titre_fr` 90 caractères maximum, `resume_fr` 2 à 3 phrases et 400 caractères maximum, garde la "
                "rubrique et la note si elles sont justes. Montants à la française (910 M₪, 1,2 Md$, 45 %), shekel en ₪, "
                "noms officiels en caractères latins (בנק דיסקונט → Discount, בנק הפועלים → Bank Hapoalim, כאל → Cal).")
    prop = json.dumps({k: s.get(k, "") for k in ("source", "rubrique", "titre_fr", "resume_fr", "score")}, ensure_ascii=False)
    v = gemini({"systemInstruction": {"parts": [{"text": consigne}]},
                "contents": [{"role": "user", "parts": [{"text": f"PROPOSITION :\n{prop}\n\nARTICLE ({s['url']}) :\n{art}"}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": VERIF}}, "sujet")
    if not v or not v.get("titre_fr"):
        return s
    if v["titre_fr"] != s.get("titre_fr"):
        print("corrigé :", s.get("titre_fr"), "→", v["titre_fr"])
    return {**s, **v, "url": s["url"]}


def main():
    ed_nom = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "semaine"
    ed, sec = EDITIONS[ed_nom], "--sec" in sys.argv
    now = datetime.datetime.now(ZoneInfo("Asia/Jerusalem"))
    etat = PC.load_plan(ETAT) or {"urls": [], "jours": []}
    url_seule = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--url=")), "")
    if url_seule:  # une seule proposition, vérifiée sur l'article (correction ou article signalé à la main)
        s = verifier({"url": url_seule, "source": "", "rubrique": "ENTREPRISES", "titre_fr": "", "resume_fr": "", "score": 5})
        if not s.get("titre_fr"):
            sys.exit("article illisible ou IA indisponible")
        PC.send_text("✏️ " + (os.environ.get("VEILLE_NOTE", "").strip() or "Proposition vérifiée sur l'article complet."))
        sujets_envoyer([s])
        return
    jour = f"{now:%Y-%m-%d}-{ed_nom}"
    if "--garde" in sys.argv:  # cron en UTC : on ne garde que le déclenchement à l'heure d'Israël, une fois par jour
        if not ed["heure"] <= now.hour < ed["heure"] + 2 or jour in etat["jours"]:
            print("hors créneau ou déjà fait :", now, jour)
            return

    textes = []
    for f in FEEDS:
        try:
            textes.append(lire(f))
        except Exception as e:
            print("flux illisible :", f, e, file=sys.stderr)
    prompt = (Path(__file__).with_name("veille_prompt.txt").read_text(encoding="utf-8")
              .replace("{NB}", str(ed["nb"])).replace("{FENETRE}", ed["fenetre"]))
    user = ("ARTICLES DÉJÀ TRAITÉS :\n" + "\n".join(etat["urls"]) + "\n\nFLUX DU JOUR :\n" + "\n=====\n".join(textes))
    sujets = gemini({"systemInstruction": {"parts": [{"text": prompt}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
                     "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA}})
    if sec:
        print(json.dumps(sujets, ensure_ascii=False, indent=1))
        return
    if not sujets:
        PC.send_text("❌ Veille du jour : l'IA n'a pas donné de réponse exploitable (Gemini saturé). "
                     "Envoie-moi directement un lien d'article.")
        sys.exit(1)
    sujets = [s for s in sujets if s["url"].strip().startswith("http") and len(s["url"].strip()) < 600]
    PC.send_text(f"🗞️ Veille du {now:%d/%m/%Y} : {len(sujets)} sujets proposés.\n"
                 "Touche « Créer le carrousel » sous ceux que tu veux traiter.")
    sujets = [verifier(s) for s in sujets]
    sujets_envoyer(sujets)
    etat["urls"] = (etat["urls"] + [s["url"].strip() for s in sujets])[-400:]
    etat["jours"] = (etat["jours"] + [jour])[-30:]
    PC.save_plan(ETAT, etat)


def sujets_envoyer(sujets):
    chat = os.environ["ADMIN_CHAT_ID"].strip()
    kb = json.dumps({"inline_keyboard": [[{"text": "📝 Créer le carrousel", "callback_data": "GEN|veille"}]]}, ensure_ascii=False)
    for i, s in enumerate(sujets, 1):
        # Le titre doit rester sur la 3e ligne et l'URL sur la dernière : le scénario Make les relit au clic
        texte = (f"{i}/{len(sujets)} · {s['rubrique']} · {s['source'][:60]} · importance {s['score']}/10\n\n"
                 f"{s['titre_fr'][:150]}\n\n{s['resume_fr'][:400]}\n\n{s['url'].strip()}")
        try:
            PC._tg("sendMessage", {"chat_id": chat, "text": texte, "reply_markup": kb})
        except Exception as e:
            print("proposition non envoyée :", e, file=sys.stderr)


if __name__ == "__main__":
    main()
