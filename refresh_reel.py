"""Actualisation de dernière minute d'un reel sur le shekel : relève le cours en direct, met à jour la légende,
remonte le reel avec ces cours, puis renvoie le tout au scénario Make (« MAJ|id|url|légende|résumé », en base64).

Usage : python refresh_reel.py job.json
job.json : {"id": "...", "slides": [...], "caption": "...", "titre": "..."}
"""
import base64, datetime, json, sys, time, urllib.request, zoneinfo
from pathlib import Path

import render_reel as R
from notify import tell_make, upload_video

TZ = zoneinfo.ZoneInfo("Asia/Jerusalem")
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
PAIRS = {"1 $": "ILS=X", "1 €": "EURILS=X"}

CAPTION_PROMPT = """Voici la légende Instagram d'un post @bizhakesef (média d'information neutre, en français) sur le shekel, rédigée le {date_article} à partir d'un article de presse. Le post sera publié dans quelques minutes.

COURS EN DIRECT, relevés {quand} (heure d'Israël) :
{cours}

Mets la légende à jour :
- Remplace les cours de change actuels cités par ces cours en direct, au format français (3,202 ₪), et précise une fois l'heure du relevé (par exemple « à 12 h 05 ce jeudi »).
- Les faits passés de l'article restent vrais à leur date : si une affirmation au présent n'est plus vérifiable avec le cours en direct (« au plus bas depuis 7 mois », une variation en %), formule-la au passé en la datant (« mardi, le shekel était tombé… »). N'invente aucune nouvelle comparaison, aucun record, aucune variation que tu ne peux pas calculer à partir des données fournies.
- Garde tout le reste à l'identique : structure, longueur, ton, appel à l'action, ligne de source.

`resume` : une ligne pour le responsable du compte, du type « Dollar : 3,184 → 3,202 ₪ (relevé à 12 h 05) ».

LÉGENDE :
"""
SCHEMA = {"type": "object", "required": ["legende", "resume"],
          "properties": {"legende": {"type": "string"}, "resume": {"type": "string"}}}


def quote(symbol):
    req = urllib.request.Request(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=5m&range=1d",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        meta = json.load(r)["chart"]["result"][0]["meta"]
    return meta["regularMarketPrice"], meta["regularMarketTime"]


UNITS = "zéro un deux trois quatre cinq six sept huit neuf dix onze douze treize quatorze quinze seize dix-sept dix-huit dix-neuf".split()
TENS = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante", 8: "quatre-vingt"}


def words(n):
    """Nombre entier de 0 à 999 en toutes lettres (français de France)."""
    if n < 20:
        return UNITS[n]
    if n < 100:
        t, u = divmod(n, 10)
        if t in (7, 9):
            return TENS[t - 1] + ("-et-" if t == 7 and u == 1 else "-") + UNITS[10 + u]
        base = TENS[t] + ("s" if t == 8 and u == 0 else "")
        return base if u == 0 else base + ("-et-" if u == 1 and t != 8 else "-") + UNITS[u]
    h, r = divmod(n, 100)
    head = ("cent" if h == 1 else UNITS[h] + " cent" + ("s" if r == 0 else ""))
    return head if r == 0 else head + " " + words(r)


def spoken(x):
    """3.203 -> « trois virgule deux cent trois » (comme le dit un présentateur)."""
    ent, dec = f"{x:.3f}".split(".")
    d = dec.rstrip("0") or "0"
    return f"{words(int(ent))} virgule " + ("zéro " * (len(d) - len(d.lstrip("0")))) + words(int(d))


def fr(x):
    return f"{x:.3f}".replace(".", ",")


def main(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    quotes = {k: quote(s) for k, s in PAIRS.items()}
    t = datetime.datetime.fromtimestamp(max(ts for _, ts in quotes.values()), TZ)
    quand = f"le {JOURS[t.weekday()]} {t.day:02d}/{t.month:02d} à {t.hour} h {t.minute:02d}"
    cours = "\n".join(f"{k} = {fr(v)} ₪" for k, (v, _) in quotes.items())
    i = job["id"]
    date_article = f"{i[6:8]}/{i[4:6]}/{i[:4]}" if len(i) >= 8 and i[:8].isdigit() else "jour de l'article"
    print("Cours :", cours.replace("\n", " ; "), "|", quand, flush=True)

    body = {"contents": [{"parts": [{"text": CAPTION_PROMPT.format(date_article=date_article, quand=quand, cours=cours)
                                     + job["caption"]}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA}}
    d = R.first_ok(R.TEXT_MODELS, body)
    out = json.loads("".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought")))
    print("Résumé :", out["resume"], flush=True)

    job["caption"] = out["legende"]
    job["maj"] = (f"cours en direct relevés {quand} (heure d'Israël) : " + cours.replace("\n", " ; ")
                  + ". Affiche et dis ces cours-là à la place des cours actuels des slides. Les faits passés des slides "
                  "restent valables mais se disent au passé, datés (« mardi »). N'invente aucune nouvelle comparaison.")
    usd = quotes["1 $"][0]
    jour = f"ce {JOURS[t.weekday()]}"
    job["scene_maj"] = {"chiffre": f"{fr(usd)} ₪", "kicker": f"COURS À {t.hour} H {t.minute:02d}", "mots": spoken(usd),
                        "titre": f"Le dollar à {fr(usd)} ₪ {jour}",
                        "phrase": f"{jour.capitalize()}, le dollar s'échange à {spoken(usd)} shekels."}
    job["maj"] += (f" La scène 2 annonce le cours en direct du dollar : chiffre « {fr(usd)} ₪ », et sa phrase dit "
                   f"« {spoken(usd)} shekels » ; le creux passé de l'article peut figurer dans son texte.")
    R.render(job, "reel.mp4")
    url = upload_video("reel.mp4", f"reel-{i}-maj{int(time.time())}")
    b64 = lambda s: base64.b64encode(s.encode()).decode()
    tell_make(f"MAJ|{i}|{url}|{b64(out['legende'])}|{b64(out['resume'])}")


if __name__ == "__main__":
    main(sys.argv[1])
