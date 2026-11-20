#!/usr/bin/env python3
"""ガードレール検査(docs/LEGAL_GUARDRAILS.md の G1〜G4, G6 を機械的に確認する)。

使い方:
    python tools/check_guardrails.py            # リポジトリルートで実行
    python tools/check_guardrails.py --root .   # ルート指定

検出したら終了コード 1。何も無ければ 0。
- Live2D 系のモデル/プロジェクトファイルがリポジトリに含まれていないか
- Cubism SDK / Core、moc3 解析系ツールへの参照がコード・設定に無いか
- Live2D のロゴ素材らしきファイルが無いか

注意: *.md は検査しない(説明文として語を含むため)。docs/ も対象外。
VTS の API メッセージ名 "Live2DParameterListRequest" 等は正当な使用なので検出しない。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__",
    "Library", "Temp", "Logs", "obj", ".pytest_cache", ".ruff_cache", "docs",
}

# G2/G4: リポジトリに存在してはいけないファイル(拡張子/末尾)
FORBIDDEN_FILE_SUFFIXES = (
    ".moc3", ".moc", ".cmo3", ".cmo", ".can3", ".can",
    ".model3.json", ".motion3.json", ".physics3.json", ".exp3.json",
    ".cdi3.json", ".pose3.json", ".userdata3.json", ".vtube.json",
)

# G1/G6: ファイル名に含まれてはいけない語(小文字比較)
FORBIDDEN_FILENAME_PATTERNS = [
    re.compile(r"cubism", re.I),
    re.compile(r"live2d.*logo|logo.*live2d", re.I),
]

# G1/G3: コード・設定の中身に含まれてはいけない語
FORBIDDEN_CONTENT_PATTERNS = [
    (re.compile(r"CubismCore", re.I), "Cubism Core への参照 (G1)"),
    (re.compile(r"live2dcubismcore", re.I), "Cubism Core ライブラリへの参照 (G1)"),
    (re.compile(r"Live2D\.Cubism|com\.live2d", re.I), "Cubism SDK for Unity への参照 (G1)"),
    (re.compile(r"pixi-live2d|live2d-py|live2d-sdk|live2dsdk", re.I), "Live2D ランタイム系パッケージ (G1)"),
    (re.compile(r"\.moc3\b|\bmoc3\b", re.I), "moc3 への参照 (G2)"),
    (re.compile(r"ayagami|D2Evil|FreeLive", re.I), "moc3 解析系ツールへの参照 (G3)"),
]

TEXT_SUFFIXES = {
    ".py", ".cs", ".json", ".toml", ".yml", ".yaml", ".txt", ".cfg", ".ini",
    ".asmdef", ".asmref", ".shader", ".cginc", ".hlsl", ".js", ".ts",
    ".bat", ".ps1", ".sh", ".spec", ".xml", ".csproj", ".lock",
}


def iter_files(root: Path, self_path: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        if path.resolve() == self_path:
            continue
        yield path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".", help="検査するリポジトリルート(既定: カレント)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    self_path = Path(__file__).resolve()
    findings: list[str] = []

    for path in iter_files(root, self_path):
        rel = path.relative_to(root).as_posix()
        name = path.name.lower()

        if name.endswith(FORBIDDEN_FILE_SUFFIXES):
            findings.append(f"{rel}: Live2D のモデル/プロジェクトファイルは含められません (G2/G4)")

        for pattern in FORBIDDEN_FILENAME_PATTERNS:
            if pattern.search(path.name):
                findings.append(f"{rel}: ファイル名が禁止パターンに一致 ({pattern.pattern}) (G1/G6)")

        if path.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:  # 読めないファイルは警告のみ
                print(f"warn: {rel} を読めません: {exc}", file=sys.stderr)
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                for pattern, reason in FORBIDDEN_CONTENT_PATTERNS:
                    if pattern.search(line):
                        findings.append(f"{rel}:{lineno}: {reason}")

    if findings:
        print("ガードレール違反の可能性があります:")
        for item in findings:
            print(f"  - {item}")
        print(f"\n{len(findings)} 件。docs/LEGAL_GUARDRAILS.md を確認し、該当変更を取り下げてください。")
        return 1

    print("ガードレール検査: 問題は見つかりませんでした。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
