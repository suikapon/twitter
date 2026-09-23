#!/usr/bin/env python3
"""
post_daily.py

Publica automáticamente en X (Twitter) un archivo AL AZAR (imagen o video)
de la carpeta `media/`, alternando categorías, y ADEMÁS publica de forma
INDEPENDIENTE en YouTube, TikTok e Instagram según su propia
configuración de reparto (ver platform_config.json).

Uso:
    python post_daily.py

Requiere las siguientes variables de entorno:
    TW_API_KEY, TW_API_SECRET, TW_ACCESS_TOKEN, TW_ACCESS_TOKEN_SECRET
    (opcional) TWEET_TEXT_TEMPLATE

    (opcional, YouTube Shorts) YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN

    (opcional, TikTok e Instagram vía Zernio)
    ZERNIO_API_KEY
    ZERNIO_TIKTOK_ACCOUNT_ID
    ZERNIO_INSTAGRAM_ACCOUNT_ID
    (opcional) TIKTOK_PRIVACY_LEVEL -> por defecto "PUBLIC_TO_EVERYONE"

    NOTA: TikTok e Instagram necesitan descargar el archivo desde una
    URL pública. Este script arma automáticamente la URL raw de GitHub,
    lo cual SOLO funciona si el repositorio es público.

CONFIGURACIÓN DE TIKTOK/INSTAGRAM: ver platform_config.json en la raíz
del repo. Ahí defines, por red social, cuántos posts al día quieres
(daily_count) y qué porcentaje de cada categoría (weights, no hace
falta que sumen 100, se normalizan solos). Edita ese archivo cuando
quieras cambiar el reparto -- no hace falta tocar este script.
"""

import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import tweepy

MEDIA_DIR = Path("media")
LOG_FILE = Path("posted_log.json")
ROTATION_FILE = Path("rotation_state.json")
TWITTER_LOG_FILE = Path("twitter_log.json")
YOUTUBE_LOG_FILE = Path("youtube_log.json")
TIKTOK_LOG_FILE = Path("tiktok_log.json")
INSTAGRAM_LOG_FILE = Path("instagram_log.json")
PLATFORM_CONFIG_FILE = Path("platform_config.json")
HIT_POSTS_FILE = Path("hit_posts.json")
HIT_LOG_FILE = Path("hit_log.json")

# Ciclo fijo para Twitter: una publicación de la primera categoría,
# luego la segunda, luego la tercera, y vuelta a empezar.
CATEGORIES = ["deltarune", "shitpost", "touhou", "DarkOddCon", "capturas"]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTS = {
    ".mp4", ".mov", ".avi", ".wmv", ".flv", ".webm",
    ".mkv", ".m4v", ".3gp", ".3g2", ".mpg", ".mpeg", ".ts",
}
YOUTUBE_VIDEO_EXTS = {".mp4"}  # YouTube solo recibe mp4, aunque Twitter acepte más formatos

YOUTUBE_TITLES = {
    "deltarune": "Deltarune #shorts #deltarune",
    "shitpost": "Shitpost #shorts #memes",
    "touhou": "Touhou #shorts #touhou",
    "DarkOddCon": "DarkOddCon #shorts",
    "capturas": "Capturas #shorts",
}
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS

# --- Zernio (TikTok / Instagram) ---------------------------------------
ZERNIO_API_BASE = "https://zernio.com/api/v1"

TIKTOK_VIDEO_EXTS = {".mp4", ".mov", ".webm"}
TIKTOK_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

INSTAGRAM_VIDEO_EXTS = {".mp4", ".mov"}
INSTAGRAM_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}

TIKTOK_CAPTIONS = {
    "deltarune": "Deltarune #deltarune #fyp",
    "shitpost": "Shitpost #memes #fyp",
    "touhou": "Touhou #touhou #fyp",
    "DarkOddCon": "DarkOddCon #fyp",
    "capturas": "Capturas #fyp",
}
INSTAGRAM_CAPTIONS = {
    "deltarune": "Deltarune #deltarune",
    "shitpost": "Shitpost #memes",
    "touhou": "Touhou #touhou",
    "DarkOddCon": "DarkOddCon",
    "capturas": "Capturas",
}


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def days_since(date_str: str) -> int:
    """Días transcurridos desde date_str (YYYY-MM-DD) hasta hoy, en UTC."""
    try:
        posted_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 9999
    return (datetime.now(timezone.utc) - posted_date).days


def load_posted_log() -> dict:
    """
    {ruta_relativa: "YYYY-MM-DD"} con la fecha (UTC) de la última vez
    que se publicó cada archivo (en CUALQUIER red social -- el cooldown
    es compartido para no repetir el mismo meme en todos lados).
    """
    if not LOG_FILE.exists():
        return {}
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("posted", {})
        if isinstance(raw, dict):
            return raw
        print("[WARN] posted_log.json tenía un formato viejo/inválido, se reinicia.")
        return {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] No se pudo leer posted_log.json ({e}), se reinicia el registro.")
        return {}


def save_posted_log(posted: dict) -> None:
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump({"posted": posted}, f, indent=2, ensure_ascii=False, sort_keys=True)


def load_rotation_state() -> str:
    if ROTATION_FILE.exists():
        try:
            with open(ROTATION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cat = data.get("next_category")
            if cat in CATEGORIES:
                return cat
        except (json.JSONDecodeError, OSError):
            pass
    return CATEGORIES[0]


def save_rotation_state(next_category: str) -> None:
    with open(ROTATION_FILE, "w", encoding="utf-8") as f:
        json.dump({"next_category": next_category}, f, indent=2, ensure_ascii=False)


def list_media_in_category(category: str) -> list:
    folder = MEDIA_DIR / category
    if not folder.exists():
        return []
    return [
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    ]


def list_all_media() -> list:
    return [
        p for p in MEDIA_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    ]


def file_key(p: Path) -> str:
    return str(p.relative_to(MEDIA_DIR))


REPEAT_COOLDOWN_DAYS = 30

# None = nunca se repite (una vez publicado, queda excluido para siempre).
CATEGORY_COOLDOWN_DAYS = {
    "deltarune": 30,
    "shitpost": None,
    "touhou": 30,
    "DarkOddCon": 30,
    "capturas": 30,
}


def available_in_category(category: str, posted: dict, allowed_exts: set | None = None) -> list:
    """Archivos de una categoría que respetan su cooldown (y opcionalmente una extensión)."""
    files = list_media_in_category(category)
    if allowed_exts:
        files = [p for p in files if p.suffix.lower() in allowed_exts]

    cooldown = CATEGORY_COOLDOWN_DAYS.get(category, REPEAT_COOLDOWN_DAYS)
    if cooldown is None:
        return [p for p in files if file_key(p) not in posted]
    return [p for p in files if days_since(posted.get(file_key(p), "2000-01-01")) >= cooldown]


def pick_next_file(posted: dict, required_exts: set | None = None) -> tuple:
    """
    Para TWITTER: elige un archivo siguiendo el ciclo fijo de CATEGORIES
    (en orden, volviendo al inicio al llegar al final), cayendo a la
    siguiente categoría del ciclo si la que toca no tiene candidatos.
    """
    preferred = load_rotation_state()
    if preferred not in CATEGORIES:
        preferred = CATEGORIES[0]
    start_idx = CATEGORIES.index(preferred)
    ordered_categories = [CATEGORIES[(start_idx + i) % len(CATEGORIES)] for i in range(len(CATEGORIES))]

    for category in ordered_categories:
        candidates = available_in_category(category, posted, required_exts)
        if candidates:
            return random.choice(candidates), category

    return None, None


# ---------------------------------------------------------------------
# Configuración de reparto por red social (platform_config.json)
# ---------------------------------------------------------------------

DEFAULT_PLATFORM_CONFIG = {
    "twitter": {"daily_count": 32},
    "youtube": {"daily_count": 6, "weights": {c: 1 for c in CATEGORIES}},
    "tiktok": {"daily_count": 10, "weights": {c: 1 for c in CATEGORIES}},
    "instagram": {"daily_count": 10, "weights": {c: 1 for c in CATEGORIES}},
}


def load_platform_config() -> dict:
    """
    Lee platform_config.json. Si falta, está corrupto, o le falta algún
    campo, se completa con valores por defecto en vez de fallar.
    """
    config = {k: dict(v) for k, v in DEFAULT_PLATFORM_CONFIG.items()}
    if PLATFORM_CONFIG_FILE.exists():
        try:
            with open(PLATFORM_CONFIG_FILE, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            for platform, settings in user_config.items():
                config.setdefault(platform, {"daily_count": 10, "weights": {c: 1 for c in CATEGORIES}})
                if "daily_count" in settings:
                    config[platform]["daily_count"] = settings["daily_count"]
                if "weights" in settings and settings["weights"]:
                    config[platform]["weights"] = settings["weights"]
        except (json.JSONDecodeError, OSError) as e:
            print(f"[WARN] No se pudo leer {PLATFORM_CONFIG_FILE} ({e}), se usan valores por defecto.")
    return config


def weighted_pick_category(weights: dict) -> str:
    cats = list(weights.keys())
    w = [max(0, float(weights[c])) for c in cats]
    if sum(w) <= 0:
        return random.choice(cats)
    return random.choices(cats, weights=w, k=1)[0]


def pick_file_for_platform(posted: dict, weights: dict, allowed_exts: set) -> tuple:
    """
    Elige un archivo para una red social según sus pesos por categoría
    (ej. {"deltarune": 50, "touhou": 30, "shitpost": 20}). Si la
    categoría sorteada no tiene contenido disponible, se reintenta con
    las categorías restantes (recalculando pesos) hasta encontrar algo
    o agotarlas todas.
    """
    remaining = dict(weights)
    while remaining:
        category = weighted_pick_category(remaining)
        candidates = available_in_category(category, posted, allowed_exts)
        if candidates:
            return random.choice(candidates), category
        del remaining[category]
    return None, None


# ---------------------------------------------------------------------
# Tweets con texto propio (hit_posts.json)
# ---------------------------------------------------------------------
# Formato de hit_posts.json: una lista de entradas
#   {"image": "media/hit/algo.jpg", "text": "..."}   -> con imagen
#   {"image": null, "text": "..."}                    -> solo texto
# Cada entrada se publica como máximo UNA vez (se registra en
# hit_log.json). Para agregar más, solo edita hit_posts.json.

def load_hit_posts() -> list:
    if not HIT_POSTS_FILE.exists():
        return []
    try:
        with open(HIT_POSTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] No se pudo leer {HIT_POSTS_FILE} ({e}).")
        return []


def load_hit_log() -> dict:
    if HIT_LOG_FILE.exists():
        try:
            with open(HIT_LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("used_indices", [])
            return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"used_indices": []}


def save_hit_log(data: dict) -> None:
    with open(HIT_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def pick_hit_entry() -> tuple:
    """
    Elige al azar una entrada no usada de hit_posts.json. Devuelve
    (índice, entrada) o (None, None) si no queda ninguna disponible.
    """
    entries = load_hit_posts()
    if not entries:
        return None, None
    log = load_hit_log()
    used = set(log.get("used_indices", []))
    available = [i for i in range(len(entries)) if i not in used]
    if not available:
        return None, None
    idx = random.choice(available)
    return idx, entries[idx]


def mark_hit_entry_used(idx: int) -> None:
    log = load_hit_log()
    used = set(log.get("used_indices", []))
    used.add(idx)
    log["used_indices"] = sorted(used)
    save_hit_log(log)


def pick_twitter_content(posted: dict, weights: dict, force_video: bool = False) -> dict | None:
    """
    Elige qué publicar en Twitter esta vez, sorteando por peso entre las
    categorías normales y, si "hit" está en los pesos, también entre los
    tweets con texto propio de hit_posts.json.

    hit_posts.json admite "image" (una sola, string o null) o "images"
    (lista de 2 a 4, todas imágenes -- Twitter no permite mezclar video
    con varias imágenes en el mismo tweet).

    Si force_video=True (2 imágenes seguidas ya publicadas), se excluyen
    las entradas de imagen(es) -- "hit" solo se considera si trae video
    único o no trae imagen (texto solo).

    Devuelve un dict {"text": str, "image_paths": list[Path],
    "category": str, "hit_index": int|None}, o None si no hay nada
    disponible en absoluto.
    """
    remaining = dict(weights)
    while remaining:
        category = weighted_pick_category(remaining)

        if category == "hit":
            idx, entry = pick_hit_entry()
            if entry is not None:
                image_paths: list = []

                if entry.get("images"):
                    raw_paths = [Path(p) for p in entry["images"]]
                    if not (2 <= len(raw_paths) <= 4) or any(p.suffix.lower() not in IMAGE_EXTS for p in raw_paths):
                        print(f"[WARN] hit #{idx}: 'images' debe tener 2-4 rutas, todas de imagen. Se omite esta entrada.")
                        del remaining["hit"]
                        continue
                    if force_video:
                        # Son varias imágenes -- no sirve para forzar video, se prueba otra categoría.
                        del remaining["hit"]
                        continue
                    image_paths = raw_paths

                elif entry.get("image"):
                    candidate = Path(entry["image"])
                    is_img = candidate.suffix.lower() in IMAGE_EXTS
                    if force_video and is_img:
                        del remaining["hit"]
                        continue
                    image_paths = [candidate]

                # Si no trae "images" ni "image", es un tweet de solo texto (image_paths queda vacío).
                return {
                    "text": entry.get("text", ""),
                    "image_paths": image_paths,
                    "category": "hit",
                    "hit_index": idx,
                }
            del remaining["hit"]
            continue

        allowed_exts = VIDEO_EXTS if force_video else None
        candidates = available_in_category(category, posted, allowed_exts)
        if candidates:
            chosen = random.choice(candidates)
            return {"text": "", "image_paths": [chosen], "category": category, "hit_index": None}
        del remaining[category]

    return None



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
    is_video = filepath.suffix.lower() in VIDEO_EXTS
    if is_video:
        media = api_v1.media_upload(
            filename=str(filepath), chunked=True, media_category="amplify_video"
        )
    else:
        media = api_v1.media_upload(filename=str(filepath))
    return media.media_id_string


# ---------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------

def youtube_credentials_available() -> bool:
    return all(os.environ.get(v) for v in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))


def upload_to_youtube(filepath: Path, title: str) -> str:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {"title": title[:100], "description": f"{title}\n\n#Shorts", "categoryId": "24"},
        "status": {"privacyStatus": "public"},
    }
    media = MediaFileUpload(str(filepath), chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]


def hours_since(iso_str) -> float:
    if not iso_str:
        return 9999
    try:
        last_dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return 9999
    return (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600


def maybe_post_to_youtube(posted: dict) -> None:
    """
    YouTube elige su PROPIO archivo (independiente de Twitter, TikTok e
    Instagram), según los pesos de categoría y la cantidad diaria de
    platform_config.json. Solo se consideran archivos .mp4 (único
    formato que sube este script a YouTube).
    """
    if not youtube_credentials_available():
        print("[INFO] YouTube: faltan credenciales, se omite.")
        return

    config = load_platform_config().get("youtube", {})
    daily_count = config.get("daily_count", 6)
    weights = config.get("weights", {c: 1 for c in CATEGORIES})

    yt_log = load_platform_log(YOUTUBE_LOG_FILE)
    if yt_log["count"] >= daily_count:
        print(f"[INFO] YouTube: cupo diario alcanzado ({yt_log['count']}/{daily_count}).")
        return

    min_hours = spacing_hours_for(daily_count)
    hs = hours_since(yt_log.get("last_upload_utc"))
    if hs < min_hours:
        print(f"[INFO] YouTube: última subida hace {hs:.1f}h, faltan {min_hours - hs:.1f}h (reparto para {daily_count}/día).")
        return

    filepath, category = pick_file_for_platform(posted, weights, YOUTUBE_VIDEO_EXTS)
    if filepath is None:
        print("[INFO] YouTube: no hay ningún .mp4 disponible ahora mismo según los pesos configurados.")
        return

    try:
        title = YOUTUBE_TITLES.get(category, category)
        video_id = upload_to_youtube(filepath, title)
        print(f"[OK] Subido a YouTube ({category}): {filepath} -> https://youtu.be/{video_id}")
        yt_log["count"] += 1
        yt_log["last_upload_utc"] = datetime.now(timezone.utc).isoformat()
        save_platform_log(YOUTUBE_LOG_FILE, yt_log)
        posted[file_key(filepath)] = today_str()
        save_posted_log(posted)
    except Exception as e:
        print(f"[WARN] Falló la subida a YouTube: {e}")


# ---------------------------------------------------------------------
# Zernio: helpers comunes (TikTok / Instagram)
# ---------------------------------------------------------------------

def zernio_credentials_available() -> bool:
    return bool(os.environ.get("ZERNIO_API_KEY"))


def build_public_media_url(filepath: Path) -> str:
    """URL pública 'raw' de GitHub del archivo (requiere repo público)."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY no está definido (¿corriendo fuera de GitHub Actions?)")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{filepath.as_posix()}"


def zernio_post(platform: str, account_id: str, api_key: str, media_url: str,
                 media_type: str, caption: str, extra_settings: dict | None = None) -> str:
    """POST /v1/posts genérico de Zernio. Devuelve un string con el resultado."""
    payload = {
        "content": caption,
        "mediaItems": [{"type": media_type, "url": media_url}],
        "platforms": [{"platform": platform, "accountId": account_id}],
        "publishNow": True,
    }
    if extra_settings:
        payload[f"{platform}Settings"] = extra_settings

    resp = requests.post(
        f"{ZERNIO_API_BASE}/posts",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise RuntimeError(f"Respuesta inesperada de Zernio ({resp.status_code}): {resp.text[:300]}")

    if resp.status_code not in (200, 201, 207):
        raise RuntimeError(f"Zernio devolvió {resp.status_code}: {data}")

    platforms = data.get("post", {}).get("platforms", [])
    entry = next((p for p in platforms if p.get("platform") == platform), None)
    if entry is None:
        raise RuntimeError(f"Respuesta de Zernio sin entrada de {platform}: {data}")

    status = entry.get("status")
    if status == "failed":
        raise RuntimeError(entry.get("errorMessage", f"Fallo desconocido al publicar en {platform}"))
    return f"{status} ({media_type})"


def load_platform_log(log_file: Path) -> dict:
    """{'date': 'YYYY-MM-DD', 'count': N, 'last_upload_utc': ISO8601 o None}"""
    today = today_str()
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == today:
                data.setdefault("count", 0)
                data.setdefault("last_upload_utc", None)
                return data
            return {"date": today, "count": 0, "last_upload_utc": data.get("last_upload_utc")}
        except (json.JSONDecodeError, OSError):
            pass
    return {"date": today, "count": 0, "last_upload_utc": None}


def save_platform_log(log_file: Path, data: dict) -> None:
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def spacing_hours_for(daily_count: int) -> float:
    """Horas mínimas entre posts para repartir 'daily_count' posts a lo largo de 24h."""
    if daily_count <= 0:
        return 9999
    return 24.0 / daily_count


def load_twitter_log() -> dict:
    """
    Igual que load_platform_log(TWITTER_LOG_FILE), pero conservando
    'recent_types' (los últimos tipos publicados: 'image'/'video') aunque
    cambie el día -- esa memoria no debe reiniciarse a medianoche.
    """
    data = load_platform_log(TWITTER_LOG_FILE)
    raw_recent = []
    if TWITTER_LOG_FILE.exists():
        try:
            with open(TWITTER_LOG_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            raw_recent = raw.get("recent_types", [])
        except (json.JSONDecodeError, OSError):
            pass
    data["recent_types"] = raw_recent[-2:]
    return data


def is_twitter_due() -> tuple:
    """
    True si ya pasó el espaciado configurado para Twitter en
    platform_config.json (clave 'twitter' -> 'daily_count'). Devuelve
    (debe_publicar, log_actual) para que main() pueda reutilizar el log
    ya cargado al guardar después.
    """
    config = load_platform_config().get("twitter", {})
    daily_count = config.get("daily_count", 32)
    tw_log = load_twitter_log()
    if tw_log["count"] >= daily_count:
        return False, tw_log
    min_hours = spacing_hours_for(daily_count)
    hs = hours_since(tw_log.get("last_upload_utc"))
    return hs >= min_hours, tw_log


# ---------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------

def get_tiktok_allowed_privacy_levels(account_id: str, api_key: str, media_type: str) -> list:
    resp = requests.get(
        f"{ZERNIO_API_BASE}/accounts/{account_id}/tiktok/creator-info",
        headers={"Authorization": f"Bearer {api_key}"},
        params={"media_type": media_type},
        timeout=30,
    )
    resp.raise_for_status()
    return [lvl["value"] for lvl in resp.json().get("privacyLevels", [])]


def upload_to_tiktok(filepath: Path, category: str) -> str:
    api_key = os.environ["ZERNIO_API_KEY"]
    account_id = os.environ["ZERNIO_TIKTOK_ACCOUNT_ID"]
    privacy_level = os.environ.get("TIKTOK_PRIVACY_LEVEL", "PUBLIC_TO_EVERYONE")

    ext = filepath.suffix.lower()
    is_video = ext in TIKTOK_VIDEO_EXTS
    media_url = build_public_media_url(filepath)
    caption = TIKTOK_CAPTIONS.get(category, category)
    media_type = "video" if is_video else "photo"

    try:
        allowed = get_tiktok_allowed_privacy_levels(account_id, api_key, media_type)
        if allowed and privacy_level not in allowed:
            print(f"[WARN] TikTok: '{privacy_level}' no disponible, se usa '{allowed[0]}'.")
            privacy_level = allowed[0]
    except requests.RequestException as e:
        print(f"[WARN] TikTok: no se pudo leer creator-info ({e}), se sigue con '{privacy_level}'.")

    settings = {
        "privacy_level": privacy_level,
        "allow_comment": True,
        "content_preview_confirmed": True,
        "express_consent_given": True,
    }
    if is_video:
        settings["allow_duet"] = True
        settings["allow_stitch"] = True
        media_item_type = "video"
    else:
        settings["media_type"] = "photo"
        settings["description"] = caption
        settings["auto_add_music"] = True
        media_item_type = "image"

    return zernio_post("tiktok", account_id, api_key, media_url, media_item_type, caption, settings)


def maybe_post_to_tiktok(posted: dict) -> None:
    """
    TikTok elige su PROPIO archivo (independiente de Twitter), según los
    pesos de categoría y la cantidad diaria de platform_config.json.
    """
    if not zernio_credentials_available() or not os.environ.get("ZERNIO_TIKTOK_ACCOUNT_ID"):
        print("[INFO] TikTok: faltan credenciales, se omite.")
        return

    config = load_platform_config().get("tiktok", {})
    daily_count = config.get("daily_count", 10)
    weights = config.get("weights", {c: 1 for c in CATEGORIES})

    tt_log = load_platform_log(TIKTOK_LOG_FILE)
    if tt_log["count"] >= daily_count:
        print(f"[INFO] TikTok: cupo diario alcanzado ({tt_log['count']}/{daily_count}).")
        return

    min_hours = spacing_hours_for(daily_count)
    hs = hours_since(tt_log.get("last_upload_utc"))
    if hs < min_hours:
        print(f"[INFO] TikTok: última publicación hace {hs:.1f}h, faltan {min_hours - hs:.1f}h (reparto para {daily_count}/día).")
        return

    allowed_exts = TIKTOK_VIDEO_EXTS | TIKTOK_IMAGE_EXTS
    filepath, category = pick_file_for_platform(posted, weights, allowed_exts)
    if filepath is None:
        print("[INFO] TikTok: no hay contenido disponible ahora mismo según los pesos configurados.")
        return

    try:
        result = upload_to_tiktok(filepath, category)
        print(f"[OK] Publicado en TikTok ({category}): {filepath} -> {result}")
        tt_log["count"] += 1
        tt_log["last_upload_utc"] = datetime.now(timezone.utc).isoformat()
        save_platform_log(TIKTOK_LOG_FILE, tt_log)
        posted[file_key(filepath)] = today_str()
        save_posted_log(posted)
    except Exception as e:
        print(f"[WARN] Falló la publicación en TikTok: {e}")


# ---------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------

def upload_to_instagram(filepath: Path, category: str) -> str:
    api_key = os.environ["ZERNIO_API_KEY"]
    account_id = os.environ["ZERNIO_INSTAGRAM_ACCOUNT_ID"]

    ext = filepath.suffix.lower()
    is_video = ext in INSTAGRAM_VIDEO_EXTS
    media_url = build_public_media_url(filepath)
    caption = INSTAGRAM_CAPTIONS.get(category, category)
    media_type = "video" if is_video else "image"

    return zernio_post("instagram", account_id, api_key, media_url, media_type, caption)


def maybe_post_to_instagram(posted: dict) -> None:
    """
    Instagram elige su PROPIO archivo (independiente de Twitter y de
    TikTok), según los pesos de categoría y la cantidad diaria de
    platform_config.json.
    """
    if not zernio_credentials_available() or not os.environ.get("ZERNIO_INSTAGRAM_ACCOUNT_ID"):
        print("[INFO] Instagram: faltan credenciales, se omite.")
        return

    config = load_platform_config().get("instagram", {})
    daily_count = config.get("daily_count", 10)
    weights = config.get("weights", {c: 1 for c in CATEGORIES})

    ig_log = load_platform_log(INSTAGRAM_LOG_FILE)
    if ig_log["count"] >= daily_count:
        print(f"[INFO] Instagram: cupo diario alcanzado ({ig_log['count']}/{daily_count}).")
        return

    min_hours = spacing_hours_for(daily_count)
    hs = hours_since(ig_log.get("last_upload_utc"))
    if hs < min_hours:
        print(f"[INFO] Instagram: última publicación hace {hs:.1f}h, faltan {min_hours - hs:.1f}h (reparto para {daily_count}/día).")
        return

    allowed_exts = INSTAGRAM_VIDEO_EXTS | INSTAGRAM_IMAGE_EXTS
    filepath, category = pick_file_for_platform(posted, weights, allowed_exts)
    if filepath is None:
        print("[INFO] Instagram: no hay contenido disponible ahora mismo según los pesos configurados.")
        return

    try:
        result = upload_to_instagram(filepath, category)
        print(f"[OK] Publicado en Instagram ({category}): {filepath} -> {result}")
        ig_log["count"] += 1
        ig_log["last_upload_utc"] = datetime.now(timezone.utc).isoformat()
        save_platform_log(INSTAGRAM_LOG_FILE, ig_log)
        posted[file_key(filepath)] = today_str()
        save_posted_log(posted)
    except Exception as e:
        print(f"[WARN] Falló la publicación en Instagram: {e}")


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main() -> int:
    if not MEDIA_DIR.exists():
        print(f"ERROR: no existe la carpeta '{MEDIA_DIR}'", file=sys.stderr)
        return 1

    for var in ("TW_API_KEY", "TW_API_SECRET", "TW_ACCESS_TOKEN", "TW_ACCESS_TOKEN_SECRET"):
        if not os.environ.get(var):
            print(f"ERROR: falta la variable de entorno {var}", file=sys.stderr)
            return 1

    posted = load_posted_log()
    all_files = list_all_media()

    print(f"[INFO] Archivos válidos en media/: {len(all_files)}")
    for cat in CATEGORIES:
        cat_files = list_media_in_category(cat)
        cooldown = CATEGORY_COOLDOWN_DAYS.get(cat, REPEAT_COOLDOWN_DAYS)
        if cooldown is None:
            disponibles = sum(1 for p in cat_files if file_key(p) not in posted)
            print(f"[INFO]   - {cat}/: {len(cat_files)} archivo(s), {disponibles} disponible(s) (nunca se repite)")
        else:
            disponibles = sum(1 for p in cat_files if days_since(posted.get(file_key(p), "2000-01-01")) >= cooldown)
            print(f"[INFO]   - {cat}/: {len(cat_files)} archivo(s), {disponibles} disponible(s) (cooldown {cooldown} días)")
    twitter_weights_preview = load_platform_config().get("twitter", {}).get("weights", {c: 1 for c in CATEGORIES})
    print(f"[INFO] Pesos de Twitter: {twitter_weights_preview}")

    if not all_files:
        print("No hay ningún archivo válido dentro de 'media/'. Nada que publicar.")
        return 0

    # --- Twitter: ciclo fijo de categorías, respetando el espaciado
    # configurado en platform_config.json (clave "twitter" -> "daily_count") ---
    twitter_due, tw_log = is_twitter_due()
    if not twitter_due:
        daily_count = load_platform_config().get("twitter", {}).get("daily_count", 32)
        min_hours = spacing_hours_for(daily_count)
        hs = hours_since(tw_log.get("last_upload_utc"))
        if tw_log["count"] >= daily_count:
            print(f"[INFO] Twitter: cupo diario alcanzado ({tw_log['count']}/{daily_count}).")
        else:
            print(f"[INFO] Twitter: última publicación hace {hs:.1f}h, "
                  f"faltan {min_hours - hs:.1f}h (reparto para {daily_count}/día). Se omite por ahora.")
    else:
        twitter_weights = load_platform_config().get("twitter", {}).get("weights", {c: 1 for c in CATEGORIES})
        recent_types = tw_log.get("recent_types", [])
        force_video = recent_types[-2:] == ["image", "image"]
        if force_video:
            print("[INFO] Twitter: las últimas 2 publicaciones fueron imágenes, se fuerza un video.")

        result = pick_twitter_content(posted, twitter_weights, force_video=force_video)
        if result is None and force_video:
            print("[INFO] Twitter: no había ningún video disponible, se elige contenido normal en su lugar.")
            result = pick_twitter_content(posted, twitter_weights, force_video=False)

        if result is None:
            print("No hay contenido disponible en ninguna categoría ahora mismo para Twitter.")
        else:
            image_paths = result["image_paths"]
            category = result["category"]
            hit_index = result["hit_index"]

            if category == "hit":
                tweet_text = result["text"]
                desc = ", ".join(str(p) for p in image_paths) if image_paths else "(solo texto)"
                print(f"[INFO] Twitter publicando (hit #{hit_index}): {desc}")
            else:
                text_template = os.environ.get("TWEET_TEXT_TEMPLATE", "")
                tweet_text = text_template.format(filename=image_paths[0].stem) if text_template else ""
                print(f"[INFO] Twitter publicando ({category}): {image_paths[0]}")

            try:
                api_v1, client_v2 = build_clients()
                media_ids = None
                if image_paths:
                    media_ids = [upload_media(api_v1, p) for p in image_paths]

                response = client_v2.create_tweet(text=tweet_text, media_ids=media_ids)
                print("[OK] Tweet publicado:", response.data)

                if category == "hit":
                    mark_hit_entry_used(hit_index)
                    if len(image_paths) > 1:
                        this_type = "image"  # varias imágenes -> cuenta como imagen para la regla de 2 seguidas
                    elif len(image_paths) == 1:
                        this_type = "image" if image_paths[0].suffix.lower() in IMAGE_EXTS else "video"
                    else:
                        this_type = None  # tweet de solo texto, no cuenta para la regla
                else:
                    posted[file_key(image_paths[0])] = today_str()
                    save_posted_log(posted)
                    this_type = "image" if image_paths[0].suffix.lower() in IMAGE_EXTS else "video"

                if this_type is not None:
                    recent_types.append(this_type)
                    tw_log["recent_types"] = recent_types[-2:]
                tw_log["count"] += 1
                tw_log["last_upload_utc"] = datetime.now(timezone.utc).isoformat()
                save_platform_log(TWITTER_LOG_FILE, tw_log)
            except Exception as e:
                print(f"[WARN] Falló la publicación en Twitter: {e}")

    # --- YouTube, TikTok e Instagram: cada uno elige su propio
    # contenido, según su reparto configurado en platform_config.json
    # (independiente de lo que se acaba de tuitear) ---
    posted = load_posted_log()  # recargar por si el guardado de arriba cambió algo
    maybe_post_to_youtube(posted)
    posted = load_posted_log()
    maybe_post_to_tiktok(posted)
    posted = load_posted_log()
    maybe_post_to_instagram(posted)

    return 0


if __name__ == "__main__":
    sys.exit(main())
