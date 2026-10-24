import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import io
import struct
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from PIL import Image
import camera_controls as controls
import camera_wifi as wifi
from viewer import UdpFrames, Viewer


def control(name):
    return next(c for c in controls.CONTROLS if c.name == name)


class RecordingViewTests(unittest.TestCase):
    def test_recorded_view_uses_center_zoom_and_orientation(self):
        image = Image.new('RGB', (8, 4))
        for x in range(8):
            for y in range(4):
                image.putpixel((x, y), (x * 30, y * 60, 0))
        view = object.__new__(Viewer)
        recorded = view.display_image(image, (270, False, 2))
        self.assertEqual(recorded.size, (2, 4))
        self.assertEqual(recorded.getpixel((0, 0)), image.getpixel((2, 2)))
        self.assertEqual(recorded.getpixel((1, 3)), image.getpixel((5, 1)))

    def test_recording_settings_are_kept_when_preview_changes(self):
        view = object.__new__(Viewer)
        view.rotation, view.mirror, view.zoom = 0, False, 1
        image = Image.new('RGB', (8, 4))
        self.assertEqual(view.display_image(image, (270, True, 2)).size, (2, 4))
        self.assertEqual(view.display_image(image).size, (8, 4))


class ProtocolTests(unittest.TestCase):
    def test_modern_encoding(self):
        self.assertEqual(controls.encode(control('Set video format'), 7, (1,)), bytes.fromhex('999901100700000001000000000000000000000000000000'))

    def test_resolution_encoding(self):
        packet = controls.encode(control('Set resolution'), 7, (640, 480, 30))
        self.assertEqual(packet, struct.pack('<6sIHHHHB', b'SETCMD', 7, 8, 5, 640, 480, 30))

    def test_text_encoding(self):
        packet = controls.encode(control('Wi-Fi name'), 7, ('Jetion_Test',))
        self.assertEqual(packet[14:], b'Jetion_Test')
        self.assertEqual(struct.unpack_from('<H', packet, 12)[0], 11)

    def test_invalid_password(self):
        with self.assertRaises(ValueError):
            controls.encode(control('Wi-Fi password'), 1, ('short',))

    def test_version_reply(self):
        packet = bytes.fromhex('999902100100000064000000000000000000000000000000')
        self.assertEqual(controls.decode(control('Firmware version'), 1, packet)['version'], 100)
        with self.assertRaises(ValueError):
            controls.decode(control('Firmware version'), 2, packet)

    def test_real_resolution_reply(self):
        packet = bytes.fromhex('524554434d44200000000900000600018002e001')
        result = controls.decode(control('Supported resolutions'), 32, packet)
        self.assertEqual(result['resolutions'], [{'width': 640, 'height': 480, 'fps': None}])

    def test_reassembly(self):
        buffer = io.BytesIO()
        Image.new('RGB', (64, 48), '#13ab77').save(buffer, format='JPEG')
        jpeg = buffer.getvalue()
        # Independently defined wire fixture: 51-byte header, 1-based fragments,
        # plain-payload flag. Reorder, duplicate and omit datagrams deliberately.
        packets = []
        chunks = [jpeg[i:i+200] for i in range(0, len(jpeg), 200)]
        for index, chunk in enumerate(chunks, 1):
            header = bytearray(51)
            struct.pack_into('<HH', header, 0, 0x9999, 3)
            struct.pack_into('<I', header, 16, 1)
            header[24] = 1
            struct.pack_into('<IIHHHIHHI', header, 25, 5, len(jpeg), index, len(chunks), len(chunk), 1, 64, 48, 1)
            packets.append(bytes(header) + chunk)
        parser = UdpFrames()
        frames = [frame for packet in reversed(packets) for frame in parser.feed(packet)]
        self.assertEqual(frames, [jpeg])
        with Image.open(io.BytesIO(frames[0])) as image:
            image.load()
            self.assertEqual(image.size, (64, 48))
        parser = UdpFrames()
        self.assertFalse([f for packet in packets[:-1] for f in parser.feed(packet)])
        parser = UdpFrames()
        self.assertEqual([f for packet in packets for duplicate in (packet, packet) for f in parser.feed(duplicate)], [jpeg])
        self.assertFalse(parser.feed(b'not a camera packet'))


class WifiTests(unittest.TestCase):
    def test_camera_candidates(self):
        self.assertTrue(wifi.camera_ssid('Jetion_870ed404'))
        self.assertFalse(wifi.camera_ssid('Home WiFi'))

    def test_interfaces(self):
        text = '    Name : Wi-Fi\n State : connected\n SSID : Jetion_870ed404\n BSSID : 54:77:87:0e:d4:04\n'
        self.assertEqual(wifi.parse_interfaces(text)[0]['ssid'], 'Jetion_870ed404')

    def test_networks(self):
        text = 'Interface name : Wi-Fi\nSSID 1 : Jetion_870ed404\n Authentication : WPA2-Personal\n BSSID 1 : 54:77:87:0e:d4:04\n Signal : 100%\nSSID 2 : Home WiFi\n Signal : 85%\n'
        found = wifi.parse_networks(text)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['interface'], 'Wi-Fi')
        self.assertEqual(found[0]['signal'], 100)

    def test_profile_xml(self):
        _, xml = wifi.profile_xml('Jetion_A&B', '1234&678')
        root = ET.fromstring(xml)
        ns = {'w': 'http://www.microsoft.com/networking/WLAN/profile/v1'}
        self.assertEqual(root.find('.//w:keyMaterial', ns).text, '1234&678')
        self.assertEqual(root.find('w:connectionMode', ns).text, 'manual')
        with self.assertRaises(ValueError):
            wifi.profile_xml('Home WiFi', '12345678')

    def test_join_profile_and_cleanup(self):
        calls = []
        connected = 'Name : Wi-Fi\nState : connected\nSSID : Jetion_Test\n'
        def netsh(*args):
            calls.append(args)
            if args == ('show', 'interfaces'):
                return connected if len(calls) > 3 else 'Name : Wi-Fi\nState : disconnected\n'
            return 'successfully'
        with patch.object(wifi, 'run_netsh', netsh):
            self.assertIn('Connected', wifi.join({'ssid': 'Jetion_Test', 'interface': 'Wi-Fi'}, '12345678'))
        added = next(c for c in calls if c[:2] == ('add', 'profile'))
        filename = next(a.split('=', 1)[1] for a in added if a.startswith('filename='))
        self.assertFalse(Path(filename).exists())
        self.assertNotIn('12345678', repr(calls))


if __name__ == '__main__':
    unittest.main()
