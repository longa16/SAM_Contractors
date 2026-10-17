"""
Synchronisation des offres SAM.gov vers la base de données locale JSON
Stratégie :
  - Fenêtre glissante : récupère les offres publiées depuis la dernière synchro
  - Pagination automatique pour dépasser la limite de 1000/requête
  - Dédoublonnage par noticeId avant insertion
  - Suppression des offres dont la responseDeadLine est dépassée
"""

import os
import json
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("SAM_API_KEY")


DATA_DIR       = Path(__file__).parent / "data"
DB_PATH        = DATA_DIR / "opportunities_db.json"
LAST_SYNC_PATH = DATA_DIR / "last_sync.json"
DATA_DIR.mkdir(exist_ok=True)


PAGE_SIZE = 1000                    
MAX_PAGES = 5                       
VALID_TYPES = {
    "solicitation",
    "combined synopsis/solicitation",
    "combined synopsis-solicitation",
}

# Helpers I/O
def load_db() -> dict:
    """Charge la DB."""
    if DB_PATH.exists():
        try:
            return json.loads(DB_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_db(db: dict) -> None:
    DB_PATH.write_text(
        json.dumps(list(db.values()), indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def load_last_sync() -> datetime:
    """Retourne la date de la dernière synchro."""
    if LAST_SYNC_PATH.exists():
        try:
            payload = json.loads(LAST_SYNC_PATH.read_text(encoding="utf-8"))
            return datetime.fromisoformat(payload["last_sync"])
        except Exception:
            pass
    return datetime.now(tz=timezone.utc) - timedelta(days=1)


def save_last_sync(dt: datetime) -> None:
    LAST_SYNC_PATH.write_text(
        json.dumps({"last_sync": dt.isoformat()}),
        encoding="utf-8"
    )

# Formatage d'une opportunité brute
def format_opp(opp: dict) -> dict:
    oa = opp.get("officeAddress") or {}
    oa_str = f"{oa.get('city','')}, {oa.get('state','')}, {oa.get('zipcode','')}".strip(", ")

    pop = opp.get("placeOfPerformance") or {}
    pop_city  = pop.get("city", {}).get("name", "")  if isinstance(pop.get("city"), dict)  else ""
    pop_state = pop.get("state", {}).get("name", "") if isinstance(pop.get("state"), dict) else ""
    pop_str = f"{pop_city}, {pop_state}".strip(", ")

    links = opp.get("resourceLinks") or []

    return {
        "noticeId":                 str(opp.get("noticeId", "")).strip(),
        "solicitationNumber":       str(opp.get("solicitationNumber", "")).strip(),
        "title":                    opp.get("title", ""),
        "type":                     opp.get("type", ""),
        "description":              opp.get("description", ""),
        "pointOfContact":           opp.get("pointOfContact", []),
        "additionalInfoLink":       str(opp.get("additionalInfoLink", "")),
        "fullParentPathName":       opp.get("fullParentPathName", ""),
        "fullParentPathCode":       opp.get("fullParentPathCode", ""),
        "organizationType":         opp.get("organizationType", ""),
        "officeAddress":            oa_str,
        "naicsCode":                opp.get("naicsCode", ""),
        "classificationCode":       opp.get("classificationCode", ""),
        "typeOfSetAside":           opp.get("typeOfSetAside", ""),
        "typeOfSetAsideDescription":opp.get("typeOfSetAsideDescription", ""),
        "postedDate":               str(opp.get("postedDate", "")),
        "responseDeadLine":         opp.get("responseDeadLine", ""),
        "active":                   str(opp.get("active", "")),
        "placeOfPerformance":       pop_str,
        "resourceLinks":            " | ".join(links),
        "_synced_at":               datetime.now(tz=timezone.utc).isoformat(),
    }

# Fetch SAM.gov avec pagination
def fetch_opportunities(posted_from: str, posted_to: str) -> list[dict]:
    """
    Récupère toutes les offres publiées entre posted_from et posted_to.
    Pagine automatiquement jusqu'à MAX_PAGES ou jusqu'à épuisement des résultats.
    """
    all_opps = []

    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        params = {
            "api_key":    API_KEY,
            "limit":      PAGE_SIZE,
            "offset":     offset,
            "postedFrom": posted_from,
            "postedTo":   posted_to,
            "is_active":  "true",
            "ptype":      "o,k",   # Solicitation + Combined Synopsis/Solicitation
        }
        url = "https://api.sam.gov/opportunities/v2/search?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"    [ERREUR réseau page {page}] {e}")
            break

        if resp.status != 200:
            print(f"    [ERREUR HTTP {resp.status}] Arrêt pagination.")
            break

        batch = data.get("opportunitiesData", [])
        total = data.get("totalRecords", 0)
        print(f"    Page {page+1} : {len(batch)} offres reçues (total SAM = {total})")
        all_opps.extend(batch)

        # Stop si on a tout récupéré
        if len(batch) < PAGE_SIZE or (offset + PAGE_SIZE) >= total:
            break

    return all_opps

# Nettoyage des offres expirées
def purge_expired(db: dict) -> tuple[dict, int]:
    """Supprime les offres dont la responseDeadLine est passée."""
    now = datetime.now(tz=timezone.utc)
    kept = {}
    removed = 0

    for notice_id, opp in db.items():
        deadline_raw = opp.get("responseDeadLine") or ""
        if not deadline_raw:
            kept[notice_id] = opp   # Pas de deadline, on garde
            continue
        try:
            # SAM.gov retourne des dates ISO 8601 ou "YYYY-MM-DDTHH:MM:SS"
            dl = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
            if dl >= now:
                kept[notice_id] = opp
            else:
                removed += 1
        except Exception:
            kept[notice_id] = opp   # Format inconnu, on garde par précaution

    return kept, removed


# Point d'entrée principal
def run_sync() -> None:
    now = datetime.now(tz=timezone.utc)
    last_sync = load_last_sync()

    posted_from = last_sync.strftime("%m/%d/%Y")
    posted_to   = now.strftime("%m/%d/%Y")

    print(f"\n{'='*60}")
    print(f"  SYNC SAM.gov — {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Fenêtre : {posted_from}  →  {posted_to}")
    print(f"{'='*60}")

    # 1. Charger la DB existante
    db = load_db()
    print(f"\n  DB actuelle : {len(db)} offres")

    # 2. Fetch depuis l'API
    print(f"\n  Récupération des nouvelles offres...")
    raw_opps = fetch_opportunities(posted_from, posted_to)
    print(f"  Total brut reçu : {len(raw_opps)} offres")

    # 3. Filtrage + formatage + insertion
    added = 0
    skipped_inactive  = 0
    skipped_type      = 0
    skipped_duplicate = 0

    for opp in raw_opps:
        # Filtre active
        if str(opp.get("active", "")).strip().lower() not in ("yes", "true", "1"):
            skipped_inactive += 1
            continue

        # Filtre type
        opp_type = str(opp.get("type", "")).strip().lower()
        if opp_type not in VALID_TYPES:
            skipped_type += 1
            continue

        # Formatage
        formatted = format_opp(opp)
        notice_id = formatted["noticeId"]
        if not notice_id:
            notice_id = formatted["solicitationNumber"]  # fallback

        # Dédoublonnage : on ne met à jour que si la version est plus récente
        if notice_id in db:
            existing_date = db[notice_id].get("postedDate", "")
            new_date      = formatted["postedDate"]
            if new_date <= existing_date:
                skipped_duplicate += 1
                continue

        db[notice_id] = formatted
        added += 1

    print(f"\n  Insertion : +{added} nouvelles offres")
    print(f"  Ignorées  : {skipped_inactive} inactives | {skipped_type} mauvais type | {skipped_duplicate} déjà en base")

    # 4. Purge des offres expirées
    db, purged = purge_expired(db)
    print(f"  Supprimées (deadline dépassée) : {purged}")
    print(f"\n  DB finale : {len(db)} offres actives")

    # 5. Sauvegarde
    save_db(db)
    save_last_sync(now)
    print(f"\n  ✓ Sauvegarde OK → {DB_PATH}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run_sync()
