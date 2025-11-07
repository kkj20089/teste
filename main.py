import requests, hashlib, urllib.parse, json, os, re, time, socket
from urllib.parse import urlparse
from flask import Flask, redirect, jsonify
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========== HARDCODED SETTINGS ==========
PORTAL_URL = "http://tatatv.cc/stalker_portal/c/"
MAC_ADDRESS = "00:1A:79:00:13:DA"
PORTAL_TYPE = "1"  # 1 = stalker_portal/c/, 2 = /c/
MODE = "2"         # 1 = Offline, 2 = Online
# =======================================

# ===== Utility Functions =====
def generate_device_info(mac):
    mac_upper = mac.upper()
    SN = hashlib.md5(mac.encode('utf-8')).hexdigest().upper()
    SNCUT = SN[:13]
    DEV1 = hashlib.sha256(mac.encode('utf-8')).hexdigest().upper()
    SIGNATURE = hashlib.sha256((SNCUT + mac).encode('utf-8')).hexdigest().upper()
    return {"SNCUT": SNCUT, "Device_ID1": DEV1, "Signature": SIGNATURE}

def normalize_url_and_name(input_url):
    url = input_url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    url = re.sub(r'/(stalker_portal(/c)?|c)/?$', '', url)
    parsed = urlparse(url)
    domain_parts = parsed.netloc.split('.')
    name_part = "".join(domain_parts[-2:]) if len(domain_parts) >= 2 else parsed.netloc.replace(".", "")
    return url.rstrip("/"), name_part.lower()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

# ===== Create Stream Link =====
def create_link(base_url, cmd, session, headers, portal_type, retries=3):
    cmd = cmd.strip().replace("ffrt ", "")
    prefix = "stalker_portal" if portal_type == "1" else "c"
    url = f"{base_url}/{prefix}/server/load.php?type=itv&action=create_link&cmd={urllib.parse.quote(cmd)}&JsHttpRequest=1-xml"
    for attempt in range(retries):
        try:
            resp = session.get(url, headers=headers, timeout=8)
            if resp.status_code != 200:
                continue
            js = resp.json().get("js", {})
            if isinstance(js, dict) and "cmd" in js and js["cmd"]:
                return js["cmd"].replace("ffrt ", "").strip()
        except Exception:
            time.sleep(1)
            continue
    return None

# ===== Initialize Portal =====
def init_portal_session(base_url, mac, portal_type):
    device_info = generate_device_info(mac)
    session = requests.Session()
    prefix = "stalker_portal" if portal_type == "1" else "c"
    handshake_url = f"{base_url}/{prefix}/server/load.php?action=handshake&type=stb&token=&JsHttpRequest=1-xml"
    headers = {
        "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
        "X-User-Agent": "Model: MAG250; Link: WiFi",
        "Referer": f"{base_url}/{prefix}/c/",
        "Accept": "*/*",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
    }
    session.cookies.set("mac", mac)
    resp = session.post(handshake_url, headers=headers, timeout=10)
    js = resp.json().get("js", {})
    token = js.get("token")
    if not token:
        raise RuntimeError("Handshake failed")

    headers["Authorization"] = f"Bearer {token}"
    sn, device_id, signature = device_info["SNCUT"], device_info["Device_ID1"], device_info["Signature"]
    profile_url = f"{base_url}/{prefix}/server/load.php?type=stb&action=get_profile&sn={sn}&device_id={device_id}&signature={signature}&JsHttpRequest=1-xml"
    session.get(profile_url, headers=headers, timeout=10)
    return session, headers

# ===== Fetch All Channels =====
def fetch_all_channels(base_url, session, headers, portal_type):
    prefix = "stalker_portal" if portal_type == "1" else "c"
    url = f"{base_url}/{prefix}/server/load.php?type=itv&action=get_all_channels&JsHttpRequest=1-xml"
    resp = session.get(url, headers=headers, timeout=15)
    try:
        data = resp.json()
        return data.get("js", {}).get("data", [])
    except Exception:
        return []

# ===== MAIN =====
base_url, save_name = normalize_url_and_name(PORTAL_URL)
session, headers = init_portal_session(base_url, MAC_ADDRESS, PORTAL_TYPE)
channels = fetch_all_channels(base_url, session, headers, PORTAL_TYPE)
if not channels:
    print("❌ No channels found.")
    exit(1)

# ===== FLASK ONLINE MODE =====
app = Flask(__name__)
ip = get_local_ip()
port = int(os.getenv("PORT", 10000))  # Render uses PORT env var
filename = f"Online_{save_name}.m3u"

@app.route("/getlink/<int:ch_id>")
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
    public_url = os.getenv("RENDER_EXTERNAL_URL", f"http://{ip}:{port}")
    return f"📡 IPTV Playlist Ready — <a href='{public_url}/{filename}'>Open Playlist</a>"

# Generate playlist
public_url = os.getenv("RENDER_EXTERNAL_URL", f"http://{ip}:{port}")
with open(filename, "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    for ch in channels:
        name, ch_id = ch.get("name", "Unknown"), ch.get("id")
        logo = ch.get("logo", "")
        logo_url = f"{base_url}/stalker_portal/misc/logos/320/{logo}" if PORTAL_TYPE == "1" else logo
        f.write(f'#EXTINF:-1 group-title="All Channels" tvg-logo="{logo_url}",{name}\n{public_url}/getlink/{ch_id}\n')

print(f"✅ Playlist Ready: {public_url}/{filename}")
app.run(host="0.0.0.0", port=port, debug=False)
