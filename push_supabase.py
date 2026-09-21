"""A collect.py kimenetét (data/latest.json) felírja a Supabase táblákba.

Csak szerveroldalon fusson (GitHub Actions), mert a service_role kulcsot
használja, ami megkerüli az RLS-t — SOHA ne kerüljön a frontendbe vagy
nyilvános repóba, csak GitHub Secretsbe.

Env változók:
    SUPABASE_URL                pl. https://abcdefgh.supabase.co
    SUPABASE_SERVICE_ROLE_KEY   a projekt service_role kulcsa

A MÁSZ-adatot szándékosan NEM írja be automatikusan: az mindig OCR-ből vagy
kézi bevitelből jön, és a README szerint át kell nézni, mielőtt bárhova
bekerül. Ehelyett a collector_runs táblába egy "needs_review" státuszú sort ír,
hogy lásd a dashboardon, hogy vár rád valami.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402


def rest(sess, table, rows, prefer="resolution=merge-duplicates,return=minimal"):
    if not rows:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }
    r = sess.post(url, headers=headers, json=rows, timeout=30)
    r.raise_for_status()
    return len(rows)


def push_stocks(sess, mszksz_data: dict) -> int:
    rows = [
        {"product": product, "observed_date": date, "stock_kt": value, "source": "MSZKSZ"}
        for product, series in mszksz_data.get("stocks_kt", {}).items()
        for date, value in series.items()
    ]
    return rest(sess, "fuel_stocks", rows)


def push_cover_days(sess, mszksz_data: dict) -> int:
    rows = [
        {"observed_date": date, "cover_days": value}
        for date, value in mszksz_data.get("cover_days", {}).items()
    ]
    return rest(sess, "fuel_cover_days", rows)


def log_run(sess, source: str, status: str, detail: str = "") -> None:
    rest(sess, "collector_runs", [{"source": source, "status": status, "detail": detail[:500]}],
         prefer="return=minimal")


def main() -> None:
    global SUPABASE_URL, SUPABASE_KEY
    try:
        SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
        SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    except KeyError as exc:
        sys.exit(f"Hiányzó környezeti változó: {exc}. Állítsd be GitHub Secretsben.")

    latest_path = config.DATA / "latest.json"
    if not latest_path.exists():
        sys.exit("Nincs data/latest.json — előbb futtasd: python collect.py all")

    data = json.loads(latest_path.read_text(encoding="utf-8"))
    sess = requests.Session()
    exit_code = 0

    for source, payload in data.get("sources", {}).items():
        try:
            if "error" in payload:
                log_run(sess, source, "error", payload["error"])
                print(f"[{source}] gyűjtési hiba, kihagyva: {payload['error']}")
                exit_code = 1
                continue

            if source == "mszksz":
                n_stock = push_stocks(sess, payload)
                n_cover = push_cover_days(sess, payload)
                log_run(sess, source, "ok", f"{n_stock} készletsor, {n_cover} készletnap-sor")
                print(f"[mszksz] {n_stock} készletsor, {n_cover} készletnap-sor felírva")

            elif source == "masz":
                # Szándékosan nem automatikus: lásd a modul docstringjét.
                status = "needs_review" if payload.get("needs_review", True) else "ok"
                log_run(sess, source, status, payload.get("period_title", ""))
                print(f"[masz] kép letöltve, kézi/OCR-ellenőrzésre vár: {payload.get('saved_to')}")

            elif source == "eurostat":
                log_run(sess, source, "ok", "kereszt-ellenőrzésre, nincs saját tábla még")
                print("[eurostat] lekérve (egyelőre nincs Supabase-tábla hozzá)")

            else:
                log_run(sess, source, "skipped", "nincs push-logika ehhez a forráshoz")

        except requests.HTTPError as exc:
            body = exc.response.text[:300] if exc.response is not None else str(exc)
            print(f"[{source}] Supabase HIBA: {body}")
            try:
                log_run(sess, source, "error", body)
            except requests.HTTPError:
                pass  # ha maga a logolás is elhasal, ne akassza meg a futást
            exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

