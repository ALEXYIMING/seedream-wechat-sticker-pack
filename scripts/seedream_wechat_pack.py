#!/usr/bin/env python3
"""
Seedream 微信表情包全套物料编排脚本。

职责：
1. 读取 16 条提示词
2. 调用 byted-ark-seedream-skill 生成无水印原图
3. 重命名并平铺到 ./原始目录/
4. 生成 240×240 主表情、5 张配套图、文案和 zip
"""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    print("ERROR: 需要 Pillow：python3 -m pip install pillow", file=sys.stderr)
    sys.exit(1)


SEEDREAM_SCRIPT = Path.home() / ".claude/skills/byted-ark-seedream-skill/scripts/generate.js"


@dataclass
class StickerPrompt:
    id: str
    title: str
    prompt: str
    text: str


@dataclass
class PackConfig:
    project_dir: Path
    prompt_file: Path
    theme: str
    album_name: str
    intro: str
    tip: str
    thanks: str
    size: str
    response_format: str
    optimize: bool
    skip_generate: bool
    transparent_mode: str


# -----------------------------
# Text / path helpers
# -----------------------------

def safe_filename(text: str, max_len: int = 32) -> str:
    text = re.sub(r"[\\/:*?\"<>|\s]+", "_", text.strip())
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:max_len] or "untitled"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


# -----------------------------
# Prompt loading
# -----------------------------

def normalize_prompt_item(item: dict[str, Any], index: int) -> StickerPrompt:
    sid = str(item.get("id") or item.get("编号") or f"{index + 1:02d}").zfill(2)
    title = str(item.get("title") or item.get("name") or item.get("标题") or item.get("画面文字") or f"表情{sid}").strip()
    prompt = str(item.get("prompt") or item.get("提示词") or item.get("画面提示词") or "").strip()
    if not prompt:
        raise ValueError(f"第 {index + 1} 条缺少 prompt/提示词")
    text = str(item.get("text") or item.get("文字") or item.get("祝福文字") or title).strip()
    return StickerPrompt(sid, title, prompt, text)


def parse_markdown_prompts(text: str) -> list[StickerPrompt]:
    blocks = re.split(r"\n(?=\s*\d{1,2}\s*[｜|.、-])", text.strip())
    prompts: list[StickerPrompt] = []
    for idx, block in enumerate(blocks):
        if not block.strip():
            continue
        title_match = re.search(r"^\s*(\d{1,2})\s*[｜|.、-]\s*([^\n]+)", block)
        sid = title_match.group(1).zfill(2) if title_match else f"{idx + 1:02d}"
        title = title_match.group(2).strip() if title_match else f"表情{sid}"
        prompt_match = re.search(r"(?:提示词|画面提示词)[:：]\s*([\s\S]+)$", block)
        prompt = prompt_match.group(1).strip() if prompt_match else block.strip()
        prompt = re.sub(r"^```(?:\w+)?|```$", "", prompt).strip()
        prompts.append(StickerPrompt(sid, title, prompt, title))
    return prompts


def load_prompts(path: Path) -> list[StickerPrompt]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("stickers") or data.get("prompts") or data.get("items")
        if not isinstance(data, list):
            raise ValueError("JSON 提示词文件应是数组，或包含 stickers/prompts/items 数组")
        return [normalize_prompt_item(item, i) for i, item in enumerate(data)]
    return parse_markdown_prompts(text)


# -----------------------------
# Seedream generation
# -----------------------------

def extract_json(stdout: str) -> dict[str, Any]:
    stdout = stdout.strip()
    if not stdout:
        raise ValueError("Seedream 未返回 JSON")
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"无法解析 Seedream 输出：{stdout[:300]}")
    return json.loads(stdout[start:end + 1])


def run_seedream(prompt: str, cfg: PackConfig) -> Path:
    cmd = [
        "node",
        str(SEEDREAM_SCRIPT),
        "--prompt", prompt,
        "--size", cfg.size,
        "--mode", "text-to-image",
        "--watermark", "false",
        "--optimize", "true" if cfg.optimize else "false",
        "--response_format", cfg.response_format,
    ]
    proc = subprocess.run(
        cmd,
        cwd=cfg.project_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
    )
    if proc.stderr:
        print(proc.stderr, end="")
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout or proc.stderr or "Seedream 调用失败")
    result = extract_json(proc.stdout)
    images = result.get("images") or []
    for img in images:
        local_path = img.get("local_path")
        if img.get("download_success") and local_path:
            return Path(local_path)
    raise RuntimeError("Seedream 没有下载成功的图片")


def generate_originals(prompts: list[StickerPrompt], cfg: PackConfig) -> list[Path]:
    raw_dir = ensure_dir(cfg.project_dir / "原始目录")
    outputs: list[Path] = []
    for i, item in enumerate(prompts, 1):
        dest = raw_dir / f"{item.id}_{safe_filename(item.title)}.png"
        if cfg.skip_generate:
            if not dest.exists():
                print(f"  ⚠️ 跳过生成，但未找到 {dest.name}")
            else:
                outputs.append(dest)
            continue

        print(f"\n=== 生成 {i}/{len(prompts)}：{item.id} {item.title} ===")
        src = run_seedream(item.prompt, cfg)
        if src.resolve() != dest.resolve():
            if dest.exists():
                dest.unlink()
            shutil.move(str(src), str(dest))
            meta = src.with_name(src.stem.rsplit("_", 1)[0] + "_metadata.json")
            if meta.exists():
                meta_dest = raw_dir / f"{item.id}_{safe_filename(item.title)}_metadata.json"
                if meta_dest.exists():
                    meta_dest.unlink()
                shutil.move(str(meta), str(meta_dest))
        outputs.append(dest)
        print(f"  ✓ 原图: {dest}")
    return outputs


# -----------------------------
# Image processing
# -----------------------------

def center_crop(img: Image.Image, ratio: float = 1.0) -> Image.Image:
    w, h = img.size
    cur = w / h
    if cur > ratio:
        nw = int(h * ratio)
        left = (w - nw) // 2
        return img.crop((left, 0, left + nw, h))
    if cur < ratio:
        nh = int(w / ratio)
        top = (h - nh) // 2
        return img.crop((0, top, w, top + nh))
    return img


def detect_bg(img: Image.Image) -> tuple[int, int, int]:
    rgb = img.convert("RGB")
    w, h = rgb.size
    pts = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    vals = [rgb.getpixel(p) for p in pts]
    return tuple(sorted(channel)[len(channel) // 2] for channel in zip(*vals))


def color_dist2(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def collect_edge_palette(rgb: Image.Image, step: int = 16) -> list[tuple[int, int, int]]:
    w, h = rgb.size
    pix = rgb.load()
    pts: list[tuple[int, int]] = []
    for x in range(0, w, step):
        pts.append((x, 0))
        pts.append((x, h - 1))
    for y in range(0, h, step):
        pts.append((0, y))
        pts.append((w - 1, y))
    # Seedream often adds shaded/vignetted borders. Include near-corner interior samples
    # so transparent-mode remove can clear those connected background blocks too.
    for margin in [0, 24, 48, 80, 128, 192]:
        if margin < w and margin < h:
            pts.extend([
                (margin, margin),
                (w - 1 - margin, margin),
                (margin, h - 1 - margin),
                (w - 1 - margin, h - 1 - margin),
            ])
    return [pix[x, y] for x, y in pts]


def remove_edge_connected_bg(img: Image.Image, threshold: int = 92, edge_step: int = 16) -> Image.Image:
    """Remove Seedream backgrounds connected to the image edge.

    A single median corner color misses high-saturation generated backgrounds with
    gradients or vignetted borders. This samples many edge colors, marks pixels
    close to that palette, and flood-fills only from the edge so foreground pixels
    with similar colors are preserved.
    """
    img = img.convert("RGBA")
    rgb = img.convert("RGB")
    pix = rgb.load()
    w, h = rgb.size
    palette = collect_edge_palette(rgb, edge_step)
    threshold2 = threshold * threshold

    candidate = bytearray(w * h)
    for y in range(h):
        row = y * w
        for x in range(w):
            color = pix[x, y]
            if any(color_dist2(color, bg) <= threshold2 for bg in palette):
                candidate[row + x] = 1

    remove = bytearray(w * h)
    queue: deque[tuple[int, int]] = deque()

    def enqueue(x: int, y: int) -> None:
        idx = y * w + x
        if candidate[idx] and not remove[idx]:
            remove[idx] = 1
            queue.append((x, y))

    for x in range(w):
        enqueue(x, 0)
        enqueue(x, h - 1)
    for y in range(h):
        enqueue(0, y)
        enqueue(w - 1, y)

    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h:
                idx = ny * w + nx
                if candidate[idx] and not remove[idx]:
                    remove[idx] = 1
                    queue.append((nx, ny))

    out = img.copy()
    out_pix = out.load()
    for y in range(h):
        row = y * w
        for x in range(w):
            r, g, b, a = out_pix[x, y]
            if remove[row + x]:
                # Keep RGB for previewers that ignore/flatten alpha incorrectly.
                out_pix[x, y] = (r, g, b, 0)
            else:
                out_pix[x, y] = (r, g, b, 255)
    return out


def remove_solid_bg(img: Image.Image, threshold: int = 28) -> Image.Image:
    # Keep the old simple remover available for narrow pure-background cases.
    img = img.convert("RGBA")
    bg = detect_bg(img)
    pix = img.load()
    w, h = img.size
    transparent_rgb = bg
    for y in range(h):
        for x in range(w):
            r, g, b, a = pix[x, y]
            dist = ((r - bg[0]) ** 2 + (g - bg[1]) ** 2 + (b - bg[2]) ** 2) ** 0.5
            if dist < threshold:
                # Keep transparent pixels carrying the original light background RGB.
                # Some previewers render RGB even when alpha is 0; black RGB here shows as black blocks.
                pix[x, y] = (transparent_rgb[0], transparent_rgb[1], transparent_rgb[2], 0)
            else:
                pix[x, y] = (r, g, b, 255)
    return img


def process_main_stickers(raw_paths: Iterable[Path], cfg: PackConfig) -> list[Path]:
    out_dir = ensure_dir(cfg.project_dir / "主表情240")
    outputs: list[Path] = []
    for src in raw_paths:
        img = Image.open(src).convert("RGBA")
        img = center_crop(img, 1.0)
        if cfg.transparent_mode == "remove":
            img = remove_edge_connected_bg(img)
        elif cfg.transparent_mode == "auto":
            # 文字型撞色背景通常应保留；只有四角颜色接近且很浅时才去底。
            bg = detect_bg(img)
            if max(bg) > 220:
                img = remove_edge_connected_bg(img)
        # Pixel stickers should stay hard-edged; LANCZOS can create semi-transparent halos/blocks.
        img = img.resize((240, 240), Image.Resampling.NEAREST)
        out = out_dir / src.name
        img.save(out, "PNG", optimize=True)
        outputs.append(out)
        print(f"  ✓ 主表情: {out.name}")
    return outputs


def make_canvas(size: tuple[int, int], color: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGBA", size, color + (255,))


def paste_fit(canvas: Image.Image, img: Image.Image, box: tuple[int, int, int, int]) -> None:
    x, y, w, h = box
    src = img.convert("RGBA")
    src.thumbnail((w, h), Image.Resampling.LANCZOS)
    px = x + (w - src.width) // 2
    py = y + (h - src.height) // 2
    canvas.alpha_composite(src, (px, py))


def draw_centered(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font: ImageFont.ImageFont, fill: tuple[int, int, int], stroke_width: int = 0) -> None:
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    x = xy[0] - (bbox[2] - bbox[0]) // 2
    y = xy[1] - (bbox[3] - bbox[1]) // 2
    draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=(0, 0, 0))


def make_assets(main_paths: list[Path], cfg: PackConfig) -> dict[str, Path]:
    out_dir = ensure_dir(cfg.project_dir / "配套图")
    imgs = [Image.open(p).convert("RGBA") for p in main_paths]
    if not imgs:
        raise ValueError("没有主表情，无法生成配套图")

    # cover
    cover = Image.new("RGBA", (240, 240), (0, 0, 0, 0))
    paste_fit(cover, imgs[0], (0, 0, 240, 240))
    cover_path = out_dir / "cover_240.png"
    cover.save(cover_path, "PNG", optimize=True)

    # icon
    icon = cover.resize((50, 50), Image.Resampling.LANCZOS)
    icon_path = out_dir / "icon_50.png"
    icon.save(icon_path, "PNG", optimize=True)

    # banner
    banner = make_canvas((750, 400), (38, 18, 86))
    draw = ImageDraw.Draw(banner)
    title_font = load_font(54)
    draw_centered(draw, (375, 58), cfg.album_name, title_font, (0, 255, 238), 3)
    slots = [(25, 115, 135, 220), (165, 135, 120, 190), (295, 105, 160, 245), (470, 135, 120, 190), (600, 115, 135, 220)]
    for slot, img in zip(slots, imgs[:5]):
        paste_fit(banner, img, slot)
    banner_path = out_dir / "banner_750x400.png"
    banner.convert("RGB").save(banner_path, "PNG", optimize=True)

    # reward guide
    guide = make_canvas((750, 560), (255, 105, 180))
    draw = ImageDraw.Draw(guide)
    draw_centered(draw, (375, 72), cfg.tip, load_font(48), (255, 255, 80), 3)
    paste_fit(guide, imgs[0], (210, 130, 330, 330))
    guide_path = out_dir / "reward_guide_750x560.png"
    guide.convert("RGB").save(guide_path, "PNG", optimize=True)

    # reward thanks
    thanks = make_canvas((750, 750), (18, 18, 38))
    draw = ImageDraw.Draw(thanks)
    draw_centered(draw, (375, 72), cfg.thanks, load_font(48), (255, 210, 64), 3)
    grid = [(90, 150, 170, 170), (290, 140, 170, 170), (490, 150, 170, 170), (185, 370, 170, 170), (395, 370, 170, 170)]
    for slot, img in zip(grid, imgs[:5]):
        paste_fit(thanks, img, slot)
    thanks_path = out_dir / "reward_thanks_750x750.png"
    thanks.convert("RGB").save(thanks_path, "PNG", optimize=True)

    return {
        "cover": cover_path,
        "icon": icon_path,
        "banner": banner_path,
        "reward_guide": guide_path,
        "reward_thanks": thanks_path,
    }


# -----------------------------
# Copy and packaging
# -----------------------------

def write_copy(prompts: list[StickerPrompt], cfg: PackConfig) -> list[Path]:
    out_dir = ensure_dir(cfg.project_dir / "文案")
    lines = [
        f"# {cfg.album_name}",
        "",
        f"表情专辑名称：{cfg.album_name}",
        f"表情介绍：{cfg.intro}",
        f"赞赏引导语：{cfg.tip}",
        f"赞赏致谢语：{cfg.thanks}",
        "",
        "## 表情列表",
    ]
    for item in prompts:
        lines.append(f"{item.id}. {item.title}")
    md = out_dir / "album.md"
    txt = out_dir / "文案.txt"
    content = "\n".join(lines) + "\n"
    md.write_text(content, encoding="utf-8")
    txt.write_text(content, encoding="utf-8")
    return [md, txt]


def validate_outputs(cfg: PackConfig, expected_count: int) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "errors": []}
    main_dir = cfg.project_dir / "主表情240"
    main_files = sorted(main_dir.glob("*.png"))
    if len(main_files) != expected_count:
        result["ok"] = False
        result["errors"].append(f"主表情数量 {len(main_files)} != {expected_count}")
    for p in main_files:
        img = Image.open(p)
        if img.size != (240, 240):
            result["ok"] = False
            result["errors"].append(f"{p.name} 尺寸不是 240×240：{img.size}")
    required_assets = [
        ("banner_750x400.png", (750, 400)),
        ("cover_240.png", (240, 240)),
        ("icon_50.png", (50, 50)),
        ("reward_guide_750x560.png", (750, 560)),
        ("reward_thanks_750x750.png", (750, 750)),
    ]
    for name, size in required_assets:
        p = cfg.project_dir / "配套图" / name
        if not p.exists():
            result["ok"] = False
            result["errors"].append(f"缺少配套图 {name}")
            continue
        img = Image.open(p)
        if img.size != size:
            result["ok"] = False
            result["errors"].append(f"{name} 尺寸不是 {size}：{img.size}")
    return result


def package_zip(cfg: PackConfig) -> Path:
    out_dir = ensure_dir(cfg.project_dir / "打包")
    zip_path = out_dir / f"{safe_filename(cfg.album_name)}_微信表情物料.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirname in ["主表情240", "配套图", "文案"]:
            base = cfg.project_dir / dirname
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(cfg.project_dir))
    return zip_path


# -----------------------------
# CLI
# -----------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seedream 微信表情包全套物料编排")
    parser.add_argument("--prompt-file", required=True, type=Path, help="JSON 或 Markdown 提示词文件")
    parser.add_argument("--project-dir", default=Path.cwd(), type=Path, help="输出所在项目目录，默认当前目录")
    parser.add_argument("--theme", default="微信表情包")
    parser.add_argument("--album-name", default="表情包")
    parser.add_argument("--intro", default="一套风格统一、适合聊天使用的微信表情包。")
    parser.add_argument("--tip", default="喜欢就支持一下")
    parser.add_argument("--thanks", default="谢谢你的喜欢")
    parser.add_argument("--size", default="2K")
    parser.add_argument("--response-format", default="png", choices=["png", "jpeg"])
    parser.add_argument("--optimize", default="true", choices=["true", "false"])
    parser.add_argument("--skip-generate", action="store_true", help="跳过 Seedream，直接处理 原始目录/ 已有图片")
    parser.add_argument("--transparent-mode", default="keep", choices=["keep", "remove", "auto"], help="文字像素图建议 keep，角色图可 remove/auto")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    cfg = PackConfig(
        project_dir=args.project_dir.resolve(),
        prompt_file=args.prompt_file.resolve(),
        theme=args.theme,
        album_name=args.album_name,
        intro=args.intro,
        tip=args.tip,
        thanks=args.thanks,
        size=args.size,
        response_format=args.response_format,
        optimize=args.optimize == "true",
        skip_generate=args.skip_generate,
        transparent_mode=args.transparent_mode,
    )

    if not SEEDREAM_SCRIPT.exists() and not cfg.skip_generate:
        raise FileNotFoundError(f"找不到 Seedream 脚本：{SEEDREAM_SCRIPT}")

    prompts = load_prompts(cfg.prompt_file)
    if len(prompts) != 16:
        print(f"⚠️ 当前提示词数量为 {len(prompts)}，微信上架通常需要 16 张主表情。")

    print(f"项目目录: {cfg.project_dir}")
    print(f"主题: {cfg.theme}")
    print(f"专辑名: {cfg.album_name}")
    print(f"无水印: true")
    print(f"原图目录: {cfg.project_dir / '原始目录'}")

    raw_paths = generate_originals(prompts, cfg)
    main_paths = process_main_stickers(raw_paths, cfg)
    assets = make_assets(main_paths, cfg)
    copy_paths = write_copy(prompts, cfg)
    validation = validate_outputs(cfg, len(prompts))
    zip_path = package_zip(cfg)

    summary = {
        "success": validation["ok"],
        "project_dir": str(cfg.project_dir),
        "raw_dir": str(cfg.project_dir / "原始目录"),
        "main_dir": str(cfg.project_dir / "主表情240"),
        "assets_dir": str(cfg.project_dir / "配套图"),
        "copy_dir": str(cfg.project_dir / "文案"),
        "zip_path": str(zip_path),
        "raw_count": len(raw_paths),
        "main_count": len(main_paths),
        "assets": {k: str(v) for k, v in assets.items()},
        "copy": [str(p) for p in copy_paths],
        "validation": validation,
    }
    summary_path = cfg.project_dir / "打包" / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 完成 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
