# -*- coding: utf-8 -*-
# pdf-compress —— 智能 PDF 压缩工具
# Copyright (C) 2026 Alex Lu
#
# 本程序是自由软件：你可以依照 GNU Affero 通用公共许可证（AGPL-3.0）的条款
# 重新发布和/或修改它，无论是第 3 版还是（由你选择的）任何更新版本。
#
# 本程序基于 PyMuPDF（© Artifex Software, Inc.，AGPL-3.0 或商业双许可）构建。
# 分发本程序时必须一并提供源代码及许可证声明，详见随附的 LICENSE 与 NOTICE。
#
# 本程序分发的目的是希望它有用，但没有任何担保，甚至没有适销性或
# 特定用途适用性的默示担保。详见 GNU AGPL-3.0。

"""pdf-compress —— 智能 PDF 压缩工具。

核心思路：先找"假肉"再压"真肉"。
  1. 剥离 Illustrator 私有数据（常常占 30%~50% 体积，查看/打印完全用不到）
  2. 只对超出目标 DPI 的图片重采样，矢量图形和文字绝不碰
  3. 字体子集化 + 对象去重 + 流压缩

用法:
    python pdf_compress.py input.pdf                    # 默认 high 档
    python pdf_compress.py input.pdf -l strong          # 更强压缩
    python pdf_compress.py input.pdf -s                 # 同时剥离 Illustrator 私有数据
    python pdf_compress.py input.pdf -t 20              # 自动选档，压到 20MB 以内
    python pdf_compress.py *.pdf -o out_dir/            # 批量

档位（画质从高到低 / 体积从大到小）:
    ultra     几乎无损重压，只动严重超采样的图
    high      推荐档，屏幕观看与打印都够用
    balanced  更小体积，画质仍良好
    strong    激进压缩，适合仅屏幕快速预览
    lossless  不重压图片，只做对象去重 + 流压缩 + 字体子集化

关于 -s / --strip-ai:
    Illustrator 导出的 PDF 会内嵌一份「私有数据」（/PieceInfo → /AIPDFPrivateData），
    用于在 AI 里重新打开时还原图层。它常常占 30%~50% 的体积，但查看/打印完全用不到。
    剥离后文件显著变小，代价是失去在 Illustrator 中的可再编辑性。

关于 -t / --target:
    给定目标大小（MB），脚本会从画质最高的档位往下试，选第一个达标的档位。
    只剥离 AI 数据即可达标时，不会重压任何图片。
"""

import argparse
import io
import os
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    print("缺少依赖，请先安装: pip install pymupdf", file=sys.stderr)
    raise SystemExit(1)

# 中文输出编码修复（Windows 控制台）
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 档位: (dpi_threshold, dpi_target, jpeg_quality)。None 表示不重压图片。
# dpi_threshold 之上的图会被重采样到 dpi_target；低于阈值的图保持原分辨率不动。
LEVELS = {
    "ultra": (201, 200, 72),
    "high": (121, 120, 75),
    "balanced": (101, 100, 80),
    "strong": (101, 100, 65),
    "lossless": None,
}
LEVEL_ORDER = ["ultra", "high", "balanced", "strong", "lossless"]  # 画质从高到低

__version__ = "1.0.0"


def human(size: float) -> str:
    """把字节数格式化成易读字符串。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def strip_illustrator(doc: "fitz.Document") -> int:
    """移除页面上的 /PieceInfo（Illustrator 私有数据入口）。返回处理页数。"""
    count = 0
    for xref in range(1, doc.xref_length()):
        obj = doc.xref_object(xref, compressed=True)
        if "/Type/Page" in obj and "PieceInfo" in obj:
            doc.xref_set_key(xref, "PieceInfo", "null")
            count += 1
    return count


def apply_level(doc: "fitz.Document", level: str) -> None:
    """按档位对文档重压图片。"""
    params = LEVELS[level]
    if params is None:
        return
    dpi_threshold, dpi_target, quality = params
    try:
        doc.rewrite_images(
            dpi_threshold=dpi_threshold,
            dpi_target=dpi_target,
            quality=quality,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"  ! 图片重采样跳过（{exc}）", file=sys.stderr)


def finalize(doc: "fitz.Document", dst: Path) -> None:
    """字体子集化 + 写盘（对象去重、流压缩）。"""
    try:
        doc.subset_fonts()
    except (RuntimeError, ValueError) as exc:
        print(f"  ! 字体子集化跳过（{exc}）", file=sys.stderr)

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    doc.save(
        str(tmp),
        garbage=4,
        deflate=True,
        deflate_images=True,
        deflate_fonts=True,
        clean=True,
        use_objstms=1,
    )
    tmp.replace(dst)


def compress(src: Path, dst: Path, level: str, strip_ai: bool,
             target_mb: float | None = None) -> tuple[int, int, str, int]:
    """压缩单个 PDF。

    返回 (原始字节数, 压缩后字节数, 实际使用的档位, 剥离的页数)。
    """
    before = src.stat().st_size
    target_bytes = int(target_mb * 1024 * 1024) if target_mb else None

    # 需要试多个档位时，先看「只剥离 AI 数据」是否已达标 —— 能不动图片就不动
    if target_bytes and strip_ai:
        doc = fitz.open(str(src))
        try:
            if doc.needs_pass:
                raise ValueError("文件已加密，需要密码，无法处理")
            pages = strip_illustrator(doc)
            probe = dst.with_suffix(dst.suffix + ".probe")
            finalize(doc, probe)
        finally:
            doc.close()
        if probe.stat().st_size <= target_bytes:
            probe.replace(dst)
            return before, dst.stat().st_size, "lossless", pages
        probe.unlink(missing_ok=True)

    candidates = LEVEL_ORDER if target_bytes else [level]
    last_error: Exception | None = None

    for candidate in candidates:
        doc = fitz.open(str(src))
        try:
            if doc.needs_pass:
                raise ValueError("文件已加密，需要密码，无法处理")
            pages = strip_illustrator(doc) if strip_ai else 0
            apply_level(doc, candidate)
            finalize(doc, dst)
        finally:
            doc.close()

        size = dst.stat().st_size
        if target_bytes is None or size <= target_bytes:
            return before, size, candidate, pages

    # 所有档位都超标：保留最小的那个（strong 档的结果已在 dst）
    return before, dst.stat().st_size, LEVEL_ORDER[-2], 0


def resolve_output(src: Path, out: str | None) -> Path:
    """决定输出路径：未指定则加 _compressed 后缀；指定目录则放进目录。"""
    if not out:
        return src.with_name(f"{src.stem}_compressed{src.suffix}")
    target = Path(out)
    if target.is_dir() or out.endswith(("/", "\\")):
        return target / f"{src.stem}_compressed{src.suffix}"
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PDF 压缩工具（PyMuPDF）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="档位: " + " / ".join(LEVEL_ORDER) + "（画质从高到低）",
    )
    parser.add_argument("--version", action="version", version=f"pdf-compress {__version__}")
    parser.add_argument("inputs", nargs="+", help="输入 PDF 文件，支持多个")
    parser.add_argument("-o", "--output", help="输出文件或输出目录（默认加 _compressed 后缀）")
    parser.add_argument("-l", "--level", choices=LEVEL_ORDER, default="high",
                        help="压缩档位（默认 high）")
    parser.add_argument("-s", "--strip-ai", action="store_true",
                        help="剥离 Illustrator 私有数据（大幅瘦身，但失去 AI 可再编辑性）")
    parser.add_argument("-t", "--target", type=float, metavar="MB",
                        help="目标大小（MB），自动选达标档位")
    args = parser.parse_args()

    files = [Path(p) for p in args.inputs]
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        print(f"文件不存在: {', '.join(missing)}", file=sys.stderr)
        return 1
    if len(files) > 1 and args.output and not Path(args.output).is_dir():
        print("多个输入文件时 -o 必须是目录", file=sys.stderr)
        return 1

    total_before = total_after = 0
    for src in files:
        dst = resolve_output(src, args.output)
        if dst.resolve() == src.resolve():
            print(f"跳过 {src.name}：输出路径与输入相同", file=sys.stderr)
            continue

        print(f"{src.name}")
        try:
            before, after, used, pages = compress(
                src, dst, args.level, args.strip_ai, args.target
            )
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"  x 失败: {exc}", file=sys.stderr)
            continue

        total_before += before
        total_after += after
        ratio = (1 - after / before) * 100 if before else 0.0
        flag = "!" if after >= before else " "
        detail = f"档位 {used}"
        if pages:
            detail += f" | 剥离 AI 数据 {pages} 页"
        if args.target and after > args.target * 1024 * 1024:
            detail += " | 未达目标，已用最低档"
        print(f"  {human(before)} -> {human(after)}  省 {ratio:.1f}% {flag}  [{detail}]")
        print(f"  输出: {dst}")

    if len(files) > 1 and total_before:
        ratio = (1 - total_after / total_before) * 100
        print(f"\n合计 {human(total_before)} -> {human(total_after)}  省 {ratio:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
