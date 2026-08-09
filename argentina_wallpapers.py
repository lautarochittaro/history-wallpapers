#!/usr/bin/env python3
"""
Argentina History Wallpapers — macOS wallpaper automation.

Downloads official public high-resolution HISTORICAL photographs of Argentina
(from the Archivo General de la Nación Argentina and related public collections),
crops/resizes them to the current Mac display aspect ratio without distortion,
and prints the one-time macOS setup to rotate through the processed folder.

The images are sourced through the Wikimedia Commons API, which hosts the
photographs *provided by the Archivo General de la Nación Argentina* (AGN) and
other public-domain Argentine historical collections, each with a machine-readable
license and credit. AGN's own site (agnargentina / atom.mininterior.gob.ar) only
serves low-res previews online and charges per hi-res copy, so Commons is used as
the reliable high-resolution mirror of the same official material.

Trusted sources only. No unofficial wallpaper sites. No upscaling. No distortion.
Public-domain / freely-licensed files only; the credit line is preserved.

Usage:
    python3 argentina_wallpapers.py --limit 10 --interval 30min
    python3 argentina_wallpapers.py --list-sources
    python3 argentina_wallpapers.py --category "Colección Witcomb" --limit 5

Attribution: imagery is credited to its Commons "Credit"/"Artist" fields
(typically "Archivo General de la Nación Argentina"). See README.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError as exc:  # pragma: no cover - import guard
    sys.stderr.write(
        f"Missing dependency: {exc.name}. Run: pip install -r requirements.txt\n"
    )
    raise SystemExit(1)

# Historical archive scans can be very large (full-plate TIFFs). Raise Pillow's
# decompression-bomb guard since the source is a trusted allow-listed host.
Image.MAX_IMAGE_PIXELS = 500_000_000

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

BASE_DIR = Path.home() / "Pictures" / "Argentina History Wallpapers"
PROCESSED_DIR = BASE_DIR / "processed"
RAW_DIR = BASE_DIR / "raw"
STATE_FILE = BASE_DIR / "state.json"

USER_AGENT = (
    "ArgentinaHistoryWallpapers/1.0 "
    "(https://github.com/lautarochittaro/history-wallpapers; personal wallpaper tool)"
)
REQUEST_TIMEOUT = 90
JPEG_QUALITY = 90

COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Curated seed list of OFFICIAL Wikimedia Commons categories holding
# high-resolution historical photographs provided by / sourced from the
# Archivo General de la Nación Argentina and related public collections.
# Add more with --category, or edit this list.
DEFAULT_CATEGORIES = [
    "Files provided by Archivo General de la Nación Argentina",
    "Images from Archivo General de la Nación Argentina",
    # --- historic Argentine photographic studios / collections ---
    "Colección Witcomb",                       # Witcomb studio collection, held by AGN
    "Witcomb (photographic studio)",           # Witcomb studio (nested subcats)
    "Alexander Witcomb",                        # founder; portrait/city plates
    "Photographs by Christiano Junior",        # pioneer 1860s–70s Argentine photographer
    "Views of the City of Buenos Aires commissioned by the Buenos Aires Municipality (Boote, Croce, et al.)",
    # --- provincial / regional historical imagery (recursed for depth) ---
    "Buenos Aires in the 19th century",
    "Black and white photographs of Argentina",  # broader public-domain historical B&W
]

# How many levels of subcategories to descend from each seed category.
# 0 = only files directly in the category; 1 = also its subcategories, etc.
# Overridable with --depth.
DEFAULT_DEPTH = 1

# Hard cap on total categories visited per run (loop / runaway guard).
MAX_CATEGORIES_VISITED = 300

# Allow-list of hostnames we will download from. Anything else is refused.
ALLOWED_HOSTS = {
    "commons.wikimedia.org",
    "upload.wikimedia.org",
}

# Only these file extensions are treated as usable raster imagery.
RASTER_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff")

# Skip anything smaller than this on the long edge — too small for a crisp
# full-screen wallpaper (and never upscaled).
MIN_LONG_EDGE = 1600

# Free/public licenses we accept (substring match, case-insensitive) against the
# Commons LicenseShortName field. Anything not matching is skipped.
ACCEPTED_LICENSE_HINTS = (
    "public domain", "pd", "cc0", "cc by", "cc-by", "attribution", "no restrictions",
)

INTERVAL_SECONDS = {
    "5min": 300, "15min": 900, "30min": 1800, "1h": 3600, "1d": 86400,
}

# --------------------------------------------------------------------------- #
# State (dedup tracking)
# --------------------------------------------------------------------------- #


@dataclass
class State:
    urls: dict          # download_url -> sha256
    hashes: set         # set of sha256 of downloaded originals

    @classmethod
    def load(cls) -> "State":
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                return cls(urls=data.get("urls", {}), hashes=set(data.get("hashes", [])))
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[warn] could not read state file ({exc}); starting fresh")
        return cls(urls={}, hashes=set())

    def save(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps({"urls": self.urls, "hashes": sorted(self.hashes)}, indent=2)
        )

    def seen_url(self, url: str) -> bool:
        return url in self.urls

    def seen_hash(self, digest: str) -> bool:
        return digest in self.hashes

    def record(self, url: str, digest: str) -> None:
        self.urls[url] = digest
        self.hashes.add(digest)


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def host_allowed(url: str) -> bool:
    return urlparse(url).netloc.lower() in ALLOWED_HOSTS


# --------------------------------------------------------------------------- #
# Wikimedia Commons source
# --------------------------------------------------------------------------- #


@dataclass
class ImageCandidate:
    page_title: str          # e.g. "File:Alfonsina Storni.jpg"
    download_url: str        # hi-res original on upload.wikimedia.org
    width: int
    height: int
    credit: str | None
    title: str | None        # human caption (subject)
    date: str | None


def _clean_html(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"<[^>]+>", "", text)          # strip tags
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _license_ok(license_name: str | None) -> bool:
    if not license_name:
        return False
    low = license_name.lower()
    return any(hint in low for hint in ACCEPTED_LICENSE_HINTS)


def fetch_category_files(
    session: requests.Session, category: str, batch_limit: int
) -> list[ImageCandidate]:
    """Query the Commons API for files in a category with full imageinfo.

    Returns hi-res, freely-licensed raster candidates. Uses a generator query so
    the file list and its imageinfo (URL, size, license, credit) come together.
    """
    title = category if category.lower().startswith("category:") else f"Category:{category}"
    params = {
        "action": "query",
        "format": "json",
        "generator": "categorymembers",
        "gcmtitle": title,
        "gcmtype": "file",
        "gcmlimit": str(min(batch_limit, 500)),
        "prop": "imageinfo",
        "iiprop": "url|size|extmetadata",
    }
    candidates: list[ImageCandidate] = []
    cont: dict = {}
    while True:
        try:
            r = session.get(COMMONS_API, params={**params, **cont}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            print(f"[error] Commons query failed for '{category}': {exc}")
            break

        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            info = (page.get("imageinfo") or [None])[0]
            if not info:
                continue
            url = info.get("url", "")
            if not url.lower().split("?")[0].endswith(RASTER_EXT):
                continue
            if not host_allowed(url):
                continue
            em = info.get("extmetadata", {})
            license_name = em.get("LicenseShortName", {}).get("value")
            if not _license_ok(license_name):
                continue
            w, h = int(info.get("width", 0)), int(info.get("height", 0))
            if max(w, h) < MIN_LONG_EDGE:
                continue

            credit_parts = []
            for key in ("Credit", "Artist"):
                val = _clean_html(em.get(key, {}).get("value"))
                if val and val.lower() != "unknown author":
                    credit_parts.append(val)
            credit = " / ".join(dict.fromkeys(credit_parts)) or "Unknown author (via Wikimedia Commons)"

            desc = _clean_html(em.get("ImageDescription", {}).get("value"))
            date = _clean_html(em.get("DateTimeOriginal", {}).get("value"))
            # Prefer the human description; fall back to the cleaned file name.
            caption = desc
            if not caption:
                caption = re.sub(r"^File:", "", page.get("title", ""))
                caption = re.sub(r"\.[^.]+$", "", caption).replace("_", " ").strip()
            if caption and len(caption) > 90:
                caption = caption[:87].rstrip() + "…"

            candidates.append(ImageCandidate(
                page_title=page.get("title", ""),
                download_url=url, width=w, height=h,
                credit=credit, title=caption, date=date,
            ))

        cont = data.get("continue", {})
        if not cont or len(candidates) >= batch_limit:
            break
    return candidates


def fetch_subcategories(session: requests.Session, category: str) -> list[str]:
    """Return the immediate subcategory names (without the 'Category:' prefix)."""
    title = category if category.lower().startswith("category:") else f"Category:{category}"
    params = {
        "action": "query", "format": "json",
        "list": "categorymembers", "cmtitle": title,
        "cmtype": "subcat", "cmlimit": "500",
    }
    subs: list[str] = []
    cont: dict = {}
    while True:
        try:
            r = session.get(COMMONS_API, params={**params, **cont}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            print(f"[warn] subcategory query failed for '{category}': {exc}")
            break
        for m in data.get("query", {}).get("categorymembers", []):
            subs.append(re.sub(r"^Category:", "", m.get("title", "")))
        cont = data.get("continue", {})
        if not cont:
            break
    return subs


def fetch_category_recursive(
    session: requests.Session,
    category: str,
    depth: int,
    batch_limit: int,
    visited: set[str],
) -> list[ImageCandidate]:
    """Collect candidates from a category and its subcategories up to `depth`.

    A shared `visited` set guards against category cycles and re-visits, and the
    global MAX_CATEGORIES_VISITED cap bounds a runaway crawl.
    """
    if category in visited or len(visited) >= MAX_CATEGORIES_VISITED:
        return []
    visited.add(category)

    candidates = fetch_category_files(session, category, batch_limit)
    print(f"[info] {len(candidates):3} usable file(s) from '{category}'"
          + (f" (depth {depth})" if depth else ""))

    if depth > 0:
        for sub in fetch_subcategories(session, category):
            if len(visited) >= MAX_CATEGORIES_VISITED:
                break
            candidates.extend(
                fetch_category_recursive(session, sub, depth - 1, batch_limit, visited)
            )
    return candidates


# --------------------------------------------------------------------------- #
# Download + dedup
# --------------------------------------------------------------------------- #


def download_image(
    session: requests.Session, candidate: ImageCandidate, state: State
) -> Path | None:
    url = candidate.download_url
    if not host_allowed(url):
        print(f"[skip] non-trusted host: {url}")
        return None
    if state.seen_url(url):
        print(f"[skip] already downloaded (url seen): {candidate.page_title}")
        return None

    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        content = r.content
    except requests.RequestException as exc:
        print(f"[error] download failed {url}: {exc}")
        return None

    digest = hashlib.sha256(content).hexdigest()
    if state.seen_hash(digest):
        print(f"[skip] already downloaded (hash seen): {candidate.page_title}")
        state.urls[url] = digest
        return None

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    name = Path(urlparse(url).path).name or f"{digest[:12]}.jpg"
    raw_path = RAW_DIR / name
    raw_path.write_bytes(content)

    if candidate.credit:
        sidecar = candidate.credit
        if candidate.date:
            sidecar += f" ({candidate.date})"
        sidecar += "\nvia Wikimedia Commons — " + candidate.page_title + "\n"
        raw_path.with_suffix(raw_path.suffix + ".credit.txt").write_text(sidecar)

    state.record(url, digest)
    print(f"[ok]   downloaded {name} ({len(content) // 1024} KiB, "
          f"{candidate.width}x{candidate.height})")
    return raw_path


# --------------------------------------------------------------------------- #
# Display detection + image processing
# --------------------------------------------------------------------------- #


def detect_display_resolution() -> tuple[int, int]:
    """Return the largest native display resolution (width, height) in pixels."""
    fallback = (3456, 2234)  # 16" MacBook Pro native
    try:
        out = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"[warn] display detection failed ({exc}); using {fallback}")
        return fallback

    res = [(int(w), int(h)) for w, h in re.findall(r"Resolution:\s*(\d+)\s*x\s*(\d+)", out)]
    if not res:
        print(f"[warn] no resolution parsed; using {fallback}")
        return fallback
    best = max(res, key=lambda wh: wh[0] * wh[1])
    print(f"[info] detected display resolution: {best[0]}x{best[1]}")
    return best


_CAPTION_FONTS = [
    "/System/Library/Fonts/Supplemental/Avenir Next.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _load_caption_font(size: int):
    for path in _CAPTION_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_caption(img: "Image.Image", text: str) -> "Image.Image":
    """Overlay a small, subtle caption (bottom-left) naming the subject."""
    if not text:
        return img
    rgba = img.convert("RGBA")
    W, H = rgba.size
    size = max(15, round(H * 0.018))
    font = _load_caption_font(size)
    margin = round(H * 0.030)

    overlay = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        _, top, _, bottom = draw.textbbox((0, 0), text, font=font)
        text_h = bottom - top
    except AttributeError:  # very old Pillow
        text_h = size
    x, y = margin, H - margin - text_h
    for dx, dy in ((1, 1), (2, 2)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 90))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 180))
    return Image.alpha_composite(rgba, overlay).convert("RGB")


def process_image(
    raw_path: Path, target_w: int, target_h: int, caption: str | None = None
) -> Path | None:
    """Center-crop + resize to target aspect ratio without distortion or upscaling."""
    try:
        img = Image.open(raw_path)
        img = ImageOps.exif_transpose(img)
    except (OSError, Image.DecompressionBombError) as exc:
        print(f"[error] cannot open {raw_path.name}: {exc}")
        return None

    src_w, src_h = img.size
    if src_w < target_w or src_h < target_h:
        print(
            f"[warn] {raw_path.name} ({src_w}x{src_h}) smaller than display "
            f"({target_w}x{target_h}); fitting to source size, no upscaling"
        )
        target_aspect = target_w / target_h
        if src_w / src_h > target_aspect:
            out_h = src_h
            out_w = round(out_h * target_aspect)
        else:
            out_w = src_w
            out_h = round(out_w / target_aspect)
        target_w, target_h = out_w, out_h

    processed = ImageOps.fit(
        img, (target_w, target_h), method=Image.LANCZOS, centering=(0.5, 0.5),
    )

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / (raw_path.stem + ".jpg")

    exif = img.info.get("exif")
    save_kwargs = dict(format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    if exif:
        save_kwargs["exif"] = exif

    rgb = processed.convert("RGB")
    if caption:
        rgb = draw_caption(rgb, caption)
    rgb.save(out_path, **save_kwargs)
    print(f"[ok]   processed {out_path.name} -> {target_w}x{target_h}"
          + (f'  [“{caption}”]' if caption else ""))

    credit_src = raw_path.with_suffix(raw_path.suffix + ".credit.txt")
    if credit_src.exists():
        out_path.with_suffix(out_path.suffix + ".credit.txt").write_text(
            credit_src.read_text()
        )
    return out_path


# --------------------------------------------------------------------------- #
# macOS wallpaper configuration
# --------------------------------------------------------------------------- #


def print_wallpaper_instructions(interval: str | None) -> None:
    interval_hint = ""
    if interval:
        human = {
            "5min": "Every 5 Minutes", "15min": "Every 15 Minutes",
            "30min": "Every 30 Minutes", "1h": "Every Hour", "1d": "Every Day",
        }.get(interval, interval)
        interval_hint = f'      • Set "Change picture" to: {human}\n'

    print(
        "\n"
        "─────────────────────────────────────────────────────────────────\n"
        "  Manual step (one time) — enable rotating folder on macOS:\n"
        "    System Settings → Wallpaper → Add Folder or Album → '+' →\n"
        f"      • Select: {PROCESSED_DIR}\n"
        "      • Enable 'Shuffle' / 'Change picture'\n"
        f"{interval_hint}"
        "  After this, macOS auto-rotates through the folder. Re-run this\n"
        "  script (e.g. via cron/launchd) to keep adding fresh images.\n"
        "─────────────────────────────────────────────────────────────────\n"
    )


def print_launchd_hint(interval: str | None) -> None:
    if not interval:
        return
    secs = INTERVAL_SECONDS.get(interval)
    if not secs:
        return
    script_path = Path(__file__).resolve()
    print(
        "  To refresh the folder automatically at this interval, schedule the\n"
        "  download step with launchd. Example StartInterval (seconds): "
        f"{secs}\n"
        f"    Command: /usr/bin/python3 {script_path} --limit 5\n"
        "  See README.md → 'Scheduling' for a ready-to-use launchd plist.\n"
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def gather_candidates(
    session: requests.Session, categories: list[str], depth: int, batch_limit: int
) -> list[ImageCandidate]:
    candidates: list[ImageCandidate] = []
    seen_titles: set[str] = set()
    visited: set[str] = set()
    for cat in categories:
        found = fetch_category_recursive(session, cat, depth, batch_limit, visited)
        for c in found:
            if c.page_title not in seen_titles:
                seen_titles.add(c.page_title)
                candidates.append(c)
        # Stop crawling further seed categories once we have a comfortable pool;
        # keeps a small --limit run from touching every seed + subcategory.
        if len(candidates) >= batch_limit:
            break
    return candidates


def run(args: argparse.Namespace) -> int:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    state = State.load()
    session = make_session()

    categories = list(args.category) if args.category else list(DEFAULT_CATEGORIES)
    depth = args.depth if args.depth is not None else DEFAULT_DEPTH
    target_w, target_h = detect_display_resolution()

    # Fetch a generous batch so we still reach --limit after dedup/filtering.
    batch = (args.limit * 4) if args.limit else 200
    candidates = gather_candidates(session, categories, depth, batch)
    print(f"[info] {len(candidates)} unique candidate image(s) after merge")

    processed_count = 0
    for cand in candidates:
        if args.limit and processed_count >= args.limit:
            break
        raw = download_image(session, cand, state)
        state.save()  # persist after each download to survive interruption
        if not raw:
            continue
        out = process_image(raw, target_w, target_h, caption=cand.title)
        if out:
            processed_count += 1

    print(f"[info] processed {processed_count} new image(s)")
    print_wallpaper_instructions(args.interval)
    print_launchd_hint(args.interval)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Download + process official high-resolution historical "
                    "photographs of Argentina (Archivo General de la Nación, via "
                    "Wikimedia Commons) into a rotating macOS wallpaper folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--limit", type=int, default=None, metavar="N",
                   help="Maximum number of NEW images to download/process.")
    p.add_argument("--interval", choices=sorted(INTERVAL_SECONDS),
                   help="Rotation interval hint for macOS setup / scheduling.")
    p.add_argument("--category", action="append", metavar="NAME",
                   help="Wikimedia Commons category name (repeatable). Overrides "
                        "the built-in seed list.")
    p.add_argument("--depth", type=int, default=None, metavar="N",
                   help=f"Subcategory recursion depth (default {DEFAULT_DEPTH}). "
                        "0 = only files directly in each category.")
    p.add_argument("--list-sources", action="store_true",
                   help="Print configured categories and allowed hosts, then exit.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_sources:
        print(f"Seed Commons categories (recursed to depth {DEFAULT_DEPTH}):")
        for c in DEFAULT_CATEGORIES:
            print(f"  {c}")
        print("Allowed hosts:")
        for h in sorted(ALLOWED_HOSTS):
            print(f"  {h}")
        return 0

    if sys.platform != "darwin":
        print("[warn] not macOS; the wallpaper-setup instructions assume macOS.")

    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n[abort] interrupted by user")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
