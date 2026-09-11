#!/usr/bin/env python3
"""
post_scheduled.py

Publica contenido en HORARIOS FIJOS que tú defines en scheduled_posts.json
(hora de Madrid, CET/CEST — maneja el horario de verano automáticamente).

Cada entrada del JSON tiene una hora y una lista de archivos; a esa hora
se publica UN TWEET POR CADA ARCHIVO de la lista, una sola vez al día.

Ejemplo de scheduled_posts.json:
[
  {"time": "09:00", "files": ["media/buenosdias.mp4"]},
  {"time": "23:30", "files": ["media/video1.mp4", "media/video2.jpg"]}
]

Pensado para correr cada ~10-15 min (ver el workflow), revisando en cada
corrida si alguna entrada "cae" ahora mismo.

Requiere las mismas variables de entorno que post_daily.py:
    TW_API_KEY, TW_API_SECRET, TW_ACCESS_TOKEN, TW_ACCESS_TOKEN_SECRET
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import tweepy

CONFIG_FILE = Path("scheduled_posts.json")
LOG_FILE = Path("scheduled_log.json")
MADRID_TZ = ZoneInfo("Europe/Madrid")

TOLERANCE_MINUTES = 8  # margen de tolerancia (el workflow revisa cada ~10-15 min)

VIDEO_EXTS = {".mp4", ".mov"}


def today_madrid_str() -> str:
    return datetime.now(MADRID_TZ).strftime("%Y-%m-%d")


def minutes_now_madrid() -> int:
    now = datetime.now(MADRID_TZ)
    return now.hour * 60 + now.minute


def parse_time_to_minutes(time_str: str) -> int | None:
    try:
        hh, mm = time_str.strip().split(":")
        return int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        return None


def is_within_window(target_minutes: int) -> bool:
    now_minutes = minutes_now_madrid()
    diff = abs(now_minutes - target_minutes)
    diff = min(diff, 1440 - diff)  # por si cruza medianoche
    return diff <= TOLERANCE_MINUTES


def load_config() -> list[dict]:
    if not CONFIG_FILE.exists():
        print(f"ERROR: no existe '{CONFIG_FILE}'.", file=sys.stderr)
        return []
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            print(f"ERROR: '{CONFIG_FILE}' debe ser una lista de entradas.", file=sys.stderr)
            return []
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR al leer '{CONFIG_FILE}': {e}", file=sys.stderr)
        return []


def load_log() -> dict:
    """dict {'HH:MM': 'YYYY-MM-DD'} -> última fecha en que se publicó esa entrada."""
    if not LOG_FILE.exists():
        return {}
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_log(data: dict) -> None:
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)


def build_clients():
    api_key = os.environ["TW_API_KEY"]
    api_secret = os.environ["TW_API_SECRET"]
    access_token = os.environ["TW_ACCESS_TOKEN"]
    access_token_secret = os.environ["TW_ACCESS_TOKEN_SECRET"]

    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_token_secret)
    api_v1 = tweepy.API(auth)
    client_v2 = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_token_secret,
    )
    return api_v1, client_v2


def upload_media(api_v1: tweepy.API, filepath: Path) -> str:
    if filepath.suffix.lower() in VIDEO_EXTS:
        media = api_v1.media_upload(
            filename=str(filepath), chunked=True, media_category="amplify_video"
        )
    else:
        media = api_v1.media_upload(filename=str(filepath))
    return media.media_id_string


def post_file(api_v1, client_v2, filepath: Path) -> bool:
    if not filepath.exists():
        print(f"ERROR: el archivo '{filepath}' no existe. Se salta.", file=sys.stderr)
        return False
    try:
        media_id = upload_media(api_v1, filepath)
        response = client_v2.create_tweet(media_ids=[media_id])
        print(f"[OK] Publicado {filepath}: {response.data}")
        return True
    except Exception as e:
        print(f"ERROR al publicar {filepath}: {e}", file=sys.stderr)
        return False


def main() -> int:
    entries = load_config()
    if not entries:
        print("[INFO] No hay entradas configuradas en scheduled_posts.json.")
        return 0

    log = load_log()
    today = today_madrid_str()
    now_minutes = minutes_now_madrid()

    api_v1 = client_v2 = None  # se crean solo si hace falta publicar algo
    any_success = False

    for entry in entries:
        time_str = entry.get("time", "")
        files = entry.get("files", [])
        target_minutes = parse_time_to_minutes(time_str)

        if target_minutes is None:
            print(f"[WARN] Entrada con hora inválida, se ignora: {entry}")
            continue

        if log.get(time_str) == today:
            continue  # esta entrada ya se publicó hoy

        if not is_within_window(target_minutes):
            continue  # todavía no es la hora de esta entrada

        print(f"[INFO] Es hora de la entrada '{time_str}' ({len(files)} archivo(s)).")

        if api_v1 is None:
            api_v1, client_v2 = build_clients()

        entry_ok = True
        for filename in files:
            ok = post_file(api_v1, client_v2, Path(filename))
            entry_ok = entry_ok and ok

        if entry_ok:
            log[time_str] = today
            any_success = True
        else:
            print(f"[WARN] La entrada '{time_str}' tuvo errores, se reintentará en la próxima corrida.")

    if any_success:
        save_log(log)
    else:
        print(f"[INFO] Nada que publicar ahora mismo (hora actual Madrid: "
              f"{now_minutes // 60:02d}:{now_minutes % 60:02d}).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
