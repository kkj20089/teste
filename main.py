import requests, hashlib, urllib.parse, json, os, socket, re, time
from urllib.parse import urlparse
from flask import Flask, redirect, jsonify
from concurrent.futures import ThreadPoolExecutor, as_completed

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

# ===== Get Local IP =====
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

# ===== Session JSON =====
def get_session_filename(portal_name): return f"session_{portal_name}.json"

def save_session_json(base_url, mac, token, portal_type, portal_name):
    portal_url = f"{base_url}/stalker_portal/c/" if portal_type == "1" else f"{base_url}/c/"
    cookie_str = f"mac={mac}; stb_lang=en; timezone=GMT"
    headers_list = [
        "User-Agent: Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
        "X-User-Agent: Model: MAG250; Link: WiFi",
        f"Referer: {portal_url}",
        "Accept: */*",
        "Connection: Keep-Alive",
        "Accept-Encoding: gzip",
        f"Cookie: {cookie_str}",
        f"Authorization: Bearer {token}"
    ]
    data = {"portal": portal_url, "mac": mac, "token": token, "cookie": cookie_str,
            "headers": headers_list, "portal_type": portal_type}
    filename = get_session_filename(portal_name)
    with open(filename, "w") as f: json.dump(data, f, indent=4)
    print(f"💾 Saved {filename}")

# ===== Create Stream Link =====
def create_link(base_url, cmd, session, headers, portal_type, retries=3):
    cmd = cmd.strip().replace("ffrt ", "")
    prefix = "stalker_portal" if portal_type == "1" else "c"
    url = f"{base_url}/{prefix}/server/load.php?type=itv&action=create_link&cmd={urllib.parse.quote(cmd)}&JsHttpRequest=1-xml"
    for attempt in range(retries):
        try:
            resp = session.get(url, headers=headers, timeout=8)
            if resp.status_code != 200:
                print(f"[create_link] HTTP {resp.status_code} → {url}")
                continue
            js = resp.json().get("js", {})
            if isinstance(js, dict) and "cmd" in js and js["cmd"]:
                return js["cmd"].replace("ffrt ", "").strip()
            else:
                print(f"[create_link] Missing cmd in response for: {cmd}")
        except Exception as e:
            print(f"[create_link] Error ({attempt+1}/{retries}): {e}")
            time.sleep(1)
            continue
    return None


# ===== Initialize Portal =====
def init_portal_session(base_url, mac, device_info, portal_type, portal_name):
    session = requests.Session()
    prefix = "stalker_portal" if portal_type == "1" else "c"

    print("[*] Performing new handshake...")
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
    try:
        resp = session.post(handshake_url, headers=headers, timeout=10)
        js = resp.json().get("js", {})
        token = js.get("token")
        if not token:
            print("❌ Handshake failed.")
            exit(1)
        save_session_json(base_url, mac, token, portal_type, portal_name)
        headers["Authorization"] = f"Bearer {token}"
    except Exception as e:
        print("❌ Handshake failed:", str(e))
        exit(1)

    print("[*] Validating via get_profile...")
    sn, device_id, device_id2, signature = device_info["SNCUT"], device_info["Device_ID1"], device_info["Device_ID1"], device_info["Signature"]
    profile_url = f"{base_url}/{prefix}/server/load.php?type=stb&action=get_profile&sn={sn}&device_id={device_id}&device_id2={device_id2}&signature={signature}&JsHttpRequest=1-xml"
    resp = session.get(profile_url, headers=headers)
    if "js" not in resp.text:
        print("❌ Profile validation failed.")
        exit(1)
    print("✅ Profile validated successfully.\n")
    return session, headers

# ===== Fetch All Channels =====
def fetch_all_channels(base_url, session, headers, portal_type):
    prefix = "stalker_portal" if portal_type == "1" else "c"
    url = f"{base_url}/{prefix}/server/load.php?type=itv&action=get_all_channels&JsHttpRequest=1-xml"
    print("[*] Fetching full channel list...")
    resp = session.get(url, headers=headers, timeout=15)
    try:
        data = resp.json()
        return data.get("js", {}).get("data", [])
    except Exception:
        print("[!] Portal did not return valid JSON.")
        return []

# ===== Main =====
# ===== Main =====
base_url_input = "http://tatatv.cc/stalker_portal/c/"
mac = "00:1A:79:00:13:DA"
portal_type = "1"   # 1 = stalker_portal/c/
mode = "2"          # 2 = Online (Flask middleware)

base_url, save_name = normalize_url_and_name(base_url_input)


device_info = generate_device_info(mac)
session, headers = init_portal_session(base_url, mac, device_info, portal_type, save_name)
channels = fetch_all_channels(base_url, session, headers, portal_type)

if not channels:
    print("❌ No channels found. Exiting.")
    exit(1)

# ===== OFFLINE MODE =====
if mode == "1":
    filename = f"Offline_{save_name}.m3u"
    print(f"\n[*] Generating offline playlist: {filename}")
    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        def fetch_real_link(ch):
            name, cmd = ch.get("name", "Unknown"), ch.get("cmd", "").strip()
            if not cmd:
                return name, None
            if "ffmpeg" in cmd:
                return name, cmd.replace("ffmpeg ", "").strip()
            return name, create_link(base_url, cmd, session, headers, portal_type)

        with ThreadPoolExecutor(max_workers=6) as executor:
            for future in as_completed([executor.submit(fetch_real_link, ch) for ch in channels]):
                name, real_url = future.result()
                ch = next((c for c in channels if c.get("name") == name), None)
                logo = ch.get("logo", "")
                logo_url = f"{base_url}/stalker_portal/misc/logos/320/{logo}" if portal_type == "1" else logo
                group_title = ch.get("tv_genre_title", "All Channels")
                if real_url:
                    f.write(f'#EXTINF:-1 group-title="{group_title}" tvg-logo="{logo_url}",{name}\n{real_url}\n')
                    print(f"✅ {name}")
                else:
                    print(f"❌ {name}")

    print(f"\n✅ Offline playlist saved as {filename}")
    exit(0)

# ===== ONLINE MODE =====
app = Flask(__name__)
ip = get_local_ip()
port = 5000
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
        real_url = create_link(base_url, cmd, session, headers, portal_type)
    if not real_url:
        return jsonify({"error": "Failed to get link"}), 500
    return redirect(real_url, code=302)

@app.route(f"/{filename}")
def serve_m3u():
    return open(filename, "r", encoding="utf-8").read(), 200, {"Content-Type": "application/x-mpegurl"}

@app.route("/")
def index():
    return f"📡 IPTV Playlist Ready — <a href='http://{ip}:{port}/{filename}'>Open Playlist</a>"

with open(filename, "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    for ch in channels:
        name, ch_id = ch.get("name", "Unknown"), ch.get("id")
        logo = ch.get("logo", "")
        logo_url = f"{base_url}/stalker_portal/misc/logos/320/{logo}" if portal_type == "1" else logo
        f.write(f'#EXTINF:-1 group-title="All Channels" tvg-logo="{logo_url}",{name}\nhttp://{ip}:{port}/getlink/{ch_id}\n')

print(f"\n✅ Online playlist saved at: {os.path.abspath(filename)}")
print(f"🌐 Playlist URL: http://{ip}:{port}/{filename}")
print("📱 Open this in TiviMate, OTT Navigator, or VLC.\n")

app.run(host="0.0.0.0", port=port, debug=False)
