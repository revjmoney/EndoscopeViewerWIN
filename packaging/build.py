"""Build an explicit, private-data-free Windows bundle and installer."""
import argparse
import hashlib
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_paths import VERSION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--iscc', required=True, type=Path)
    args = parser.parse_args()
    os.chdir(ROOT)
    build = ROOT / 'build'
    release = ROOT / 'dist' / 'release'
    licenses = build / 'licenses'
    temporary = ROOT / '.cache' / 'build-temp'
    for directory in (release, licenses, temporary):
        directory.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(temporary)
    os.environ['PYINSTALLER_CONFIG_DIR'] = str(ROOT / '.cache' / 'pyinstaller')
    shutil.copyfile(ROOT / 'LICENSE', licenses / 'EndoscopeViewerWIN-MIT.txt')
    shutil.copyfile(Path(sys.base_prefix) / 'LICENSE.txt', licenses / 'Python.txt')
    pillow = importlib.metadata.distribution('Pillow')
    for file in pillow.files:
        if '/licenses/' in str(file).replace('\\', '/') and Path(file).name == 'LICENSE':
            shutil.copyfile(pillow.locate_file(file), licenses / 'Pillow-and-dependencies.txt')
    for name in ('tcl8.6', 'tk8.6'):
        candidate = Path(sys.base_prefix) / 'tcl' / name / 'license.terms'
        if candidate.exists():
            shutil.copyfile(candidate, licenses / (name + '.txt'))
    shutil.copyfile(ROOT / 'packaging' / 'THIRD-PARTY-NOTICES.txt', licenses / 'THIRD-PARTY-NOTICES.txt')
    shutil.copyfile(ROOT / 'packaging' / 'Tcl-license.txt', licenses / 'Tcl-license.txt')
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--onedir', '--windowed', '--noupx', '--name', 'EndoscopeViewerWIN',
        '--distpath', str(ROOT / 'dist' / 'app'), '--workpath', str(build / 'pyinstaller'),
        '--specpath', str(build), '--version-file', str(ROOT / 'packaging' / 'version-info.txt'),
        '--add-data', str(licenses) + ';licenses',
        str(ROOT / 'viewer.py'),
    ], check=True)
    bundle = ROOT / 'dist' / 'app' / 'EndoscopeViewerWIN'
    # PyInstaller's code dependency graph is the input; never add the source tree.
    forbidden = {'.apk', '.xapk', '.mp4', '.png', '.jpg', '.jpeg'}
    for item in bundle.rglob('*'):
        if item.is_file() and (item.suffix.lower() in forbidden or item.name == 'preferences.json'):
            raise RuntimeError('Unexpected private/media asset in application bundle: ' + item.name)
    subprocess.run([str(args.iscc.resolve()), '/DAppVersion=' + VERSION,
                    str(ROOT / 'packaging' / 'installer.iss')], check=True)
    archive = release / f'EndoscopeViewerWIN-{VERSION}-windows-x64-portable.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for item in bundle.rglob('*'):
            if item.is_file():
                output.write(item, Path('EndoscopeViewerWIN') / item.relative_to(bundle))
    artifacts = [release / f'EndoscopeViewerWIN-{VERSION}-windows-x64-setup.exe', archive]
    checksums = ''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in artifacts)
    (release / 'SHA256SUMS.txt').write_text(checksums, encoding='utf-8')
    print(checksums)


if __name__ == '__main__':
    main()
