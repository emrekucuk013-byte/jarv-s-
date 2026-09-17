"""HTTP server: serves the HUD page and a small JSON API over the shared agent core.

Endpoints
  GET  /                    the page
  GET  /api/state           reminders, memory, notices, voice capabilities
  POST /api/turn {text}     one agent turn; the response is a stream of JSON lines:
                            {"type":"text","delta"} {"type":"tool",...} {"type":"confirm","id","description"}
                            {"type":"done","text"} {"type":"error","message"}
  POST /api/confirm {id, approved}
  POST /api/stt (audio body) -> {"text"}          Deepgram, when a key is set
  GET  /api/tts?text=...    -> audio/mpeg          ElevenLabs, when a key is set
  POST /api/dismiss {id}    (0 = all)
  POST /api/memory {facts:[...]}
  POST /api/pause | /api/resume
"""

from __future__ import annotations

import json
import queue
import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import STATE_DIR, secret
from ..memory import default_store
from .confirm import WebConfirmer

PAGE = Path(__file__).with_name("page.html")


class WebApp:
    """Everything the handler needs, built once."""

    def __init__(self, rt, config):
        self.rt = rt
        self.config = config
        self.confirmer = WebConfirmer(float(config.get("web", "confirm_timeout", 90)))
        self.agent = rt.new_agent("web", self.confirmer)
        self.memory = default_store(config)
        self.turn_lock = threading.Lock()
        v = config["voice"]
        self.transcriber = None
        self.speaker = None
        dg, el = secret("DEEPGRAM_API_KEY"), secret("ELEVENLABS_API_KEY")
        if dg:
            from ..voice.stt import DeepgramTranscriber
            self.transcriber = DeepgramTranscriber(dg, v.get("stt_model", "nova-3"), v.get("stt_language", "en"))
        if el and v.get("tts_provider", "elevenlabs") == "elevenlabs":
            from ..voice.tts import ElevenLabsSpeaker

            class _NoPlayer:
                sample_rate = 16000
                def play(self, chunks): pass
                def stop(self): pass
            self.speaker = ElevenLabsSpeaker(el, v["tts_voice_id"], _NoPlayer(), v.get("tts_model_id", "eleven_turbo_v2_5"))

    # -- state -------------------------------------------------------------

    def state(self) -> dict:
        from ..tools.reminders import ReminderStore
        store = ReminderStore(self.config.path("reminders", "path", STATE_DIR / "reminders.json"))
        return {
            "name": self.rt.agent.name,
            "reminders": store.load(),
            "memories": self.memory.load(),
            "notices": [{"id": n.id, "level": n.level, "text": n.text, "check": n.check, "created": n.created}
                        for n in self.rt.inbox.pending()],
            "paused": self.rt.kill_switch.engaged,
            "provider": self.config["model"].get("provider"),
            "model": self.config["model"].get("ollama_model") if self.config["model"].get("provider") == "ollama" else self.config["model"].get("name"),
            "stt": "deepgram" if self.transcriber else "browser",
            "tts": "elevenlabs" if self.speaker else "browser",
        }

    # -- one turn, streamed ---------------------------------------------------

    def run_turn(self, text: str, out: queue.Queue) -> None:
        with self.turn_lock:
            self.confirmer.bind(out)
            try:
                result = self.agent.run_turn(
                    text,
                    on_text=lambda d: out.put({"type": "text", "delta": d}),
                    on_tool=lambda e: out.put({"type": "tool", "tool": e["tool"], "ok": not e["is_error"],
                                               "declined": bool(e.get("declined")), "flagged": bool(e.get("flagged"))}),
                )
                if result.error:
                    out.put({"type": "error", "message": result.error, "text": result.text})
                out.put({"type": "done", "text": result.text})
            except Exception as e:  # noqa: BLE001
                out.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
                out.put({"type": "done", "text": ""})
            finally:
                out.put(None)


def make_handler(app: WebApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Vyron/1"

        def log_message(self, fmt, *args):  # quiet
            pass

        # helpers
        def _json(self, code: int, data) -> None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _jbody(self) -> dict:
            try:
                return json.loads(self._body() or b"{}")
            except json.JSONDecodeError:
                return {}

        # GET
        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                body = PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/api/state":
                self._json(200, app.state())
            elif u.path == "/api/tts":
                text = parse_qs(u.query).get("text", [""])[0].strip()
                if not app.speaker:
                    return self._json(404, {"error": "no ElevenLabs key on the server"})
                try:
                    audio = app.speaker.synthesize(text[:1000])
                except Exception as e:  # noqa: BLE001
                    return self._json(502, {"error": str(e)})
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(audio)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(audio)
            else:
                self._json(404, {"error": "not found"})

        # POST
        def do_POST(self):
            u = urlparse(self.path)
            if u.path == "/api/turn":
                text = str(self._jbody().get("text", "")).strip()
                if not text:
                    return self._json(400, {"error": "text is required"})
                out: queue.Queue = queue.Queue()
                threading.Thread(target=app.run_turn, args=(text, out), daemon=True).start()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                try:
                    while True:
                        ev = out.get()
                        if ev is None:
                            break
                        self.wfile.write((json.dumps(ev, ensure_ascii=False) + "\n").encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            elif u.path == "/api/confirm":
                b = self._jbody()
                ok = app.confirmer.answer(str(b.get("id", "")), bool(b.get("approved")))
                self._json(200 if ok else 404, {"ok": ok})
            elif u.path == "/api/stt":
                if not app.transcriber:
                    return self._json(404, {"error": "no Deepgram key on the server"})
                audio = self._body()
                ctype = (self.headers.get("Content-Type") or "audio/wav").split(";")[0]
                try:
                    text = app.transcriber.transcribe(audio, ctype)
                except Exception as e:  # noqa: BLE001
                    return self._json(502, {"error": str(e)})
                self._json(200, {"text": text})
            elif u.path == "/api/dismiss":
                nid = int(self._jbody().get("id", 0) or 0)
                n = app.rt.inbox.dismiss_all() if nid == 0 else int(app.rt.inbox.dismiss(nid))
                self._json(200, {"dismissed": n})
            elif u.path == "/api/memory":
                facts = [str(f).strip() for f in self._jbody().get("facts", []) if str(f).strip()]
                app.memory.save(facts)
                app.rt.audit.log("memory_edit", source="web", count=len(facts))
                self._json(200, {"ok": True, "count": len(facts)})
            elif u.path in ("/api/pause", "/api/resume"):
                (app.rt.kill_switch.engage if u.path == "/api/pause" else app.rt.kill_switch.release)()
                app.rt.audit.log("kill_switch", source="web", engaged=app.rt.kill_switch.engaged)
                self._json(200, {"paused": app.rt.kill_switch.engaged})
            else:
                self._json(404, {"error": "not found"})

    return Handler


# -- TLS: browsers only allow the microphone on https (or localhost) ----------------

def ensure_cert(cert_dir: Path) -> tuple[Path, Path] | None:
    cert, key = cert_dir / "web-cert.pem", cert_dir / "web-key.pem"
    if cert.exists() and key.exists():
        return cert, key
    cert_dir.mkdir(parents=True, exist_ok=True)
    host = socket.gethostname()
    try:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                        "-keyout", str(key), "-out", str(cert), "-subj", f"/CN={host}",
                        "-addext", f"subjectAltName=DNS:{host},DNS:{host}.local,DNS:localhost,IP:127.0.0.1"],
                       check=True, capture_output=True)
        return cert, key
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def lan_addresses() -> list[str]:
    names = []
    host = socket.gethostname()
    if host:
        names.append(host if host.endswith(".local") else host + ".local")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        names.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return names


def open_in_browser(url: str) -> None:
    import platform
    import webbrowser
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


def serve(rt, config, port: int | None = None, use_https: bool | None = None, open_browser: bool = True) -> int:
    app = WebApp(rt, config)
    port = int(port or config.get("web", "port", 8080))
    use_https = config.get("web", "https", True) if use_https is None else use_https
    handler = make_handler(app)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    httpd.daemon_threads = True
    # A plain-http listener on this computer only: localhost counts as secure, so the
    # microphone works here with no certificate warning. Other devices use the https port.
    local_port = int(config.get("web", "local_port", port + 1))
    local = ThreadingHTTPServer(("127.0.0.1", local_port), handler)
    local.daemon_threads = True
    threading.Thread(target=local.serve_forever, daemon=True).start()
    scheme = "http"
    if use_https:
        pair = ensure_cert(config.path("web", "cert_dir", STATE_DIR))
        if pair:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(pair[0]), str(pair[1]))
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            scheme = "https"
        else:
            print("Couldn't create a certificate (openssl missing); serving plain http. Microphones in browsers need https.")
    here = f"http://localhost:{local_port}"
    print(f"\n{rt.agent.name} HUD")
    print(f"   on this computer:            {here}")
    print("   on a phone/tablet (same Wi-Fi):")
    for a in lan_addresses():
        print(f"      {scheme}://{a}:{port}")
    if scheme == "https":
        print("   (phones will warn once that the certificate is self-made: choose 'Show details' / 'Visit anyway')")
    print("Keep this window open. Ctrl-C to stop.\n")
    if open_browser:
        open_in_browser(here)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        local.server_close()
    return 0
