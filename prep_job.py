"""Normalise la commande de montage reçue de Make.
Make envoie désormais la colonne « Médias » du Sheet telle quelle (« medias », texte JSON {"files": [...]})
au lieu de la liste des slides : cela économise un module, donc un crédit Make, à chaque montage."""
import json, sys

p = sys.argv[1]
j = json.load(open(p, encoding="utf-8"))
if not j.get("slides") and j.get("medias"):
    m = j.pop("medias")
    m = json.loads(m) if isinstance(m, str) else m
    j["slides"] = [f.get("image_url") or f.get("httpUrl") for f in m.get("files", [])]
json.dump(j, open(p, "w", encoding="utf-8"), ensure_ascii=False)
print(j["id"], len(j["slides"]), "slides")
