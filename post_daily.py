#!/usr/bin/env python3
"""
post_daily.py

Publica automáticamente en X (Twitter) un archivo AL AZAR (imagen o video)
de la carpeta `media/`, evitando repetir el mismo archivo el mismo día
(UTC). Diseñado para correr cada 30 minutos vía GitHub Actions.

Uso:
    python post_daily.py

Requiere las siguientes variables de entorno:
    TW_API_KEY
    TW_API_SECRET
    TW_ACCESS_TOKEN
    TW_ACCESS_TOKEN_SECRET
    (opcional) TWEET_TEXT_TEMPLATE  -> texto que acompaña al tweet,
        usando {filename} para insertar el nombre del archivo sin extensión

    (opcional, para subir también a YouTube Shorts)
    YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN

    (opcional, para publicar también en TikTok vía la API de Zernio)
    ZERNIO_API_KEY          -> API key de tu cuenta de Zernio
    ZERNIO_TIKTOK_ACCOUNT_ID -> ID de la cuenta de TikTok ya conectada en Zernio
    (opcional) TIKTOK_PRIVACY_LEVEL -> por defecto "PUBLIC_TO_EVERYONE"

    NOTA IMPORTANTE sobre TikTok: Zernio/TikTok necesitan descargar el
    archivo desde una URL pública (no se les puede mandar el archivo
    directamente desde este script). Este script arma automáticamente
    la URL pública de GitHub (raw.githubusercontent.com) a partir del
    archivo ya commiteado en media/, lo cual SOLO funciona si el
    repositorio es público.
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
YOUTUBE_LOG_FILE = Path("youtube_log.json")
TIKTOK_LOG_FILE = Path("tiktok_log.json")

# Carpetas entre las que se alterna: una publicación de la primera,
# luego una de la segunda, luego otra vez la primera, etc.
CATEGORIES = ["deltarune", "shitpost", "touhou"]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTS = {
    ".mp4", ".mov", ".avi", ".wmv", ".flv", ".webm",
    ".mkv", ".m4v", ".3gp", ".3g2", ".mpg", ".mpeg", ".ts",
}
YOUTUBE_VIDEO_EXTS = {".mp4"}  # YouTube solo recibe mp4, aunque Twitter acepte más formatos

# Título fijo por categoría para los Shorts de YouTube (en vez de usar el
# nombre del archivo, que suele ser un hash sin sentido).
YOUTUBE_TITLES = {
    "deltarune": "Deltarune #shorts #deltarune",
    "shitpost": "Shitpost #shorts #memes",
    "touhou": "Touhou #shorts #touhou",
}
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS

# --- TikTok (vía Zernio) -----------------------------------------------
# Zernio expone la API de TikTok en https://zernio.com/api/v1
ZERNIO_API_BASE = "https://zernio.com/api/v1"

# Formatos que TikTok acepta a través de Zernio (subconjunto de los que
# usamos para Twitter/YouTube).
TIKTOK_VIDEO_EXTS = {".mp4", ".mov", ".webm"}
TIKTOK_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}  # TikTok no acepta .gif

TIKTOK_CAPTIONS = {
    "deltarune": "Deltarune #deltarune #fyp",
    "shitpost": "Shitpost #memes #fyp",
    "touhou": "Touhou #touhou #fyp",
}

# Cada cuánto se permite subir a TikTok como mínimo (para no gastar de
# golpe el cupo diario, ya que este script corre cada 45 min).
TIKTOK_MIN_HOURS_BETWEEN_UPLOADS = 1.5

# Cupos que Zernio aplica por defecto a cuentas de TikTok conectadas vía
# TikTok for Business (ver doc: "Daily posting caps"). Se replican aquí
# solo para repartir mejor las subidas a lo largo del día; si de todos
# modos se supera el cupo real, Zernio simplemente encola el post.
TIKTOK_DAILY_VIDEO_LIMIT = 15
TIKTOK_DAILY_PHOTO_LIMIT = 15


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def days_since(date_str: str) -> int:
    """Días transcurridos desde date_str (YYYY-MM-DD) hasta hoy, en UTC."""
    try:
        posted_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 9999  # fecha inválida -> lo tratamos como "hace mucho"
    return (datetime.now(timezone.utc) - posted_date).days


def load_posted_log() -> dict:
    """
    Devuelve un dict {ruta_relativa: "YYYY-MM-DD"} con la fecha (UTC) de
    la última vez que se publicó cada archivo.

    Es tolerante a un archivo corrupto, vacío, o en un formato viejo
    (ej. una lista en vez de un dict): en cualquiera de esos casos,
    arranca con un log vacío en vez de crashear.
    """
    if not LOG_FILE.exists():
        return {}
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("posted", {})
        if isinstance(raw, dict):
            return raw
        print("[WARN] posted_log.json tenía un formato viejo/ inválido, "
              "se reinicia el registro.")
        return {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] No se pudo leer posted_log.json ({e}), se reinicia el registro.")
        return {}


def save_posted_log(posted: dict) -> None:
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump({"posted": posted}, f, indent=2, ensure_ascii=False, sort_keys=True)


def load_rotation_state() -> str:
    """Devuelve la categoría que le toca publicar ahora. Por defecto, la primera."""
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


def list_media_in_category(category: str) -> list[Path]:
    folder = MEDIA_DIR / category
    if not folder.exists():
        return []
    return [
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    ]


def list_all_media() -> list[Path]:
    """Todos los archivos válidos dentro de media/, incluyendo subcarpetas."""
    return [
        p for p in MEDIA_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    ]


def file_key(p: Path) -> str:
    """Clave estable para identificar un archivo en el log (ruta relativa a media/)."""
    return str(p.relative_to(MEDIA_DIR))


REPEAT_COOLDOWN_DAYS = 30

# Cooldown específico por categoría (sobreescribe REPEAT_COOLDOWN_DAYS).
# None = nunca se repite (una vez publicado, queda excluido para siempre).
CATEGORY_COOLDOWN_DAYS = {
    "deltarune": 30,
    "shitpost": None,
    "touhou": 30,
}


def pick_next_file(posted: dict, force_video_only: bool = False) -> tuple[Path | None, str | None]:
    """
    Elige un archivo siguiendo el ciclo fijo de CATEGORIES (en el orden
    de la lista, volviendo al inicio al llegar al final). Primero
    intenta la categoría que le toca según rotation_state.json. Si esa
    categoría no tiene ningún candidato disponible (todo en cooldown, o
    la carpeta está vacía), cae de respaldo a la siguiente en el ciclo,
    y así sucesivamente, para no dejar de publicar.

    Si force_video_only=True, solo se consideran archivos .mp4 (se usa
    cuando toca el turno de subir a YouTube, para garantizar que el
    archivo elegido sea compatible).

    Devuelve (archivo_elegido, categoría_realmente_usada), o (None, None)
    si no hay absolutamente nada disponible en ninguna categoría.
    """
    preferred = load_rotation_state()
    if preferred not in CATEGORIES:
        preferred = CATEGORIES[0]
    start_idx = CATEGORIES.index(preferred)
    ordered_categories = [CATEGORIES[(start_idx + i) % len(CATEGORIES)] for i in range(len(CATEGORIES))]

    for category in ordered_categories:
        files = list_media_in_category(category)
        if force_video_only:
            files = [p for p in files if p.suffix.lower() in YOUTUBE_VIDEO_EXTS]

        cooldown = CATEGORY_COOLDOWN_DAYS.get(category, REPEAT_COOLDOWN_DAYS)

        if cooldown is None:
            # Nunca se repite: cualquier archivo ya presente en el log queda excluido para siempre.
            candidates = [p for p in files if file_key(p) not in posted]
        else:
            candidates = [
                p for p in files
                if days_since(posted.get(file_key(p), "2000-01-01")) >= cooldown
            ]

        if candidates:
            return random.choice(candidates), category

    return None, None


def build_clients():
    """
    tweepy.Client (API v2) se usa para crear el tweet.
    tweepy.API (API v1.1) se sigue necesitando para subir media,
    ya que la subida de media todavía no está 100% migrada a v2.
    """
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
        # "amplify_video" (en vez de "tweet_video") permite videos largos
        # en cuentas Premium. chunked=True es obligatorio para archivos
        # grandes.
        media = api_v1.media_upload(
            filename=str(filepath), chunked=True, media_category="amplify_video"
        )
    else:
        media = api_v1.media_upload(filename=str(filepath))
    return media.media_id_string


YOUTUBE_DAILY_LIMIT_PER_CATEGORY = 3


def load_youtube_log() -> dict:
    """
    {'date': 'YYYY-MM-DD', 'counts': {'deltarune': N, 'shitpost': N},
     'last_upload_utc': ISO8601 o None}
    """
    today = today_str()
    if YOUTUBE_LOG_FILE.exists():
        try:
            with open(YOUTUBE_LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == today:
                data.setdefault("last_upload_utc", None)
                data.setdefault("counts", {})
                for cat in CATEGORIES:
                    data["counts"].setdefault(cat, 0)
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"date": today, "counts": {cat: 0 for cat in CATEGORIES}, "last_upload_utc": None}


def save_youtube_log(data: dict) -> None:
    with open(YOUTUBE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def youtube_credentials_available() -> bool:
    return all(
        os.environ.get(var)
        for var in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
    )


def upload_to_youtube(filepath: Path, title: str) -> str:
    """Sube un video a YouTube como Short público. Devuelve el video_id."""
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
        "snippet": {
            "title": title[:100],
            "description": f"{title}\n\n#Shorts",
            "categoryId": "24",  # Entertainment
        },
        "status": {"privacyStatus": "public"},
    }
    media = MediaFileUpload(str(filepath), chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]


YOUTUBE_MIN_HOURS_BETWEEN_UPLOADS = 4


def hours_since_last_youtube_upload(yt_log: dict) -> float:
    last = yt_log.get("last_upload_utc")
    if not last:
        return 9999  # nunca se subió nada -> se permite
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return 9999
    return (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600


def maybe_upload_to_youtube(filepath: Path, category: str) -> None:
    """
    Sube a YouTube si: es un .mp4 (YouTube solo recibe ese formato aquí),
    hay credenciales, esa categoría ('deltarune'/'shitpost') no llegó a
    su cupo diario de YOUTUBE_DAILY_LIMIT_PER_CATEGORY, y ya pasaron al
    menos YOUTUBE_MIN_HOURS_BETWEEN_UPLOADS horas desde la última subida
    (para repartirlas a lo largo del día en vez de subirlas todas seguidas).
    """
    if filepath.suffix.lower() not in YOUTUBE_VIDEO_EXTS:
        print(f"[INFO] YouTube: solo se suben archivos {YOUTUBE_VIDEO_EXTS}, se omite.")
        return

    if not youtube_credentials_available():
        print("[INFO] YouTube: faltan credenciales (YT_CLIENT_ID / YT_CLIENT_SECRET / "
              "YT_REFRESH_TOKEN), se omite la subida.")
        return

    yt_log = load_youtube_log()
    current_count = yt_log["counts"].get(category, 0)

    if current_count >= YOUTUBE_DAILY_LIMIT_PER_CATEGORY:
        print(f"[INFO] YouTube: cupo diario de '{category}' alcanzado "
              f"({current_count}/{YOUTUBE_DAILY_LIMIT_PER_CATEGORY}), se omite.")
        return

    hours_since = hours_since_last_youtube_upload(yt_log)
    if hours_since < YOUTUBE_MIN_HOURS_BETWEEN_UPLOADS:
        faltan = YOUTUBE_MIN_HOURS_BETWEEN_UPLOADS - hours_since
        print(f"[INFO] YouTube: última subida hace {hours_since:.1f}h, "
              f"faltan {faltan:.1f}h para la próxima. Se omite por ahora.")
        return

    try:
        title = YOUTUBE_TITLES.get(category, category)
        video_id = upload_to_youtube(filepath, title)
        print(f"[OK] Subido a YouTube ({category}): https://youtu.be/{video_id}")
        yt_log["counts"][category] = current_count + 1
        yt_log["last_upload_utc"] = datetime.now(timezone.utc).isoformat()
        save_youtube_log(yt_log)
    except Exception as e:
        print(f"[WARN] Falló la subida a YouTube (no afecta al post de Twitter): {e}")



def is_youtube_turn_due() -> bool:
    """
    True si: hay credenciales, ya pasaron las 4h desde la última subida,
    y al menos una categoría todavía no llegó a su cupo diario. En ese
    caso, el turno de publicación debe forzar un .mp4.
    """
    if not youtube_credentials_available():
        return False
    yt_log = load_youtube_log()
    if hours_since_last_youtube_upload(yt_log) < YOUTUBE_MIN_HOURS_BETWEEN_UPLOADS:
        return False
    return any(
        yt_log["counts"].get(cat, 0) < YOUTUBE_DAILY_LIMIT_PER_CATEGORY
        for cat in CATEGORIES
    )


def tiktok_credentials_available() -> bool:
    return all(
        os.environ.get(var)
        for var in ("ZERNIO_API_KEY", "ZERNIO_TIKTOK_ACCOUNT_ID")
    )


def load_tiktok_log() -> dict:
    """
    {'date': 'YYYY-MM-DD', 'counts': {'video': N, 'photo': N},
     'last_upload_utc': ISO8601 o None}
    """
    today = today_str()
    if TIKTOK_LOG_FILE.exists():
        try:
            with open(TIKTOK_LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == today:
                data.setdefault("counts", {})
                data["counts"].setdefault("video", 0)
                data["counts"].setdefault("photo", 0)
                data.setdefault("last_upload_utc", None)
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"date": today, "counts": {"video": 0, "photo": 0}, "last_upload_utc": None}


def save_tiktok_log(data: dict) -> None:
    with open(TIKTOK_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def hours_since_last_tiktok_upload(tt_log: dict) -> float:
    last = tt_log.get("last_upload_utc")
    if not last:
        return 9999  # nunca se subió nada -> se permite
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return 9999
    return (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600


def build_public_media_url(filepath: Path) -> str:
    """
    Construye la URL pública "raw" de GitHub del archivo. TikTok (a
    través de Zernio) no acepta subida directa de bytes desde este
    script: necesita descargar el media desde una URL pública, sin
    autenticación (ver "Media URLs" en la doc de Zernio).

    IMPORTANTE: esto solo funciona si el repositorio es público y el
    archivo ya está commiteado en la rama actual (los archivos de
    media/ ya lo están, porque se suben de antemano al repo).

    GITHUB_REPOSITORY y GITHUB_REF_NAME los define automáticamente
    GitHub Actions, no hace falta configurarlos a mano.
    """
    repo = os.environ.get("GITHUB_REPOSITORY")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if not repo:
        raise RuntimeError(
            "GITHUB_REPOSITORY no está definido (¿se está corriendo fuera de GitHub Actions?)"
        )
    rel_path = filepath.as_posix()
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{rel_path}"


def get_tiktok_allowed_privacy_levels(account_id: str, api_key: str, media_type: str) -> list[str]:
    """Consulta creator-info y devuelve los privacy_level permitidos para esta cuenta."""
    resp = requests.get(
        f"{ZERNIO_API_BASE}/accounts/{account_id}/tiktok/creator-info",
        headers={"Authorization": f"Bearer {api_key}"},
        params={"media_type": media_type},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return [lvl["value"] for lvl in data.get("privacyLevels", [])]


def upload_to_tiktok(filepath: Path, category: str) -> str:
    """
    Publica el archivo en TikTok a través de la API de Zernio (POST
    /v1/posts). Devuelve un string descriptivo del resultado.
    Lanza una excepción si Zernio/TikTok rechaza la publicación.
    """
    api_key = os.environ["ZERNIO_API_KEY"]
    account_id = os.environ["ZERNIO_TIKTOK_ACCOUNT_ID"]
    privacy_level = os.environ.get("TIKTOK_PRIVACY_LEVEL", "PUBLIC_TO_EVERYONE")

    ext = filepath.suffix.lower()
    is_video = ext in TIKTOK_VIDEO_EXTS
    is_image = ext in TIKTOK_IMAGE_EXTS
    if not is_video and not is_image:
        raise ValueError(f"Extensión '{ext}' no soportada por TikTok.")

    media_url = build_public_media_url(filepath)
    caption = TIKTOK_CAPTIONS.get(category, category)
    media_type = "video" if is_video else "photo"

    # Verificamos contra creator-info que el privacy_level elegido sea
    # válido para esta cuenta; si no, usamos el primero que sí lo sea.
    try:
        allowed_levels = get_tiktok_allowed_privacy_levels(account_id, api_key, media_type)
        if allowed_levels and privacy_level not in allowed_levels:
            print(f"[WARN] TikTok: '{privacy_level}' no está disponible para esta cuenta, "
                  f"se usa '{allowed_levels[0]}' en su lugar.")
            privacy_level = allowed_levels[0]
    except requests.RequestException as e:
        print(f"[WARN] TikTok: no se pudo leer creator-info ({e}), se sigue con '{privacy_level}'.")

    tiktok_settings = {
        "privacy_level": privacy_level,
        "allow_comment": True,
        "content_preview_confirmed": True,
        "express_consent_given": True,
    }

    if is_video:
        media_items = [{"type": "video", "url": media_url}]
        tiktok_settings["allow_duet"] = True
        tiktok_settings["allow_stitch"] = True
    else:
        media_items = [{"type": "image", "url": media_url}]
        tiktok_settings["media_type"] = "photo"
        tiktok_settings["description"] = caption
        tiktok_settings["auto_add_music"] = True

    payload = {
        "content": caption,
        "mediaItems": media_items,
        "platforms": [{"platform": "tiktok", "accountId": account_id}],
        "tiktokSettings": tiktok_settings,
        "publishNow": True,
    }

    resp = requests.post(
        f"{ZERNIO_API_BASE}/posts",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=180,
    )

    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise RuntimeError(
            f"Respuesta inesperada de Zernio (status {resp.status_code}): {resp.text[:300]}"
        )

    # 200/201 = éxito total, 207 = éxito parcial (puede incluir un fallo
    # puntual en la entrada de tiktok, se revisa abajo).
    if resp.status_code not in (200, 201, 207):
        raise RuntimeError(f"Zernio devolvió {resp.status_code}: {data}")

    platforms = data.get("post", {}).get("platforms", [])
    tiktok_entry = next((p for p in platforms if p.get("platform") == "tiktok"), None)
    if tiktok_entry is None:
        raise RuntimeError(f"Respuesta de Zernio sin entrada de tiktok: {data}")

    status = tiktok_entry.get("status")
    if status == "failed":
        raise RuntimeError(tiktok_entry.get("errorMessage", "Fallo desconocido al publicar en TikTok"))

    return f"{status} ({media_type})"


def maybe_upload_to_tiktok(filepath: Path, category: str) -> None:
    """
    Publica en TikTok el mismo archivo que se acaba de publicar en
    Twitter, salvo que: falten credenciales, el formato no sea
    compatible con TikTok, ya se haya llegado al cupo diario de
    video/foto, o no haya pasado suficiente tiempo desde la última
    subida. Cualquier fallo se loguea pero NO hace fallar el resto del
    script (el tweet ya se publicó).
    """
    if not tiktok_credentials_available():
        print("[INFO] TikTok: faltan credenciales (ZERNIO_API_KEY / ZERNIO_TIKTOK_ACCOUNT_ID), se omite.")
        return

    ext = filepath.suffix.lower()
    if ext not in TIKTOK_VIDEO_EXTS and ext not in TIKTOK_IMAGE_EXTS:
        print(f"[INFO] TikTok: extensión '{ext}' no soportada, se omite.")
        return

    tt_log = load_tiktok_log()
    media_kind = "video" if ext in TIKTOK_VIDEO_EXTS else "photo"
    current_count = tt_log["counts"].get(media_kind, 0)
    daily_limit = TIKTOK_DAILY_VIDEO_LIMIT if media_kind == "video" else TIKTOK_DAILY_PHOTO_LIMIT

    if current_count >= daily_limit:
        print(f"[INFO] TikTok: cupo diario de {media_kind} alcanzado ({current_count}/{daily_limit}), se omite.")
        return

    hours_since = hours_since_last_tiktok_upload(tt_log)
    if hours_since < TIKTOK_MIN_HOURS_BETWEEN_UPLOADS:
        faltan = TIKTOK_MIN_HOURS_BETWEEN_UPLOADS - hours_since
        print(f"[INFO] TikTok: última subida hace {hours_since:.1f}h, faltan {faltan:.1f}h. Se omite por ahora.")
        return

    try:
        result = upload_to_tiktok(filepath, category)
        print(f"[OK] Publicado en TikTok ({category}): {result}")
        tt_log["counts"][media_kind] = current_count + 1
        tt_log["last_upload_utc"] = datetime.now(timezone.utc).isoformat()
        save_tiktok_log(tt_log)
    except Exception as e:
        print(f"[WARN] Falló la publicación en TikTok (no afecta al post de Twitter): {e}")


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
    today = today_str()

    print(f"[INFO] Archivos válidos en media/: {len(all_files)}")
    for cat in CATEGORIES:
        cat_files = list_media_in_category(cat)
        cooldown = CATEGORY_COOLDOWN_DAYS.get(cat, REPEAT_COOLDOWN_DAYS)
        if cooldown is None:
            disponibles = sum(1 for p in cat_files if file_key(p) not in posted)
            print(f"[INFO]   - {cat}/: {len(cat_files)} archivo(s), {disponibles} disponible(s) (nunca se repite)")
        else:
            disponibles = sum(
                1 for p in cat_files
                if days_since(posted.get(file_key(p), "2000-01-01")) >= cooldown
            )
            print(f"[INFO]   - {cat}/: {len(cat_files)} archivo(s), {disponibles} disponible(s) (cooldown {cooldown} días)")
    print(f"[INFO] Le toca a la categoría: {load_rotation_state()}")

    if not all_files:
        print("No hay ningún archivo válido dentro de 'media/'. Nada que publicar.")
        return 0

    youtube_turn = is_youtube_turn_due()
    if youtube_turn:
        print("[INFO] Toca turno de YouTube: se forzará elegir un .mp4.")

    next_file, used_category = pick_next_file(posted, force_video_only=youtube_turn)
    if next_file is None and youtube_turn:
        print("[INFO] No había ningún .mp4 disponible para el turno de YouTube, "
              "se elige contenido normal en su lugar.")
        next_file, used_category = pick_next_file(posted, force_video_only=False)
    if next_file is None:
        print("No hay contenido disponible en ninguna categoría ahora mismo "
              "(cooldown activo o agotado). Nada que publicar.")
        return 0

    print(f"[INFO] Publicando ({used_category}): {next_file}")

    try:
        api_v1, client_v2 = build_clients()
        media_id = upload_media(api_v1, next_file)

        text_template = os.environ.get("TWEET_TEXT_TEMPLATE", "")
        tweet_text = text_template.format(filename=next_file.stem) if text_template else ""

        response = client_v2.create_tweet(text=tweet_text, media_ids=[media_id])
        print("[OK] Tweet publicado:", response.data)
        maybe_upload_to_youtube(next_file, used_category)
        maybe_upload_to_tiktok(next_file, used_category)
    except Exception as e:
        # Si algo falla al publicar, NO marcamos el archivo como publicado,
        # para que el próximo intento (en 30 min) lo vuelva a probar.
        print(f"ERROR al publicar el tweet: {e}", file=sys.stderr)
        return 1

    # Solo actualizamos el log si la publicación fue exitosa.
    posted[file_key(next_file)] = today
    save_posted_log(posted)

    # La próxima vez le toca a la otra categoría (alternancia).
    next_category = CATEGORIES[(CATEGORIES.index(used_category) + 1) % len(CATEGORIES)]
    save_rotation_state(next_category)

    return 0


if __name__ == "__main__":
    sys.exit(main())
