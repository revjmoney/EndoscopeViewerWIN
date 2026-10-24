"""Windows Wi-Fi discovery restricted to known camera-family SSIDs."""
from pathlib import Path
import re
import subprocess
import tempfile
import time
from xml.sax.saxutils import escape

from camera_controls import CameraControls, CONTROLS


PREFIXES = ("jetion", "eloam", "limink", "nidage", "molink", "hdwifi", "hd_wifi", "hd wifi")


def run_netsh(*arguments):
    result = subprocess.run(["netsh", "wlan", *arguments], capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = result.stdout.decode("utf-8", errors="replace")
    # netsh often returns success even when a Wi-Fi operation fails.
    return output + result.stderr.decode("utf-8", errors="replace")


def camera_ssid(name):
    return name.lower().startswith(PREFIXES)


def parse_interfaces(text):
    result, current = [], {}
    for line in text.splitlines():
        match = re.match(r"\s*([^:]+?)\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        key, value = match.groups()
        if key == "Name":
            if current:
                result.append(current)
            current = {"interface": value}
        elif key in ("SSID", "State", "Profile", "Authentication"):
            current[key.lower()] = value
    if current:
        result.append(current)
    return result


def parse_networks(text):
    results, network = [], None
    interface = None
    for line in text.splitlines():
        match = re.match(r"\s*Interface name\s*:\s*(.+)", line)
        if match:
            interface = match[1].strip()
        match = re.match(r"\s*SSID\s+\d+\s*:\s*(.*)", line)
        if match:
            if network:
                results.append(network)
            network = {"ssid": match[1].strip(), "signal": 0, "interface": interface}
        if network:
            match = re.match(r"\s*Authentication\s*:\s*(.+)", line)
            if match:
                network["authentication"] = match[1].strip()
            match = re.match(r"\s*Signal\s*:\s*(\d+)%", line)
            if match:
                network["signal"] = max(network["signal"], int(match[1]))
    if network:
        results.append(network)
    return [n for n in results if camera_ssid(n["ssid"])]


def scan():
    interfaces = parse_interfaces(run_netsh("show", "interfaces"))
    output = run_netsh("show", "networks", "mode=bssid")
    networks = parse_networks(output)
    for item in interfaces:
        if item.get("state") == "connected" and camera_ssid(item.get("ssid", "")):
            if not any(n["ssid"] == item["ssid"] for n in networks):
                networks.append({**item, "signal": 100})
    if not interfaces and not networks:
        raise RuntimeError("Windows could not list Wi-Fi adapters. Connect the adapter and allow Windows Wi-Fi/location access, or use manual Connect.")
    return interfaces, sorted(networks, key=lambda n: -n["signal"])


def profile_xml(ssid, password, authentication="WPA2-Personal"):
    if not camera_ssid(ssid) or not 1 <= len(ssid.encode("utf-8")) <= 32:
        raise ValueError("Select a recognized camera hotspot.")
    profile = "HD WiFi Viewer - " + ssid
    if authentication == "Open":
        auth = "<authentication>open</authentication><encryption>none</encryption><useOneX>false</useOneX>"
        key = ""
    elif authentication == "WPA2-Personal":
        if not 8 <= len(password) <= 63 or not password.isascii():
            raise ValueError("Enter an 8–63 character ASCII Wi-Fi password.")
        auth = "<authentication>WPA2PSK</authentication><encryption>AES</encryption><useOneX>false</useOneX>"
        key = "<sharedKey><keyType>passPhrase</keyType><protected>false</protected><keyMaterial>" + escape(password) + "</keyMaterial></sharedKey>"
    else:
        raise ValueError("Automatic profile creation supports open and WPA2-Personal camera hotspots. Join this network in Windows first.")
    xml = '<?xml version="1.0"?><WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1"><name>' + escape(profile) + '</name><SSIDConfig><SSID><hex>' + ssid.encode('utf-8').hex() + '</hex><name>' + escape(ssid) + '</name></SSID></SSIDConfig><connectionType>ESS</connectionType><connectionMode>manual</connectionMode><MSM><security><authEncryption>' + auth + '</authEncryption>' + key + '</security></MSM></WLANProfile>'
    return profile, xml


def join(network, password, stop=None):
    interfaces = parse_interfaces(run_netsh("show", "interfaces"))
    if any(i.get("state") == "connected" and i.get("ssid") == network["ssid"] for i in interfaces):
        return "Already connected to " + network["ssid"]
    interface = network.get("interface")
    if not interface:
        raise RuntimeError("No Wi-Fi interface was found for the selected camera.")
    profile, xml = profile_xml(network["ssid"], password, network.get("authentication", "WPA2-Personal"))
    from app_paths import data_dir
    temporary = data_dir() / ".cache" / "wifi"
    temporary.mkdir(parents=True, exist_ok=True)
    filename = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".xml", dir=temporary, delete=False) as handle:
            handle.write(xml)
            filename = handle.name
        output = run_netsh("add", "profile", "filename=" + filename, "interface=" + interface, "user=current")
        if not any(marker in output.lower() for marker in ("successfully", "is added on interface")):
            raise RuntimeError("Windows did not accept the camera Wi-Fi profile: " + output.strip())
        output = run_netsh("connect", "name=" + profile, "ssid=" + network["ssid"], "interface=" + interface)
    finally:
        if filename:
            Path(filename).unlink(missing_ok=True)
    end = time.monotonic() + 15
    while time.monotonic() < end:
        if stop is not None and stop.is_set():
            raise RuntimeError("Camera search canceled.")
        interfaces = parse_interfaces(run_netsh("show", "interfaces"))
        if any(i.get("state") == "connected" and i.get("ssid") == network["ssid"] for i in interfaces):
            return "Connected to " + network["ssid"]
        time.sleep(.5)
    raise RuntimeError("Windows did not join the camera hotspot. Check the password or join it manually.")


def verify_camera(host="192.168.10.123"):
    # Association can finish before DHCP/routing and the camera's service are ready.
    client = CameraControls()
    error = None
    for _ in range(6):
        try:
            result = client.execute(host, CONTROLS[0], timeout=1.2)
            return {"host": host, "port": 8031, "mode": "UDP/JPEG", "version": result["version"]}
        except OSError as exc:
            error = exc
            time.sleep(.5)
    raise RuntimeError("Hotspot joined but the camera did not answer its version query: " + str(error))
