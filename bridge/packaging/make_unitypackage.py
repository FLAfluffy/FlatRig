"""unity-package/ の Editor 拡張を、Unity にドラッグ&ドロップで入れられる .unitypackage にする。

.unitypackage は、アセットごとに <GUID>/asset(中身)・asset.meta・pathname(取り込み先のパス)を入れた tar.gz。
GUID は各ファイルの .meta のものをそのまま使う(生成済みのアバターからの参照が変わらないように)。
取り込み先は Assets/FlatRig/Tools/ 。生成物の置き場所(Assets/FlatRig/Generated)とは分けておき、更新で上書きしない。
使い方(単体で試すとき): python packaging/make_unitypackage.py <unity-package フォルダ> <出力.unitypackage>
"""

from __future__ import annotations

import gzip
import io
import re
import sys
import tarfile
import uuid
from pathlib import Path

DEST = "Assets/FlatRig/Tools"
SKIP_DIRS = {"obj", "bin", "__pycache__"}
SKIP_FILES = {"package.json", ".DS_Store"}  # package.json は Package Manager 用。Assets に入れる版では使わない
GUID = re.compile(r"^guid:\s*([0-9a-f]{32})\s*$", re.M)
# 取り込み先のフォルダ Tools(unity-package には .meta が無い)。毎回同じ GUID になるよう名前から作る。
# 親の Assets/FlatRig は入れない(生成物で既にあることが多く、別の GUID で入れると食い違う。無ければ Unity が作る)
NS = uuid.UUID("6f1b0e1c-9a52-4c7e-8f0a-3c2d7b1e5a40")
FOLDER_META = (
    "fileFormatVersion: 2\nguid: {guid}\nfolderAsset: yes\nDefaultImporter:\n"
    "  externalObjects: {{}}\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n"
)


def _folder_guid(path: str) -> str:
    return uuid.uuid5(NS, path).hex


def collect(src: Path) -> list[tuple[str, str, bytes | None, bytes]]:
    """(GUID, 取り込み先のパス, 中身(フォルダは None), .meta の中身) の一覧。.meta が無いものはエラー。"""
    items: list[tuple[str, str, bytes | None, bytes]] = []
    guid = _folder_guid(DEST)
    items.append((guid, DEST, None, FOLDER_META.format(guid=guid).encode("utf-8")))
    missing = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if p.suffix == ".meta" or p.name in SKIP_FILES or any(d in SKIP_DIRS for d in rel.parts):
            continue
        meta = p.with_name(p.name + ".meta")
        if not meta.is_file():
            missing.append(str(rel))
            continue
        meta_bytes = meta.read_bytes()
        m = GUID.search(meta_bytes.decode("utf-8", "replace"))
        if m is None:
            missing.append(f"{rel}.meta(guid が読めません)")
            continue
        content = None if p.is_dir() else p.read_bytes()
        items.append((m.group(1), f"{DEST}/{rel.as_posix()}", content, meta_bytes))
    if missing:
        raise ValueError(".meta が無いファイルがあります(Unity で一度開いて作ってください): " + ", ".join(missing))
    guids = [g for g, *_ in items]
    if len(set(guids)) != len(guids):
        raise ValueError("GUID が重複しています")
    return items


def _add(tar: tarfile.TarFile, name: str, data: bytes | None) -> None:
    info = tarfile.TarInfo(name)
    info.mtime = 0
    if data is None:
        info.type, info.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(info)
    else:
        info.size, info.mode = len(data), 0o644
        tar.addfile(info, io.BytesIO(data))


def write(src: Path, out: Path) -> int:
    """out に .unitypackage を書き、入れたアセットの数を返す。"""
    items = collect(src)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for guid, path, content, meta in items:
            _add(tar, guid, None)
            if content is not None:
                _add(tar, f"{guid}/asset", content)
            _add(tar, f"{guid}/asset.meta", meta)
            _add(tar, f"{guid}/pathname", path.encode("utf-8"))
    with out.open("wb") as f, gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0) as gz:
        gz.write(raw.getvalue())
    return len(items)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    print(f"{write(Path(sys.argv[1]), Path(sys.argv[2]))} 個のアセットを書き出しました")
