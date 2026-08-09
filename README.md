# Argentina History Wallpapers

Create and maintain a rotating macOS wallpaper folder from **official public
high-resolution historical photographs of Argentina** — the collections of the
**Archivo General de la Nación Argentina (AGN)** and related public-domain
Argentine archives.

The script:

1. Creates `~/Pictures/Argentina History Wallpapers/`.
2. Downloads images from **trusted official sources only** (see §5).
3. Detects your Mac's native display resolution.
4. Center-crops + resizes each image to your display's aspect ratio **without
   distortion and without upscaling**.
5. Saves optimized JPGs to `.../processed/`, with a small, subtle caption naming
   the subject (from the official image description).
6. Tracks source URLs **and** file hashes to avoid duplicate downloads.
7. Prints clear instructions for enabling macOS folder rotation (Shuffle).

> Imagery is **not** taken from unofficial wallpaper sites. Low-resolution
> images are skipped, never upscaled, and never stretched/distorted. Only
> public-domain / freely-licensed files are used, and the credit string is saved
> in a `.credit.txt` sidecar next to each image.

---

## Why Wikimedia Commons for AGN imagery?

The Archivo General de la Nación's own online catalogs
(`agnargentina.gob.ar`, `atom.mininterior.gob.ar`) show only low-resolution
previews and **charge per high-resolution copy** (delivered by manual request,
not download). They are also behind a WAF that blocks automated access.

The same official material — files **provided by the AGN** and other
public-domain Argentine collections — is published at full resolution on
**Wikimedia Commons**, each with a machine-readable license and the AGN credit.
This script therefore uses the Commons API as the reliable high-resolution
mirror, and preserves the "Archivo General de la Nación Argentina" credit on
every image.

---

## 1. Install dependencies

Requires **Python 3.10+** and **macOS** (for wallpaper configuration).

```bash
cd "~/Library/CloudStorage/Dropbox/projects/history-wallpapers"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Run

```bash
# Download up to 5 new images, process them, print manual rotation setup
python3 argentina_wallpapers.py --limit 5 --interval 30min

# Pull from a specific official collection
python3 argentina_wallpapers.py --category "Colección Witcomb" --limit 5

# Inspect configured sources and allowed hosts
python3 argentina_wallpapers.py --list-sources
```

> The script **never changes your wallpaper itself** — it only downloads,
> processes, and prints the one-time manual macOS setup (see §3).

### Command-line options

| Option | Description |
|---|---|
| `--limit N` | Maximum number of **new** images to download/process. |
| `--interval {5min,15min,30min,1h,1d}` | Rotation interval hint used in setup + scheduling output. |
| `--category NAME` | Wikimedia Commons category (repeatable; overrides the seed list). |
| `--depth N` | Subcategory recursion depth (default 1; 0 = direct files only). |
| `--list-sources` | Print configured categories/hosts and exit. |

Each seed category is crawled **recursively into its subcategories** (default
one level), so a collection like *Buenos Aires in the 19th century* automatically
pulls its per-decade subcategories, and the AGN categories pull their photo fonds
(Caras y Caretas, etc.). Use `--depth 2` for deeper collections or `--depth 0` to
stay in the named category only.

## 3. Change the wallpaper interval

macOS controls the *rotation timing*, not the script. After the first run:

> **System Settings → Wallpaper → Add Folder or Album → `+`**
> - Select `~/Pictures/Argentina History Wallpapers/processed`
> - Enable **Shuffle** / **Change picture**
> - Pick the cadence (Every 5 Minutes / 15 Minutes / 30 Minutes / Hour / Day)

The `--interval` flag prints the matching macOS label so you pick the right one.

### Scheduling new downloads (launchd)

Example `~/Library/LaunchAgents/com.argentina.wallpapers.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.argentina.wallpapers</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/ABSOLUTE/PATH/TO/argentina_wallpapers.py</string>
    <string>--limit</string><string>5</string>
  </array>
  <key>StartInterval</key><integer>86400</integer>  <!-- seconds: 1d -->
  <key>RunAtLoad</key><true/>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.argentina.wallpapers.plist
```

`StartInterval` seconds by `--interval`: `5min`=300, `15min`=900, `30min`=1800,
`1h`=3600, `1d`=86400.

## 4. Add more image sources

Sources are **Wikimedia Commons categories** of official/public-domain Argentine
historical imagery. Two ways:

1. **Per run:** `--category "Category Name"` (repeatable).
2. **Permanently:** edit `argentina_wallpapers.py` → `DEFAULT_CATEGORIES`.

Only files with an accepted free license (public domain / CC0 / CC BY) and a
long edge of at least `MIN_LONG_EDGE` px are used. Downloads from any host not in
`ALLOWED_HOSTS` are refused by design.

Seed categories (each recursed into its subcategories):

**Archivo General de la Nación**
- `Files provided by Archivo General de la Nación Argentina`
- `Images from Archivo General de la Nación Argentina`

**Historic photographic studios / photographers**
- `Colección Witcomb` · `Witcomb (photographic studio)` · `Alexander Witcomb`
- `Photographs by Christiano Junior` (pioneer 1860s–70s photographer)
- `Views of the City of Buenos Aires commissioned by the Buenos Aires Municipality (Boote, Croce, et al.)`

**Provincial / regional historical imagery**
- `Buenos Aires in the 19th century` (recurses per-decade + La Plata subcats)
- `Black and white photographs of Argentina`

> Note on provincial archives: dedicated provincial-archive categories
> (Santa Fe, Córdoba, Mendoza, …) are largely unpopulated on Commons today.
> Regional coverage therefore comes via place/era categories such as
> *Buenos Aires in the 19th century* and *Black and white photographs of La Plata*
> (auto-discovered by recursion). Add a `--category "…"` when a provincial
> archive publishes a populated Commons category.

## 5. Attribution requirements

AGN / public-domain images may be freely reused but the archive **asks for
credit**. Always display the credit line, e.g.:

> **Archivo General de la Nación Argentina** (via Wikimedia Commons)

The script:

- Saves each image's Commons `Credit` / `Artist` (and date) to a `*.credit.txt`
  sidecar next to the raw and processed file.
- Preserves embedded EXIF metadata when re-saving JPGs.

Review each image's specific license/credit on its Commons page before
redistribution. See:

- Archivo General de la Nación: <https://www.argentina.gob.ar/interior/archivo-general-de-la-nacion>
- AGN files on Commons: <https://commons.wikimedia.org/wiki/Category:Files_provided_by_Archivo_General_de_la_Naci%C3%B3n_Argentina>
- Commons reuse guidance: <https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia>

---

## Project structure

```
history-wallpapers/
├── argentina_wallpapers.py   # main script
├── requirements.txt
└── README.md

~/Pictures/Argentina History Wallpapers/   # created at runtime
├── raw/                        # original downloads + .credit.txt sidecars
├── processed/                  # display-fitted optimized JPGs (use this folder)
└── state.json                  # dedup tracking (URLs + SHA-256 hashes)
```

## Notes & limitations

- Seed categories are examples; add your own with `--category` or edit the list.
- Some AGN scans are large TIFFs; they are re-saved as optimized JPGs for use as
  wallpaper. Pillow's decompression-bomb guard is raised for the trusted host.
- The script never changes the wallpaper. You point macOS at the processed
  folder once (see §3); macOS then handles rotation and timing.
- Designed for macOS. On other platforms it still downloads/processes images;
  only the wallpaper-setup instructions are macOS-specific.
```
