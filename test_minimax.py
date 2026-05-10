"""
Standalone test: verifica connessione MiniMax e sintetizza una frase di prova.
Eseguire dalla root del repo: python test_minimax.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Carica .env manualmente (senza pydantic-settings)
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

API_KEY   = os.environ.get("MINIMAX_API_KEY", "")
GROUP_ID  = os.environ.get("MINIMAX_GROUP_ID", "")
BASE_URL  = "https://api.minimax.io/v1"

import httpx

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


async def list_system_voices():
    """Elenca voci di sistema disponibili (non clonate)."""
    print("\n── Lista voci di sistema ───────────────────────────────────")
    # Proviamo l'endpoint voci pubbliche
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{BASE_URL}/text_to_speech/voice_list",
            headers=HEADERS,
            params={"GroupId": GROUP_ID},
        )
    print(f"Status: {resp.status_code}")
    try:
        data = resp.json()
        print(f"Response: {data}")
    except Exception:
        print(f"Raw: {resp.text[:500]}")
    return resp


async def test_t2a_sync(voice_id: str, text: str, out_path: Path):
    """Test con T2A v2 sincrono (più semplice per il test iniziale)."""
    print(f"\n── Test sintesi sincrona (voice_id={voice_id}) ─────────────")
    payload = {
        "model": "speech-02-hd",
        "text": text,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
        "language_boost": "Italian",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{BASE_URL}/t2a_v2",
            headers=HEADERS,
            params={"GroupId": GROUP_ID},
            json=payload,
        )

    print(f"Status: {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        print(f"Raw: {resp.text[:1000]}")
        return False

    print(f"Response keys: {list(data.keys())}")
    base = data.get("base_resp", {})
    print(f"base_resp: {base}")

    if base.get("status_code", -1) != 0:
        print(f"ERRORE API: {base.get('status_msg')}")
        print(f"Full response: {data}")
        return False

    # Prova a trovare l'audio nell'oggetto di risposta
    audio_obj = data.get("data", {})
    audio_b64 = audio_obj.get("audio") if isinstance(audio_obj, dict) else None

    if audio_b64:
        import base64
        out_path.write_bytes(base64.b64decode(audio_b64))
        print(f"Audio salvato: {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")
        return True
    else:
        print(f"Nessun audio nella risposta: {data}")
        return False


async def test_t2a_async(voice_id: str, text: str, out_path: Path):
    """Test con T2A v2 asincrono (submit → poll → download)."""
    print(f"\n── Test sintesi ASINCRONA (voice_id={voice_id}) ────────────")

    # Step 1: submit
    payload = {
        "model": "speech-02-hd",
        "text": text,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
            "emotion": "neutral",
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
        "language_boost": "Italian",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/t2a_async_v2",
            headers=HEADERS,
            params={"GroupId": GROUP_ID},
            json=payload,
        )

    print(f"Submit status: {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        print(f"Raw: {resp.text[:500]}")
        return False

    print(f"Submit response: {data}")

    task_id = data.get("task_id")
    base = data.get("base_resp", {})
    if not task_id or base.get("status_code", -1) != 0:
        print(f"Submit FALLITO: {base}")
        return False

    print(f"task_id: {task_id}")

    # Step 2: poll
    for attempt in range(30):
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=15) as client:
            poll = await client.get(
                f"{BASE_URL}/query/t2a_async_query_v2",
                headers=HEADERS,
                params={"task_id": task_id, "GroupId": GROUP_ID},
            )
        try:
            pdata = poll.json()
        except Exception:
            print(f"Poll raw: {poll.text[:200]}")
            continue

        status = pdata.get("status") or pdata.get("task_status")
        print(f"  Poll {attempt+1}: status={status}")

        if status in ("Success", "success", "completed", 2):
            file_id = (
                pdata.get("file_id")
                or pdata.get("output_file_id")
                or (pdata.get("audio_file") or {}).get("file_id")
            )
            print(f"  Completato! file_id={file_id}")
            print(f"  Full poll response: {pdata}")

            if not file_id:
                print("Nessun file_id trovato nella risposta!")
                return False

            # Step 3: retrieve
            async with httpx.AsyncClient(timeout=15) as client:
                fdata = await client.get(
                    f"{BASE_URL}/files/retrieve",
                    headers=HEADERS,
                    params={"file_id": file_id, "GroupId": GROUP_ID},
                )
            print(f"  File retrieve: {fdata.json()}")
            url = (fdata.json().get("download_url")
                   or fdata.json().get("url")
                   or (fdata.json().get("file") or {}).get("download_url"))

            if not url:
                print("Nessun download_url!")
                return False

            # Step 4: download
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                dl = await client.get(url)
            out_path.write_bytes(dl.content)
            print(f"Audio salvato: {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")
            return True

        if status in ("Failed", "failed", "error", -1):
            print(f"Job FALLITO: {pdata}")
            return False

    print("Timeout: job non completato in 2.5 minuti")
    return False


async def main():
    print(f"API Key: {API_KEY[:20]}…")
    print(f"Group ID: {GROUP_ID}")

    if not API_KEY or not GROUP_ID:
        print("ERROR: MINIMAX_API_KEY o MINIMAX_GROUP_ID non impostati")
        sys.exit(1)

    out_dir = Path("./storage/test_audio")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Prova prima con una voce built-in comune di MiniMax
    # Voci italiane note: "Italian_WarmMale", "Italian_ExpressiveFemale"
    # oppure voci generiche: "male-qn-qingse-jingying-v2", "audiobook_male_2"
    test_voices = [
        "Italian_WarmMale",
        "Italian_ExpressiveFemale",
        "audiobook_male_2",
        "male-qn-qingse-jingying-v2",
    ]

    text = (
        "Ciao, questo è un test della sintesi vocale MiniMax. "
        "Il sistema funziona correttamente per la generazione di audiolibri in italiano."
    )

    # Prima prova sincrona (più veloce per debug)
    for voice_id in test_voices:
        out_path = out_dir / f"test_{voice_id}.mp3"
        ok = await test_t2a_sync(voice_id, text, out_path)
        if ok:
            print(f"\n✓ Sintesi sincrona OK con voice_id='{voice_id}'")
            print(f"  Ascolta: {out_path}")
            return

    # Se sincrona fallisce, prova asincrona con primo voice_id
    print("\nSintesi sincrona non riuscita — provo asincrona…")
    for voice_id in test_voices:
        out_path = out_dir / f"test_async_{voice_id}.mp3"
        ok = await test_t2a_async(voice_id, text, out_path)
        if ok:
            print(f"\n✓ Sintesi asincrona OK con voice_id='{voice_id}'")
            print(f"  Ascolta: {out_path}")
            return

    print("\n✗ Tutti i test falliti. Controlla i log sopra.")


if __name__ == "__main__":
    asyncio.run(main())
