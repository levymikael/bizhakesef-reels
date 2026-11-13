"""Série « Portrait » de @bizhakesef : chaque mercredi, une personnalité israélienne du business, d'après son article
Wikipédia (source vérifiable : l'IA n'ajoute rien de mémoire).

Le programme ci-dessous fixe qui passe quand. Chaque mercredi, le premier portrait dont la date est arrivée et qui n'a pas
encore été envoyé est rédigé par Gemini, puis envoyé au scénario Make (« SAVOIR|<json base64>|url ») : l'aperçu arrive sur
Telegram avec les boutons REEL / Carrousel / Rejeter, comme un article. Rien n'est publié sans validation.

Variables : GEMINI_API_KEY, CLOUDINARY_*, MAKE_HOOK_URL, ADMIN_CHAT_ID ; PORTRAIT (facultatif) force une personne.
"""
import base64, datetime, json, os, re, urllib.parse

import render_reel as R
from notify import tell_make
import photo_choice as PC
import urllib.request

# (mercredi prévu, titre exact de l'article Wikipédia en anglais, ou « he:titre » pour Wikipédia en hébreu). Rami Levy est parti en avance le 11/11.
PROGRAMME = [
    ("2026-11-11", "he:רמי לוי"),  # pas d'article en anglais
    ("2026-11-18", "Omri Casspi"),
    ("2026-11-25", "Dov Moran"),
    ("2026-12-02", "Ofra Strauss"),
    ("2026-12-09", "Gil Shwed"),
    ("2026-12-16", "Amnon Shashua"),
    ("2026-12-23", "Yossi Vardi"),
    ("2026-12-30", "Shari Arison"),
    ("2027-01-06", "Assaf Rappaport"),
    ("2027-01-13", "Kira Radinsky"),
    ("2027-01-20", "Stef Wertheimer"),
    ("2027-01-27", "Eyal Waldman"),
    ("2027-02-03", "Avishai Abrahami"),
]
RESERVE = ["Shlomo Kramer", "Yitzhak Tshuva", "Ziv Aviram"]  # si un article est trop maigre

PROMPT = """Tu es le rédacteur en chef de @bizhakesef, un média Instagram d'information en français sur l'économie israélienne. Ton : factuel, précis, neutre, sans sensationnalisme, sans flatterie ni critique, sans tutoiement, sans question au lecteur.

Tu prépares un post de la série « Portrait » : le parcours d'une personnalité israélienne du monde des affaires, à partir de son article Wikipédia ci-dessous (en anglais ou en hébreu). Le lecteur doit apprendre en 30 secondes qui est cette personne, ce qu'elle a construit et ce qui rend son parcours remarquable.

Règles des faits :
- Uniquement les faits de l'article ci-dessous. Aucun chiffre, aucune date, aucun nom ajouté de mémoire, même s'ils te semblent exacts.
- Parcours professionnel et économique : origines utiles à comprendre le parcours, création ou direction d'entreprises, chiffres clés (taille, valorisation, rachat, salariés, fortune si l'article la donne), apports à l'économie israélienne. Pas de vie privée au-delà de ce qui éclaire le parcours, pas de politique sauf si elle fait partie du parcours économique.
- Si l'article mentionne une affaire judiciaire ou une controverse majeure liée aux affaires, mentionne-la sobrement et factuellement, sans jugement.
- Tout chiffre est daté (« en 2019 ») si l'article le date. Garde la devise d'origine, montants à la française (670 M$, 1,8 Md$, 6 Md₪, 45 %) ; le shekel s'écrit ₪ ou « shekels », jamais « NIS ». Noms propres translittérés correctement depuis l'hébreu. Les sociétés, marques et lieux s'écrivent sous leur nom officiel en caractères latins, tel qu'il apparaît dans la presse internationale (ישראייר → Israir, קופיקס → Cofix, שופרסל → Shufersal, מחנה יהודה → Mahane Yehuda) : jamais une translittération approximative ou inventée.
- Si l'article ne contient pas assez de matière sur le parcours économique de la personne, mets `pertinent` à false.

Le carrousel compte exactement 9 slides, chaque slide se comprend seule :
1. Couverture (`type` = "cover") : elle portera la photo de la personne. `kicker` = « PORTRAIT ». `titre` : 50 caractères maximum, le nom de la personne puis son parcours résumé en « avant → après » chiffré, tiré des faits ci-dessous : « Rami Levy : de 38 m² à 6 Md₪ », « Omri Casspi : de la NBA à 100 M$ investis ». Si l'article ne donne pas deux chiffres pour cela, le nom suivi du chiffre le plus frappant (« Gil Shwed : 4 Md$ de chiffre d'affaires »). Jamais un titre sans chiffre ni une formule creuse (« l'homme fort de… »). Autres champs vides.
2. à 8. Exactement SEPT faits marquants de sa vie et de sa carrière (`type` = "point"), dans l'ordre chronologique, chacun un moment fort qu'on a envie de raconter (un départ, une création, un exploit, un rachat, un record) : `chiffre` (10 caractères maximum) : un MIX de chiffres et de dates clés. Au moins QUATRE des sept faits portent le CHIFFRE marquant du moment (montant d'un rachat ou d'une levée, chiffre d'affaires, fortune, nombre de magasins ou de salariés, part de marché, record), écrit à la française (1,2 Md$, 50 magasins, 6 Md₪, 45 %) ; les autres portent l'année clé de l'étape (1976), au plus TROIS slides avec une année. Quand le chiffre est affiché, l'année va dans `titre` ou `texte` (« En 2021, … »). Le chiffre affiché doit figurer tel quel dans `texte` ou s'en déduire directement. `titre` (40 caractères maximum), `texte` (120 caractères maximum), `tendance` ("hausse", "baisse" ou "neutre"), `kicker` vide.
9. Fin (`type` = "cta") : `titre` = « L'actualité économique d'Israël, en français », `texte` = « Suivez @bizhakesef et partagez ce post à ceux qui suivent l'économie israélienne. ».
Pas d'emoji dans les slides. Citations entre « ».

La légende (`caption`), 400 à 900 caractères, sans hashtags :
- Première ligne : « Portrait : » suivi du nom et du fait le plus marquant de son parcours, 125 caractères maximum.
- Deux ou trois paragraphes courts : le parcours, puis ce qu'il représente pour l'économie israélienne. Un ou deux emojis sobres.
- Puis une ligne d'appel : « ✉️ Envoyez ce post à quelqu'un qui … », en nommant le public que ce parcours inspirera ou intéressera (par exemple « qui rêve de créer son entreprise en Israël », « qui fait ses courses chez Rami Levy »).
- Puis « Suivez @bizhakesef : un portrait chaque jeudi. », puis « 📰 Source : Wikipédia ».

Autres champs : `hashtags` 3 à 5, du plus spécifique au plus large, toujours #EconomieIsraelienne et #Portrait ; `verification` : une entrée par chiffre ou date, « <chiffre> ← <extrait exact de l'article, dans sa langue> » ; `format_recommande` "reel" ; `interet_grand_public` : note de 1 à 5 suivie d'un tiret et d'une raison de 10 mots maximum (5 : personne connue de tout Israël ou d'une marque du quotidien, 3 : connue du monde des affaires, 1 : peu connue) ; `source_nom` « Wikipédia » ; `categorie` « PORTRAIT » ; `raison_si_non_pertinent` vide si pertinent.

ARTICLE WIKIPÉDIA (« {titre} ») :
"""

def wiki_text(sujet):
    lang, title = sujet.split(":", 1) if sujet[:3] in ("he:", "fr:") else ("en", sujet)
    u = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "prop": "extracts", "explaintext": 1, "redirects": 1, "titles": title,
        "format": "json", "formatversion": 2})
    with urllib.request.urlopen(urllib.request.Request(u, headers=R.UA), timeout=30) as r:
        pg = json.load(r)["query"]["pages"][0]
    titre = pg.get("title", title)
    return titre, (pg.get("extract") or "")[:14000], f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(titre.replace(" ", "_"))


def photo_personne(sujet, titre, url):
    """Photo de la personne pour la couverture : l'image principale de son article Wikipédia, à défaut une recherche."""
    lang = url.split("//")[1].split(".")[0]
    try:
        u = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
            "action": "query", "prop": "pageimages", "piprop": "thumbnail", "pithumbsize": 1080, "redirects": 1,
            "titles": titre, "format": "json", "formatversion": 2})
        with urllib.request.urlopen(urllib.request.Request(u, headers=R.UA), timeout=30) as r:
            th = (json.load(r)["query"]["pages"][0].get("thumbnail") or {}).get("source")
        if th:
            return th
    except Exception as e:
        print("photo Wikipédia indisponible :", e)
    nom = titre if lang == "en" else sujet.split(":", 1)[-1]
    c = R.cover_candidates({"nom": nom, "wikipedia": titre if lang == "en" else "", "type": "personne",
                            "recherche_photo": nom, "theme_photo": ""}, "", 1, force=True)
    return c[0]["url"] if c else ""


def _valeur(ch):
    """Ordre de grandeur d'un chiffre affiché (6 Md₪ > 162 M₪ > 76 %), pour garder les plus forts dans le reel."""
    m = re.search(r"(\d+(?:[ ,.]\d+)*)\s*(Md|M|k)?", ch or "")
    if not m or re.fullmatch(r"(19|20)\d\d", (ch or "").strip()):
        return -1  # une année ou pas de nombre : passe après les chiffres
    v = float(m.group(1).replace(" ", "").replace(",", "."))
    mult = {"Md": 1e9, "M": 1e6, "k": 1e3}.get(m.group(2) or "", 1)
    return v * mult if any(c in ch for c in "$€₪") else v


JUDIC = (r"amende|condamn|inculp|mise? en examen|enqu[êe]te|proc[èe]s|tribunal|sanction|fraude|corruption|"
         r"accus|prison|plainte|poursuites judiciaires|autorit[ée] de la concurrence|antitrust|scandale|perquisition")


def slides_reel(sl):
    """Indices des slides gardées dans le reel : couverture, les 4 faits aux chiffres les plus forts (ordre conservé), fin."""
    faits = [i for i, x in enumerate(sl) if x.get("type") == "point"]
    # les affaires judiciaires restent dans le carrousel, pas dans le reel (sauf s'il ne reste pas assez de faits)
    judic = lambda x: re.search(JUDIC, " ".join(x.get(k, "") for k in ("titre", "texte", "kicker")), re.I)
    propres = [i for i in faits if not judic(sl[i])]
    faits = propres if len(propres) >= 4 else faits
    top = sorted(faits, key=lambda i: -_valeur(sl[i].get("chiffre")))[:4]
    return [0] + sorted(top) + [len(sl) - 1]


DONE = "portraits-faits"  # portraits déjà envoyés, enregistrés chez Cloudinary


def rediger(sujet, schema):
    titre, texte, url = wiki_text(sujet)
    if len(texte) < 1500:
        print("Article trop court :", titre, len(texte))
        return titre, None, url
    consigne = ""
    for essai in range(3):  # contrôle : titre de couverture court, 5 faits, des chiffres plutôt que des dates
        body = {"contents": [{"parts": [{"text": PROMPT.replace("{titre}", titre) + texte + consigne}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema}}
        d = R.first_ok(R.TEXT_MODELS, body)
        out = json.loads("".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought")))
        if not out.get("pertinent"):
            break
        sl = out["slides"]
        faits = [x for x in sl if x.get("type") == "point"]
        for x in faits:  # un « chiffre » sans nombre (« MVNO ») n'en est pas un
            if not re.search(r"\d", x.get("chiffre") or ""):
                x["chiffre"] = ""
        annees = sum(bool(re.fullmatch(r"(19|20)\d\d", (x.get("chiffre") or "").strip())) for x in faits)
        defauts = []
        if len(sl[0].get("titre", "")) > 55:
            defauts.append(f"le titre de couverture fait {len(sl[0]['titre'])} caractères, 50 au maximum")
        if len(faits) != 7:
            defauts.append(f"il y a {len(faits)} slides de faits, il en faut exactement 7")
        if annees > 3 or sum(bool(x.get("chiffre")) for x in faits) - annees < 4:
            defauts.append("les faits doivent porter des chiffres (montants, nombres, pourcentages) et au plus trois années")
        if not defauts:
            break
        print("Nouvel essai :", " ; ".join(defauts))
        consigne = "\n\nATTENTION, ta proposition précédente ne respectait pas les règles : " + " ; ".join(defauts) + ". Corrige-le."
    if not out.get("pertinent"):
        print("Portrait écarté :", titre, out.get("raison_si_non_pertinent"))
        return titre, None, url
    return titre, out, url


def main():
    schema = json.loads(open("schema.json", encoding="utf-8").read())
    faits = (PC.load_plan(DONE) or {}).get("faits", [])
    force = os.environ.get("PORTRAIT", "").strip()
    today = datetime.date.today().isoformat()
    if force:
        file = [force]
    else:
        dus = [t for d, t in PROGRAMME if t not in faits]  # chaque mercredi, le suivant dans l ordre (les dates sont indicatives)
        if not dus:
            print("Aucun portrait programmé aujourd'hui.")
            return
        file = dus[:1] + [t for t in RESERVE if t not in faits]
    for sujet in file:
        titre, out, url = rediger(sujet, schema)
        faits.append(sujet)  # même forcé : un portrait envoyé en avance ne repasse pas à sa date
        if out:
            PC.save_plan(DONE, {"faits": faits})
            photo = photo_personne(sujet, titre, url)
            out["slides"][0]["photo"] = photo
            try:  # pour le reel : même photo, et seulement la couverture, les 4 faits les plus forts et la fin
                carte = PC.load_plan(R.PORTRAITS) or {}
                carte[out["slides"][0]["titre"]] = {"photo": photo, "garder": slides_reel(out["slides"])}
                PC.save_plan(R.PORTRAITS, carte)
            except Exception as e:
                print("plan du reel non enregistré :", e)
            print("Portrait :", titre, "|", out["slides"][0]["titre"], "| photo :", photo or "aucune")
            tell_make("SAVOIR|" + base64.b64encode(json.dumps(out, ensure_ascii=False).encode()).decode() + "|" + url)
            return
    PC.save_plan(DONE, {"faits": faits})
    raise SystemExit("Aucun portrait exploitable")


if __name__ == "__main__":
    main()
