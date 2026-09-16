"""`python -m vyron --doctor`: says exactly what is and isn't ready on this machine."""

from __future__ import annotations

import importlib
import sys

from .config import ENV_PATH, load_config, load_env, secret

OK, BAD, WARN = "  [ok]  ", "  [!!]  ", "  [..]  "


def _check_http(url: str, headers: dict[str, str]) -> tuple[bool, str]:
    try:
        import httpx
        r = httpx.get(url, headers=headers, timeout=15)
    except ImportError:
        return False, "httpx not installed (pip install -e '.[voice]')"
    except Exception as e:  # noqa: BLE001
        return False, f"unreachable ({type(e).__name__})"
    if r.status_code == 200:
        return True, "key accepted"
    if r.status_code in (401, 403):
        return False, f"key rejected (HTTP {r.status_code})"
    return False, f"HTTP {r.status_code}"


def run_doctor() -> int:
    load_env()
    config = load_config()
    problems = 0
    lines: list[str] = []

    def report(ok: bool | None, text: str) -> None:
        nonlocal problems
        if ok is False:
            problems += 1
        lines.append((OK if ok else BAD if ok is False else WARN) + text)

    v = sys.version_info
    report(v >= (3, 11), f"Python {v.major}.{v.minor} (needs 3.11+)")
    report(ENV_PATH.exists(), f".env file at {ENV_PATH}" + ("" if ENV_PATH.exists() else " (copy .env.example to .env)"))

    for pkg, why in [("anthropic", "text mode"), ("httpx", "voice"), ("sounddevice", "voice"), ("numpy", "voice"), ("pynput", "voice")]:
        try:
            importlib.import_module(pkg)
            report(True, f"package {pkg} ({why})")
        except ImportError:
            report(False if why == "text mode" else None, f"package {pkg} missing ({why}; pip install -e '.[voice]')")

    # Text brain
    provider = config["model"].get("provider", "ollama")
    key = secret("ANTHROPIC_API_KEY")
    if provider == "ollama":
        host = config["model"].get("ollama_host", "http://localhost:11434")
        model = config["model"].get("ollama_model", "llama3.2")
        try:
            import httpx
            tags = httpx.get(f"{host}/api/tags", timeout=5).json().get("models", [])
            have = {m.get("name", "").split(":")[0] for m in tags} | {m.get("name", "") for m in tags}
            if model in have or model.split(":")[0] in have:
                report(True, f"Ollama running at {host}; model {model} is downloaded (free brain)")
            else:
                report(False, f"Ollama is running but model {model} isn't downloaded: run  ollama pull {model}")
        except Exception as e:  # noqa: BLE001
            report(False, f"Ollama not reachable at {host} ({type(e).__name__}). Install/start it: https://ollama.com/download")
    elif not key:
        report(False, "ANTHROPIC_API_KEY not set: text mode can't start")
    else:
        try:
            import anthropic
            anthropic.Anthropic(api_key=key).models.retrieve(config["model"]["name"])
            report(True, f"Anthropic key accepted; model {config['model']['name']} available")
        except Exception as e:  # noqa: BLE001
            report(False, f"Anthropic check failed: {type(e).__name__}: {str(e)[:120]}")

    # Ears
    dg = secret("DEEPGRAM_API_KEY")
    if not dg:
        report(None, "DEEPGRAM_API_KEY not set: voice mode won't start")
    else:
        ok, msg = _check_http("https://api.deepgram.com/v1/projects", {"Authorization": f"Token {dg}"})
        report(ok, f"Deepgram: {msg}")

    # Mouth
    el = secret("ELEVENLABS_API_KEY")
    voice_id = config["voice"].get("tts_voice_id", "")
    if not el:
        report(None, "ELEVENLABS_API_KEY not set: voice mode won't start")
    else:
        ok, msg = _check_http(f"https://api.elevenlabs.io/v1/voices/{voice_id}", {"xi-api-key": el})
        report(ok, f"ElevenLabs voice {voice_id}: {msg}")

    # Audio devices
    try:
        import sounddevice as sd
        inp, out = sd.default.device
        names = sd.query_devices()
        report(inp >= 0, f"microphone: {names[inp]['name'] if inp >= 0 else 'none found'}")
        report(out >= 0, f"speaker: {names[out]['name'] if out >= 0 else 'none found'}")
    except Exception as e:  # noqa: BLE001
        report(None, f"audio devices not checked ({type(e).__name__})")

    print(f"{config['assistant']['name']} doctor\n" + "\n".join(lines))
    if problems:
        print(f"\n{problems} thing(s) need fixing before everything works. Text mode needs only the Anthropic key.")
    else:
        print("\nEverything checks out. Run: python -m vyron   (or --voice)")
    return 1 if problems else 0
