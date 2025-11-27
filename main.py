import requests
import hashlib
import urllib.parse
import json
import os
import socket
import re
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, redirect, jsonify, send_from_directory, make_response


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

# ===== Normalize URL & Auto Filename =====
def normalize_url_and_name(input_url):
    url = input_url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url

    # Remove trailing /c, /c/, /stalker_portal, /stalker_portal/, /stalker_portal/c etc.
    url = re.sub(r'/(stalker_portal(/c)?|c)/?$', '', url)

    parsed = urlparse(url)
    domain_parts = parsed.netloc.split('.')
    name_part = "".join(domain_parts[-2:]) if len(domain_parts) >= 2 else parsed.netloc.replace(".", "")
    return url.rstrip("/"), name_part.lower()

# ===== Get Local IP (Original Function) =====
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

# ===== Session file naming =====
def get_session_filename(portal_name):
    return f"session_{portal_name}.json"

# ===== Save session.json =====
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
    data = {"portal": portal_url, "mac": mac, "token": token, "cookie": cookie_str, "headers": headers_list, "portal_type": portal_type}
    filename = get_session_filename(portal_name)
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)
    print(f"💾 Saved {filename}")

# ===== Load session.json =====
def load_session_json(portal_name):
    filename = get_session_filename(portal_name)
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return None

# ===== create_link (with retries) =====
def create_link(base_url, cmd, session, headers, portal_type, retries=3):
    cmd = cmd.strip().replace("ffrt ", "")
    prefix = "stalker_portal" if portal_type == "1" else "c"
    url = f"{base_url}/{prefix}/server/load.php?type=itv&action=create_link&cmd={urllib.parse.quote(cmd)}&JsHttpRequest=1-xml"
    for _ in range(retries):
        try:
            resp = session.get(url, headers=headers, timeout=8)
            js = resp.json().get("js", {})
            if isinstance(js, dict) and "cmd" in js:
                return js["cmd"].replace("ffrt ", "").strip()
        except Exception:
            continue
    return None

# ===== Initialize Session =====
def init_portal_session(base_url, mac, device_info, portal_type, portal_name):
    session_data = load_session_json(portal_name)
    session = requests.Session()
    prefix = "stalker_portal" if portal_type == "1" else "c"

    def do_handshake():
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
        resp = session.post(handshake_url, headers=headers)
        try:
            token = resp.json()["js"]["token"]
            save_session_json(base_url, mac, token, portal_type, portal_name)
            headers["Authorization"] = f"Bearer {token}"
            return token, headers
        except Exception:
            print("❌ Handshake failed:", resp.text)
            return None, None

    if session_data and session_data.get("mac") == mac and session_data.get("portal_type") == portal_type:
        print(f"[✓] Using saved {get_session_filename(portal_name)}")
        token = session_data.get("token")
        headers = {h.split(": ")[0]: h.split(": ", 1)[1] for h in session_data["headers"]}
        for c in session_data.get("cookie", "").split("; "):
            if "=" in c:
                k, v = c.split("=", 1)
                session.cookies.set(k.strip(), v.strip())
    else:
        token, headers = do_handshake()

    if not token:
        token, headers = do_handshake()

    headers["Authorization"] = f"Bearer {token}"
    session.cookies.set("mac", mac)
    session.cookies.set("stb_lang", "en")
    session.cookies.set("timezone", "GMT")

    # ===== Validate get_profile =====
    sn, device_id, device_id2, signature = device_info["SNCUT"], device_info["Device_ID1"], device_info["Device_ID1"], device_info["Signature"]
    profile_url = f"{base_url}/{prefix}/server/load.php?type=stb&action=get_profile&sn={sn}&device_id={device_id}&device_id2={device_id2}&signature={signature}&JsHttpRequest=1-xml"
    print("[*] Validating via get_profile...")
    resp = session.get(profile_url, headers=headers)
    if "js" not in resp.text:
        print("[!] Session expired — renewing session...")
        os.remove(get_session_filename(portal_name))
        return init_portal_session(base_url, mac, device_info, portal_type, portal_name)
    print("👍 Profile validated successfully.\n")
    return session, headers

# ===== Fetch Channels =====
def fetch_channels(base_url, session, headers, portal_type):
    prefix = "stalker_portal" if portal_type == "1" else "c"
    channel_url = f"{base_url}/{prefix}/server/load.php?type=itv&action=get_all_channels&JsHttpRequest=1-xml"
    print("[*] Fetching channel list...")
    resp = session.get(channel_url, headers=headers)
    try:
        js = resp.json()
        return js.get("js", {}).get("data", [])
    except Exception:
        print("[!] Portal did not return valid JSON. Response snippet:")
        print(resp.text[:400])
        return []

# ===== Main =====
base_url_input = "https://kip.kkjkkj20089.workers.dev/"
mac = "00:1A:79:00:13:DA"
print("\nSelect Portal Type:")
print("1. stalker_portal/c/")
print("2. /c/")
portal_type = "1"

base_url, save_m3u_name = normalize_url_and_name(base_url_input)

mode = "2"
device_info = generate_device_info(mac)
session, headers = init_portal_session(base_url, mac, device_info, portal_type, save_m3u_name)
if not session:
    exit(1)

# ===== Fetch Genres =====
prefix = "stalker_portal" if portal_type == "1" else "c"
genre_url = f"{base_url}/{prefix}/server/load.php?type=itv&action=get_genres&JsHttpRequest=1-xml"
# New robust code
try:
    resp = session.get(genre_url, headers=headers)
    resp.raise_for_status() # Checks for HTTP errors
    genres = resp.json().get("js", [])
except Exception as e:
    print(f"❌ Error fetching genres: {e}")
    # Print the first 200 chars of the response to see if it's HTML
    if 'resp' in locals():
        print(f"Server Response: {resp.text[:200]}") 
    genres = []
print("\nAvailable Genres:")
for i, g in enumerate(genres, 1):
    print(f"{i}. {g['title']}")
selected = "3,4,5,6,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,59"
selected_ids = [str(genres[int(x.strip()) - 1]['id']) for x in selected.split(",")]
genre_titles = {str(genres[int(x.strip()) - 1]['id']): genres[int(x.strip()) - 1]['title'] for x in selected.split(",")}

# ===== Fetch Channels =====
all_channels = fetch_channels(base_url, session, headers, portal_type)

# If authorization failed → renew session and retry once
if not all_channels:
    print("[!] Channel fetch failed — session expired. Renewing session...")
    os.remove(get_session_filename(save_m3u_name))

    new_session, new_headers = init_portal_session(base_url, mac, device_info, portal_type, save_m3u_name)

    globals()["session"] = new_session
    globals()["headers"] = new_headers

    all_channels = fetch_channels(base_url, new_session, new_headers, portal_type)

filtered = [ch for ch in all_channels if str(ch.get("tv_genre_id")) in selected_ids]

# ===== OFFLINE MODE =====
if mode == "1":
    filename = f"Offline_{save_m3u_name}.m3u"
    print(f"\n[*] Generating offline playlist please wait: {filename}")
    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        def fetch_real_link(ch):
            name, cmd = ch.get("name", "Unknown"), ch.get("cmd", "").strip()
            if not cmd:
                return name, None
            if "ffmpeg" in cmd:
                return name, cmd.replace("ffmpeg ", "").strip()
            return name, create_link(base_url, cmd, session, headers, portal_type)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(fetch_real_link, ch) for ch in filtered]
            for future in as_completed(futures):
                name, real_url = future.result()
                ch = next((c for c in filtered if c.get("name") == name), None)
                logo = ch.get("logo", "")
                if portal_type == "1" and logo:
                    logo_url = f"{base_url}/stalker_portal/misc/logos/320/{logo}"
                else:
                    logo_url = logo or ""
                group_title = genre_titles.get(str(ch.get("tv_genre_id")), "Other")
                if real_url:
                    f.write(f'#EXTINF:-1 group-title="{group_title}" tvg-logo="{logo_url}",{name}\n{real_url}\n')
                    print(f"✅ {name}")
                else:
                    print(f"❌ {name}")

    print(f"\n✅ Offline playlist saved as {filename}")
    print("ℹ️ You can download this file from the 'Files' tab on the left.")
    exit(0)

# ===== ONLINE MODE (Flask Middleware) =====
app = Flask(__name__)
filename = f"Online_{save_m3u_name}.m3u"

# --- MODIFICATION: Detect Replit URL ---
base_server_url = "https://teste-rdny.onrender.com"

port = int(os.environ.get("PORT", 8080))

@app.route("/getlink/<int:ch_id>")
def getlink(ch_id):
    ch = next((c for c in filtered if str(c.get("id")) == str(ch_id)), None)
    if not ch:
        return jsonify({"error": "Channel not found"}), 404
    cmd = ch.get("cmd", "").strip()
    if "ffmpeg" in cmd:
        real_url = cmd.replace("ffmpeg ", "").strip()
    else:
        real_url = create_link(base_url, cmd, session, headers, portal_type)
    if not real_url:
        # SESSION probably expired → renew session
        print("[!] Session expired while creating link. Renewing...")

        os.remove(get_session_filename(save_m3u_name))  # delete old session.json
        new_session, new_headers = init_portal_session(base_url, mac, device_info, portal_type, save_m3u_name)

        globals()["session"] = new_session
        globals()["headers"] = new_headers

        # Retry once
        real_url = create_link(base_url, cmd, new_session, new_headers, portal_type)

        if not real_url:
            return jsonify({"error": "Failed even after session refresh"}), 500

    print(f"[*] Redirecting to: {real_url[:50]}...")
    response = make_response(redirect(real_url, code=302))
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "*"
    return response


# --- MODIFICATION: Add route to serve the M3U file ---
@app.route(f"/{filename}")
def serve_playlist():
    try:
        return send_from_directory(
            os.getcwd(), 
            filename, 
            as_attachment=True, 
            mimetype="audio/mpegurl"
        )
    except FileNotFoundError:
        return jsonify({"error": "Playlist file not found. Has it been generated?"}), 404
# --- END MODIFICATION ---

@app.route("/")
def index():
    # --- MODIFICATION: Link to the correct playlist URL ---
    return f"📡 IPTV Playlist Online — Playlist URL: <a href='{base_server_url}/{filename}'>{base_server_url}/{filename}</a>"
    # --- END MODIFICATION ---

print(f"\n[*] Generating online playlist: {filename}")
with open(filename, "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    for ch in filtered:
        name, ch_id = ch.get("name", "Unknown"), ch.get("id")
        logo = ch.get("logo", "")
        if portal_type == "1" and logo:
            logo_url = f"{base_url}/stalker_portal/misc/logos/320/{logo}"
        else:
            logo_url = logo or ""
        group_title = genre_titles.get(str(ch.get("tv_genre_id")), "Other")
        # --- MODIFICATION: Use the 'base_server_url' variable ---
        f.write(f'#EXTINF:-1 group-title="{group_title}" tvg-logo="{logo_url}",{name}\n{base_server_url}/getlink/{ch_id}\n')
        # --- END MODIFICATION ---

print(f"\n✅ Online playlist saved at: {os.path.abspath(filename)}")
# --- MODIFICATION: Print the correct public URL ---
print(f"🌐 Playlist URL: {base_server_url}/{filename}")
# --- END MODIFICATION ---
print("📱 open this playlist url in TiviMate or OTT Navigator Or Any Player.\n")

app.run(host="0.0.0.0", port=port, debug=False)
