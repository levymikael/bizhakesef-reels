"""Statistiques @bizhakesef lues directement dans l'API Instagram Graph (remplace Supermetrics).

Sortie : un JSON imprimé entre les balises STATS_JSON_DEBUT / STATS_JSON_FIN (lu dans le journal du workflow),
avec :
  - compte : abonnés, nombre de posts ;
  - audience : villes, pays, âge et sexe des abonnés (Instagram exige au moins 100 abonnés) ;
  - en_ligne : abonnés en ligne par heure (moyenne des derniers jours, heure d'Israël) ;
  - posts : pour chaque post des 30 derniers jours, portée, vues, likes, commentaires, enregistrements, partages,
    et pour les reels la durée moyenne de visionnage (secondes) ;
  - jours : portée, vues du profil et abonnés gagnés par jour sur 30 jours.
Variables : IG_ACCESS_TOKEN
"""
import datetime as dt, json, os, sys

import story  # graph() et identifiant du compte

IG = story.IG


def g(path, **q):
    try:
        return story.graph("GET", path, **q)
    except Exception as e:
        return {"erreur": str(e)}


def demographie(breakdown):
    d = g(f"{IG}/insights", metric="follower_demographics", period="lifetime", metric_type="total_value",
          breakdown=breakdown)
    if "erreur" in d:
        return d
    try:
        res = d["data"][0]["total_value"]["breakdowns"][0]["results"]
    except (KeyError, IndexError):
        return {}
    return dict(sorted(((r["dimension_values"][0], r["value"]) for r in res), key=lambda kv: -kv[1]))


def main():
    out = {"releve": dt.datetime.utcnow().isoformat(timespec="minutes") + "Z"}
    out["compte"] = g(IG, fields="followers_count,media_count,username")
    out["audience"] = {b: demographie(b) for b in ("city", "country", "age", "gender")}
    # Abonnés en ligne par heure : l'API donne les heures du Pacifique (America/Los_Angeles), pas UTC.
    # Moyenne des jours disponibles, convertie en heure d'Israël (creux attendu vers 3-5 h du matin)
    on = g(f"{IG}/insights", metric="online_followers", period="lifetime",
           since=int((dt.datetime.utcnow() - dt.timedelta(days=7)).timestamp()), until=int(dt.datetime.utcnow().timestamp()))
    try:
        from zoneinfo import ZoneInfo
        cumul = {}
        for v in on["data"][0]["values"]:
            for h, n in (v.get("value") or {}).items():
                cumul.setdefault(int(h), []).append(n)
        maintenant = dt.datetime.now(dt.timezone.utc)
        decal = int((maintenant.astimezone(ZoneInfo("Asia/Jerusalem")).utcoffset()
                     - maintenant.astimezone(ZoneInfo("America/Los_Angeles")).utcoffset()).total_seconds() // 3600)
        out["en_ligne"] = {f"{(h + decal) % 24:02d}h": round(sum(l) / len(l)) for h, l in sorted(cumul.items())}
    except Exception as e:
        out["en_ligne"] = {"erreur": str(on.get("erreur") or e)}
    # Posts des 30 derniers jours
    limite = dt.datetime.utcnow() - dt.timedelta(days=30)
    posts = []
    for m in g(f"{IG}/media", fields="id,timestamp,media_product_type,caption", limit=60).get("data", []):
        ts = dt.datetime.strptime(m["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")
        if ts < limite:
            continue
        reel = m.get("media_product_type") == "REELS"
        met = "reach,views,likes,comments,saved,shares" + (",ig_reels_avg_watch_time" if reel else "")
        ins = g(f"{m['id']}/insights", metric=met)
        p = {"id": m["id"], "date": m["timestamp"][:16], "type": m.get("media_product_type"), "titre": (m.get("caption") or "")[:70]}
        for x in ins.get("data", []):
            v = x["values"][0]["value"] if x.get("values") else x.get("total_value", {}).get("value")
            p[x["name"]] = round(v / 1000, 1) if x["name"] == "ig_reels_avg_watch_time" else v  # ms -> s
        if "erreur" in ins:
            p["erreur"] = ins["erreur"]
        posts.append(p)
    out["posts"] = posts
    # Compte, jour par jour
    jours = {}
    debut, fin = int((dt.datetime.utcnow() - dt.timedelta(days=29)).timestamp()), int(dt.datetime.utcnow().timestamp())
    for metric in ("reach", "follower_count"):
        d = g(f"{IG}/insights", metric=metric, period="day", since=debut, until=fin)
        for x in d.get("data", []):
            for v in x.get("values", []):
                jours.setdefault(v["end_time"][:10], {})[metric] = v["value"]
    for metric in ("profile_views", "accounts_engaged", "total_interactions"):
        d = g(f"{IG}/insights", metric=metric, period="day", metric_type="total_value", since=debut, until=fin)
        if "erreur" not in d:
            out.setdefault("total_30j", {})[metric] = (d.get("data") or [{}])[0].get("total_value", {}).get("value")
    out["jours"] = dict(sorted(jours.items()))
    print("STATS_JSON_DEBUT")
    print(json.dumps(out, ensure_ascii=False))
    print("STATS_JSON_FIN")


if __name__ == "__main__":
    main()
