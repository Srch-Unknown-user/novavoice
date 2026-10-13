"""
NovaVoice TTS Platform — Flask + edge-tts
Most natural multilingual neural TTS including Uzbek (Madina & Sardor).
Run: python app.py  ->  http://127.0.0.1:5005
"""
import asyncio
import io
import json
import os
import socket
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
import edge_tts

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
AUDIO_DIR = BASE_DIR / "audio_cache"
AUDIO_DIR.mkdir(exist_ok=True)

# ---- generated-speech records ----
RECORDS_FILE = BASE_DIR / "records.json"
MAX_RECORDS = 100


def load_records():
    try:
        if RECORDS_FILE.exists():
            data = json.loads(RECORDS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def persist_records(recs):
    try:
        RECORDS_FILE.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print("records save failed:", e)


def sanitize_filename(name, default="record"):
    """Windows-safe filename from a user-given name."""
    bad = '\\/:*?"<>|'
    cleaned = "".join(c for c in (name or "") if c not in bad).strip().strip(".")
    cleaned = " ".join(cleaned.split())
    return cleaned[:80] or default


def save_record(text, voice, rate_val, pitch_val, volume_val, audio_bytes, name=""):
    """Persist one generated MP3 + metadata. Returns the record dict."""
    rid = uuid.uuid4().hex[:12]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    title = sanitize_filename(name) if (name or "").strip() else ""
    if not title:
        title = "".join(c for c in voice if c.isalnum() or c in "-_")[:40] or "voice"
    fname = f"{stamp}_{title}_{rid}.mp3"
    try:
        (AUDIO_DIR / fname).write_bytes(audio_bytes)
    except Exception as e:
        print("audio save failed:", e)
        return None
    rec = {
        "id": rid,
        "name": (name or "").strip()[:80],
        "file": fname,
        "text": text,
        "voice": voice,
        "rate": rate_val,
        "pitch": pitch_val,
        "volume": volume_val,
        "created": datetime.now().isoformat(timespec="seconds"),
        "size": len(audio_bytes),
    }
    recs = load_records()
    recs.append(rec)
    while len(recs) > MAX_RECORDS:  # prune oldest + their files
        old = recs.pop(0)
        try:
            (AUDIO_DIR / old.get("file", "")).unlink(missing_ok=True)
        except Exception:
            pass
    persist_records(recs)
    return rec


def cleanup_orphans():
    """Delete cached mp3 files that have no record (legacy random-name cache)."""
    try:
        known = {r.get("file") for r in load_records()}
        for f in AUDIO_DIR.glob("*.mp3"):
            if f.name not in known:
                try:
                    f.unlink()
                except Exception:
                    pass
    except Exception as e:
        print("cleanup failed:", e)

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

# ---- voices cache ----
VOICES_CACHE_FILE = BASE_DIR / "voices_cache.json"
VOICES_CACHE_TTL = 24 * 3600
_voices_mem = {"data": None, "ts": 0}

# Curated most-natural voices pinned on top of UI
BEST_VOICES = [
    "uz-UZ-MadinaNeural",
    "uz-UZ-SardorNeural",
    "en-US-AvaNeural",
    "en-US-AndrewNeural",
    "en-US-EmmaNeural",
    "en-US-BrianNeural",
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
    "ru-RU-SvetlanaNeural",
    "ru-RU-DmitryNeural",
    "tr-TR-EmelNeural",
    "tr-TR-AhmetNeural",
    "es-ES-ElviraNeural",
    "fr-FR-DeniseNeural",
    "de-DE-KatjaNeural",
    "ar-SA-ZariyahNeural",
    "uz-UZ-MadinaNeural",
]

DEFAULT_VOICE = "uz-UZ-MadinaNeural"
PORT = 5005


def lan_ip():
    # find outbound LAN IP without sending anything
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _load_cached_voices():
    if _voices_mem["data"] and (time.time() - _voices_mem["ts"] < VOICES_CACHE_TTL):
        return _voices_mem["data"]
    if VOICES_CACHE_FILE.exists():
        try:
            age = time.time() - VOICES_CACHE_FILE.stat().st_mtime
            if age < VOICES_CACHE_TTL:
                data = json.loads(VOICES_CACHE_FILE.read_text(encoding="utf-8"))
                _voices_mem.update(data=data, ts=time.time())
                return data
        except Exception:
            pass
    return None


async def _fetch_voices_async():
    voices = await edge_tts.list_voices()
    # sort: Uzbek first, then BEST, then alphabetically
    def sort_key(v):
        sn = v.get("ShortName", "")
        locale = v.get("Locale", "")
        is_uz = 0 if locale.startswith("uz") else 1
        try:
            best_idx = BEST_VOICES.index(sn)
        except ValueError:
            best_idx = 999
        return (is_uz, best_idx, locale, sn)
    voices = sorted(voices, key=sort_key)
    return voices


def get_voices():
    cached = _load_cached_voices()
    if cached:
        return cached
    try:
        voices = asyncio.run(_fetch_voices_async())
        VOICES_CACHE_FILE.write_text(json.dumps(voices, ensure_ascii=False, indent=1), encoding="utf-8")
        _voices_mem.update(data=voices, ts=time.time())
        return voices
    except Exception as e:
        # offline fallback — curated minimal list so UI still works
        print("Voice list fetch failed:", e)
        if VOICES_CACHE_FILE.exists():
            try:
                return json.loads(VOICES_CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return [
            {"ShortName": "uz-UZ-MadinaNeural", "FriendlyName": "Madina - Uzbek (Female)", "Locale": "uz-UZ", "LocaleName": "Uzbek", "Gender": "Female"},
            {"ShortName": "uz-UZ-SardorNeural", "FriendlyName": "Sardor - Uzbek (Male)", "Locale": "uz-UZ", "LocaleName": "Uzbek", "Gender": "Male"},
            {"ShortName": "en-US-AvaNeural", "FriendlyName": "Ava - English US (Female)", "Locale": "en-US", "LocaleName": "English (US)", "Gender": "Female"},
            {"ShortName": "en-US-AndrewNeural", "FriendlyName": "Andrew - English US (Male)", "Locale": "en-US", "LocaleName": "English (US)", "Gender": "Male"},
            {"ShortName": "ru-RU-SvetlanaNeural", "FriendlyName": "Svetlana - Russian (Female)", "Locale": "ru-RU", "LocaleName": "Russian", "Gender": "Female"},
            {"ShortName": "ru-RU-DmitryNeural", "FriendlyName": "Dmitry - Russian (Male)", "Locale": "ru-RU", "LocaleName": "Russian", "Gender": "Male"},
            {"ShortName": "tr-TR-EmelNeural", "FriendlyName": "Emel - Turkish (Female)", "Locale": "tr-TR", "LocaleName": "Turkish", "Gender": "Female"},
        ]


def fmt_rate(v: int) -> str:
    # v: -50..+100 slider -> "+20%"
    sign = "+" if v >= 0 else ""
    return f"{sign}{int(v)}%"


def fmt_pitch(v: int) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{int(v)}Hz"


def fmt_volume(v: int) -> str:
    # slider -100..+100 -> edge expects e.g. "+0%"
    sign = "+" if v >= 0 else ""
    return f"{sign}{int(v)}%"


async def synthesize_async(text: str, voice: str, rate: str, pitch: str, volume: str) -> bytes:
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    buf.seek(0)
    return buf.read()


# ---------- routes ----------
@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "default_voice": DEFAULT_VOICE, "lan": lan_ip(), "port": PORT})


@app.route("/api/voices")
def voices():
    vs = get_voices()
    locales = sorted({v.get("Locale", "") for v in vs if v.get("Locale")})
    return jsonify({"voices": vs, "count": len(vs), "locales": locales, "best": BEST_VOICES, "default": DEFAULT_VOICE})


@app.route("/api/speak", methods=["POST"])
def speak():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    voice = (data.get("voice") or DEFAULT_VOICE).strip()
    rate_val = int(data.get("rate", 0))
    pitch_val = int(data.get("pitch", 0))
    volume_val = int(data.get("volume", 0))

    if not text:
        return jsonify({"error": "No text provided"}), 400
    if len(text) > 10000:
        return jsonify({"error": "Text too long (max 10000 chars)"}), 400

    # clamp
    rate_val = max(-50, min(100, rate_val))
    pitch_val = max(-50, min(50, pitch_val))
    volume_val = max(-100, min(100, volume_val))

    rate = fmt_rate(rate_val)
    pitch = fmt_pitch(pitch_val)
    volume = fmt_volume(volume_val)

    try:
        audio_bytes = asyncio.run(synthesize_async(text, voice, rate, pitch, volume))
    except Exception as e:
        print("TTS error:", e)
        return jsonify({"error": f"TTS failed: {e}"}), 500

    if not audio_bytes or len(audio_bytes) < 500:
        return jsonify({"error": "Empty audio — try shorter text or another voice"}), 500

    return send_file(
        io.BytesIO(audio_bytes),
        mimetype="audio/mpeg",
        as_attachment=False,
        download_name="novavoice.mp3",
    )


@app.route("/api/download", methods=["POST"])
def download():
    # same as speak but forces file download with nice filename
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    voice = (data.get("voice") or DEFAULT_VOICE).strip()
    rate_val = max(-50, min(100, int(data.get("rate", 0))))
    pitch_val = max(-50, min(50, int(data.get("pitch", 0))))
    volume_val = max(-100, min(100, int(data.get("volume", 0))))
    if not text:
        return jsonify({"error": "No text"}), 400
    try:
        audio_bytes = asyncio.run(synthesize_async(text, voice, fmt_rate(rate_val), fmt_pitch(pitch_val), fmt_volume(volume_val)))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    safe_voice = "".join(c for c in voice if c.isalnum() or c in "-_")[:40]
    custom = sanitize_filename((data.get("name") or "").strip(), default="")
    dl_name = (custom or f"TTS-{safe_voice}") + ".mp3"
    return send_file(io.BytesIO(audio_bytes), mimetype="audio/mpeg", as_attachment=True, download_name=dl_name)


@app.route("/api/records")
def records_list():
    recs = sorted(load_records(), key=lambda r: r.get("created", ""), reverse=True)
    return jsonify({"records": recs, "count": len(recs)})


@app.route("/audio/<path:fname>")
def serve_audio(fname):
    if "/" in fname or "\\" in fname or not fname.endswith(".mp3"):
        return jsonify({"error": "bad filename"}), 400
    return send_from_directory(str(AUDIO_DIR), fname, mimetype="audio/mpeg")


@app.route("/api/records", methods=["POST"])
def records_create():
    """Save an already-generated MP3 (uploaded blob) as a named record."""
    f = request.files.get("audio")
    if not f or not (f.filename or "").endswith(".mp3"):
        return jsonify({"error": "audio mp3 file required"}), 400
    audio_bytes = f.read()
    if not audio_bytes or len(audio_bytes) < 500:
        return jsonify({"error": "empty audio"}), 400
    name = (request.form.get("name") or "").strip()[:80]
    if not name:
        return jsonify({"error": "name required"}), 400
    text = (request.form.get("text") or "").strip()
    voice = (request.form.get("voice") or DEFAULT_VOICE).strip()

    def num(key, lo, hi, default=0):
        try:
            return max(lo, min(hi, int(request.form.get(key, default))))
        except (TypeError, ValueError):
            return default

    rec = save_record(text, voice, num("rate", -50, 100), num("pitch", -50, 50),
                      num("volume", -100, 100), audio_bytes, name=name)
    if not rec:
        return jsonify({"error": "save failed"}), 500
    return jsonify({"ok": True, "record": rec})


@app.route("/api/records/<rid>", methods=["PUT"])
def records_rename(rid):
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()[:80]
    if not name:
        return jsonify({"error": "name required"}), 400
    recs = load_records()
    rec = next((r for r in recs if r.get("id") == rid), None)
    if not rec:
        return jsonify({"error": "not found"}), 404
    rec["name"] = name
    persist_records(recs)
    return jsonify({"ok": True, "record": rec})


@app.route("/api/records/<rid>", methods=["DELETE"])
def records_delete(rid):
    recs = load_records()
    rec = next((r for r in recs if r.get("id") == rid), None)
    if not rec:
        return jsonify({"error": "not found"}), 404
    try:
        (AUDIO_DIR / rec.get("file", "")).unlink(missing_ok=True)
    except Exception:
        pass
    persist_records([r for r in recs if r.get("id") != rid])
    return jsonify({"ok": True})


if __name__ == "__main__":
    cleanup_orphans()
    ip = lan_ip()
    print("=" * 55)
    print("  NovaVoice  —  PC:      http://127.0.0.1:5005")
    print(f"  NovaVoice  —  Phone (same Wi-Fi):  http://{ip}:{PORT}")
    print("  Uzbek: Madina (F) / Sardor (M) + 150+ neural voices")
    print("  Android: Chrome -> open phone URL -> Menu -> Add to Home screen")
    print("=" * 55)
    app.run(host="0.0.0.0", port=PORT, debug=False)
