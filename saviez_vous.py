"""Série « Le saviez-vous ? » de @bizhakesef : 3 fois par semaine, un fait marquant sur l'économie israélienne,
tiré d'un article Wikipédia (source vérifiable : l'IA n'ajoute rien de mémoire).

Choisit un sujet pas encore traité, fait rédiger le carrousel par Gemini (même format que les carrousels d'actualité),
puis l'envoie au scénario Make (« SAVOIR|<json base64> ») qui l'enregistre et lance la création des slides :
l'aperçu arrive sur Telegram avec les boutons REEL / Carrousel / Rejeter, comme un article.

Variables : GEMINI_API_KEY, CLOUDINARY_*, MAKE_HOOK_URL, ADMIN_CHAT_ID
"""
import base64, json, random, sys, urllib.parse, urllib.request

import render_reel as R
from notify import tell_make
import photo_choice as PC

SUJETS = [
    "Economy of Israel", "Tel Aviv Stock Exchange", "Bank of Israel", "Israeli new shekel", "Israeli shekel (1980–1985)",
    "1985 Israel Economic Stabilization Plan", "Israeli bank stock crisis of 1983", "Yozma", "Israel Innovation Authority",
    "Silicon Wadi", "Leviathan gas field", "Tamar gas field", "Natural gas in Israel", "Sorek desalination plant",
    "Water supply and sanitation in Israel", "Netafim", "Mobileye", "Waze", "Check Point", "Teva Pharmaceuticals",
    "Elbit Systems", "Israel Aerospace Industries", "ICL Group", "Dead Sea Works", "Israel Diamond Exchange",
    "Port of Haifa", "Port of Ashdod", "Ben Gurion Airport", "El Al", "Israel Railways", "Israel Electric Corporation",
    "Bank Leumi", "Bank Hapoalim", "Histadrut", "Kibbutz", "Tnuva", "Strauss Group", "Osem", "SodaStream", "Wix.com",
    "Fiverr", "Monday.com", "Intel Israel", "M-Systems", "ICQ", "Mirabilis (company)", "Given Imaging", "Iscar",
    "Delek Group", "Shufersal", "Bezeq", "Israel Securities Authority", "Israel Central Bureau of Statistics",
    "Tourism in Israel", "Agriculture in Israel", "Science and technology in Israel", "Israel Chemicals",
    "Ormat Technologies", "Elbit Imaging", "Paz Oil Company", "Ofer Brothers Group", "Azrieli Group", "Azrieli Center",
    "Israel Military Industries", "Rafael Advanced Defense Systems", "Unit 8200", "Weizmann Institute of Science",
    "Technion – Israel Institute of Technology", "Mellanox Technologies", "Wiz (company)", "CyberArk",
]

PROMPT = """Tu es le rédacteur en chef de @bizhakesef, un média Instagram d'information en français sur l'économie israélienne. Ton : factuel, précis, neutre, sans sensationnalisme, sans tutoiement, sans question au lecteur.

Tu prépares un post de la série « Le saviez-vous ? » à partir de l'article Wikipédia ci-dessous : UN fait surprenant, utile ou méconnu sur l'économie israélienne, qu'on a envie d'envoyer à quelqu'un ou d'enregistrer.

Règles des faits :
- Uniquement les faits de l'article ci-dessous. Aucun chiffre, aucune date, aucun nom ajouté de mémoire.
- Préfère un fait durable (histoire, record, invention, ordre de grandeur structurel) à un chiffre qui vieillit vite. Tout chiffre est daté (« en 2019 ») si l'article le date.
- Garde la devise d'origine, montants à la française (670 M$, 1,8 Md$, 45 %). Noms d'institutions sous leur forme française usuelle.
- Si l'article ne contient aucun fait vraiment intéressant sur l'économie israélienne, mets `pertinent` à false.

Le carrousel, 5 à 7 slides, chaque slide se comprend seule :
1. Couverture (`type` = "cover") : `kicker` = « LE SAVIEZ-VOUS ? ». `titre` : le fait, 55 caractères maximum, qui donne envie de lire la suite (affirmation, pas de question). Autres champs vides.
2. Faits (`type` = "point"), du plus marquant au moins marquant : `chiffre` (10 caractères maximum, ou vide), `titre` (40 caractères maximum), `texte` (120 caractères maximum), `tendance` ("hausse", "baisse" ou "neutre"), `kicker` vide.
3. « À RETENIR » (`type` = "point", avant-dernière) : `kicker` = « À RETENIR », `titre` vide, `texte` (150 caractères maximum) : ce que ce fait dit de l'économie israélienne aujourd'hui, à partir de l'article. `chiffre` vide, `tendance` "neutre".
4. Fin (`type` = "cta") : `titre` = « L'actualité économique d'Israël, en français », `texte` = « Suivez @bizhakesef et partagez ce post à ceux qui suivent l'économie israélienne. ».
Pas d'emoji dans les slides. Citations entre « ».

La légende (`caption`), 400 à 900 caractères, sans hashtags :
- Première ligne : « Le saviez-vous ? » suivi du fait, 125 caractères maximum, avec le nom de l'entreprise, de l'institution ou du secteur.
- Deux ou trois paragraphes courts : le fait, puis son contexte. Un ou deux emojis sobres.
- Puis : « 📌 Enregistrez ce post pour le retrouver. », puis « Suivez @bizhakesef : Le saviez-vous ? revient chaque lundi. », puis « 📰 Source : Wikipédia ».

Autres champs : `hashtags` 3 à 5, du plus spécifique au plus large, toujours #EconomieIsraelienne et #LeSaviezVous ; `verification` : une entrée par chiffre ou date, « <chiffre> ← <extrait exact de l'article, en anglais> » ; `format_recommande` "reel" ; `interet_grand_public` « 4 — fait marquant et durable sur l'économie israélienne » (adapte la note et la raison) ; `source_nom` « Wikipédia » ; `categorie` « LE SAVIEZ-VOUS ? » ; `raison_si_non_pertinent` vide si pertinent.

ARTICLE WIKIPÉDIA (« {titre} ») :
"""

USED = "saviez-vous-sujets"  # liste des sujets déjà traités, enregistrée chez Cloudinary


def wiki_text(title):
    u = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "prop": "extracts", "explaintext": 1, "redirects": 1, "titles": title,
        "format": "json", "formatversion": 2})
    with urllib.request.urlopen(urllib.request.Request(u, headers=R.UA), timeout=30) as r:
        pg = json.load(r)["query"]["pages"][0]
    return pg.get("title", title), (pg.get("extract") or "")[:14000]


def main():
    schema = json.loads(open("schema.json", encoding="utf-8").read())
    used = (PC.load_plan(USED) or {}).get("sujets", [])
    libres = [s for s in SUJETS if s not in used] or SUJETS[:]
    random.shuffle(libres)
    for sujet in libres[:4]:  # si un article ne donne rien, on passe au suivant
        titre, texte = wiki_text(sujet)
        if len(texte) < 1500:
            continue
        body = {"contents": [{"parts": [{"text": PROMPT.replace("{titre}", titre) + texte}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema}}
        d = R.first_ok(R.TEXT_MODELS, body)
        out = json.loads("".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought")))
        used.append(sujet)
        if out.get("pertinent"):
            PC.save_plan(USED, {"sujets": used})
            url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(titre.replace(" ", "_"))
            print("Sujet :", titre, "|", out["slides"][0]["titre"])
            tell_make("SAVOIR|" + base64.b64encode(json.dumps(out, ensure_ascii=False).encode()).decode() + "|" + url)
            return
        print("Sujet écarté :", titre, out.get("raison_si_non_pertinent"))
    PC.save_plan(USED, {"sujets": used})
    raise SystemExit("Aucun sujet exploitable trouvé")


if __name__ == "__main__":
    main()
