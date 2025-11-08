import requests, hashlib, urllib.parse, json, os, socket, re, time
from urllib.parse import urlparse, quote
from flask import Flask, redirect, jsonify

from concurrent.futures import ThreadPoolExecutor, as_completed

# ===== CONFIG =====
WORKER_URL = "https://kip.kkjkkj20089.workers.dev/"  # Cloudflare Worker proxy
BASE_URL_INPUT = "http://tatatv.cc/stalker_portal/c/"
MAC = "00:1A:79:00:13:DA"
PORTAL_TYPE = "1"  # 1 = stalker_portal/c/
MODE = "2"         # 2 = Online (Flask middleware)

# ===== Device Info Generator =====
def generate_device_info(mac):
    mac_upper = mac.upper()
    mac_encoded = urllib.parse.quote(mac_upper)
    SN = hashlib.md5(mac.encode('utf-8')).hexdigest().upper()
    SNCUT = SN[:13]
    DEV1 = hashlib.sha256(mac.encode('utf-8')).hexdigest().upper()
    DEV2 = hashlib.sha256(SNCUT.encode('utf-8')).hexdigest().upper()
    SIGNATURE = hashlib.sha256((SNCUT + mac).encode('utf-8')).hexdigest().upper()
    return {"SN": SN, "SNCUT": SNCUT, "Device_ID1": DEV1, "Device_ID2": DEV2, "Signature": SIGNATURE, "MAC_Encoded": mac_encoded}

# ===== Normalize URL =====
def normalize_url_and_name(input_url):
    url = input_url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    url = re.sub(r'/(stalker_portal(/c)?|c)/?$', '', url)
    parsed = urlparse(url)
    domain_parts = parsed.netloc.split('.')
    name_part = "".join(domain_parts[-2:]) if len(domain_parts) >= 2 else parsed.netloc.replace(".", "")
    return url.rstrip("/"), name_part.lower()

# ===== Local IP =====
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

# ===== Session Filename =====
def get_session_filename(portal_name): return f"session_{portal_name}.json"

def save_session_json(base_url, mac, token, portal_type, portal_name):
    data = {
        "base_url": base_url,
        "mac": mac,
        "token": token,
        "portal_type": portal_type
    }
    filename = get_session_filename(portal_name)
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)
    print(f"💾 Saved {filename}")

# ===== Proxy Helper =====
def proxy_url(url):
    return f"{WORKER_URL}?url={quote(url, safe='')}"

# ===== Create Stream Link =====
def create_link(base_url, cmd, session, headers, portal_type, retries=3):
    cmd = cmd.strip().replace("ffrt ", "")
    prefix = "stalker_portal" if portal_type == "1" else "c"
    url = f"{base_url}/{prefix}/server/load.php?type=itv&action=create_link&cmd={urllib.parse.quote(cmd)}&JsHttpRequest=1-xml"
    for attempt in range(retries):
        try:
            resp = session.get(proxy_url(url), headers=headers, timeout=8)
            if resp.status_code != 200:
                print(f"[create_link] HTTP {resp.status_code} → {url}")
                continue
            js = resp.json().get("js", {})
            if isinstance(js, dict) and "cmd" in js and js["cmd"]:
                return js["cmd"].replace("ffrt ", "").strip()
        except Exception as e:
            print(f"[create_link] Error ({attempt+1}/{retries}): {e}")
            time.sleep(1)
    return None

# ===== Handshake =====
def init_portal_session(base_url, mac, device_info, portal_type, portal_name):
    session = requests.Session()
    prefix = "stalker_portal" if portal_type == "1" else "c"
    print("[*] Performing new handshake...")

    handshake_url = f"{base_url}/{prefix}/server/load.php?action=handshake&type=stb&token=&JsHttpRequest=1-xml"
    headers = {
        "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG254 stbapp ver: 4 rev: 273 Safari/533.3",
        "X-User-Agent": "Model: MAG254; Link: WiFi",
        "Referer": f"{base_url}/{prefix}/c/",
        "Accept": "*/*",
        "Accept-Encoding": "gzip"
    }
    session.cookies.set("mac", mac)

    resp = session.post(proxy_url(handshake_url), headers=headers, timeout=15)
    js = resp.json().get("js", {})
    token = js.get("token")
    if not token:
        print("❌ Handshake failed (no token).")
        print(resp.text[:400])
        exit(1)
    save_session_json(base_url, mac, token, portal_type, portal_name)
    headers["Authorization"] = f"Bearer {token}"

    print("[*] Validating via get_profile...")
    sn, device_id, device_id2, signature = (
        device_info["SNCUT"],
        device_info["Device_ID1"],
        device_info["Device_ID1"],
        device_info["Signature"],
    )
    profile_url = f"{base_url}/{prefix}/server/load.php?type=stb&action=get_profile&sn={sn}&device_id={device_id}&device_id2={device_id2}&signature={signature}&JsHttpRequest=1-xml"
    resp = session.get(proxy_url(profile_url), headers=headers)
    if "js" not in resp.text:
        print("❌ Profile validation failed.")
        exit(1)
    print("✅ Profile validated successfully.\n")
    return session, headers

def fetch_all_channels(base_url, session, headers, portal_type, mac, device_info, portal_name):
    prefix = "stalker_portal" if portal_type == "1" else "c"
    url = f"{base_url}/{prefix}/server/load.php?type=itv&action=get_all_channels&JsHttpRequest=1-xml"
    print("[*] Fetching full channel list...")

    resp = session.get(proxy_url(url), headers=headers, timeout=20)

    # Handle expired session or bad JSON
    text = resp.text.strip()
    if not text or "Unauthorized" in text or "token" in text.lower():
        print("⚠️ Session expired or unauthorized. Performing new handshake...")
        session, headers = init_portal_session(base_url, mac, device_info, portal_type, portal_name)
        resp = session.get(proxy_url(url), headers=headers, timeout=20)

    try:
        data = resp.json()
        return data.get("js", {}).get("data", []), session, headers
    except Exception as e:
        print("[!] Portal did not return valid JSON:", e)
        print("Response:", resp.text[:400])
        return [], session, headers


# ===== MAIN =====
base_url, save_name = normalize_url_and_name(BASE_URL_INPUT)
device_info = generate_device_info(MAC)

# Use saved session if possible
session_file = get_session_filename(save_name)
if os.path.exists(session_file):
    try:
        with open(session_file) as f:
            sess = json.load(f)
            token = sess.get("token")
            if token:
                print("✅ Loaded existing session token, skipping handshake.")
                session = requests.Session()
                headers = {
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "*/*",
                }
            else:
                raise Exception("Empty token")
    except Exception as e:
        print("⚠️ Session invalid, retrying handshake:", e)
        session, headers = init_portal_session(base_url, MAC, device_info, PORTAL_TYPE, save_name)
else:
    session, headers = init_portal_session(base_url, MAC, device_info, PORTAL_TYPE, save_name)

channels, session, headers = fetch_all_channels(base_url, session, headers, PORTAL_TYPE, MAC, device_info, save_name)

if not channels:
    print("❌ No channels found.")
    exit(1)

# ===== Flask App =====
app = Flask(__name__)
ip = get_local_ip()
port = int(os.getenv("PORT", 5000))
filename = f"Online_{save_name}.m3u"

@app.route("/getlink/<ch_id>")
def getlink(ch_id):
    ch = next((c for c in channels if str(c.get("id")) == str(ch_id)), None)
    if not ch:
        return jsonify({"error": "Channel not found"}), 404
    cmd = ch.get("cmd", "").strip()
    if "ffmpeg" in cmd:
        real_url = cmd.replace("ffmpeg ", "").strip()
    else:
        real_url = create_link(base_url, cmd, session, headers, PORTAL_TYPE)
    if not real_url:
        return jsonify({"error": "Failed to get link"}), 500
    return redirect(real_url, code=302)

@app.route(f"/{filename}")
def serve_m3u():
    return open(filename, "r", encoding="utf-8").read(), 200, {"Content-Type": "application/x-mpegurl"}

@app.route("/")
def index():
    full_url = f"https://{os.getenv('REPL_SLUG', 'localhost')}.{os.getenv('REPL_OWNER', 'local')}.repl.co/{filename}"
    return f"📡 IPTV Playlist Ready — <a href='{full_url}'>Open Playlist</a>"

@app.route("/healthz")
def healthz():
    return "ok", 200

# ===== Write Playlist =====
with open(filename, "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    for ch in channels:
        name, ch_id = ch.get("name", "Unknown"), ch.get("id")
        logo = ch.get("logo", "")
        logo_url = f"{base_url}/stalker_portal/misc/logos/320/{logo}" if PORTAL_TYPE == "1" else logo
        f.write(f'#EXTINF:-1 group-title="All Channels" tvg-logo="{logo_url}",{name}\nhttps://{os.getenv("REPL_SLUG","localhost")}.{os.getenv("REPL_OWNER","local")}.repl.co/getlink/{ch_id}\n')

print(f"\n✅ Playlist ready: {filename}")
print(f"🌍 Access on: https://{os.getenv('REPL_SLUG','localhost')}.{os.getenv('REPL_OWNER','local')}.repl.co/{filename}\n")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))


