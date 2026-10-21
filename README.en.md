# pdf-compress

**Smart PDF compression — strip the fake bloat first, then compress the real content.**

Most of a bloated PDF's size isn't content at all — it's **invisible junk**. This tool removes that junk first, then compresses only what actually needs compressing, as gently as possible.

Real-world result: a 63-page brand book went from **34.4 MB to 16.3 MB (-52.7%)** — with 100% of the text preserved, zero loss to vector graphics, and no visible quality difference.

---

## The problem it solves

Most PDF compressors blindly downscale everything until the file fits, turning documents into mush. But the real situation is often this:

**An Illustrator-exported PDF where 12 MB of the 35 MB is Illustrator private data.**

`/PieceInfo` → `/AIPDFPrivateData` is the layer-reconstruction data Adobe Illustrator embeds so it can reopen the PDF for editing later. It's useless for viewing, printing, emailing, or uploading — **yet it routinely accounts for 30%–50% of the file size.**

So the strategy is:

| Step | What it does | Trade-off |
|---|---|---|
| 1 | Strip Illustrator private data | Lose AI layer-editability |
| 2 | Resample **only images above the target DPI** | High-res images get softer |
| 3 | Subset fonts + dedupe objects + compress streams | None |

**Text and vector graphics are never touched**, so text stays selectable, searchable, and copyable.

---

## Install

```bash
pip install pymupdf
```

One dependency. Nothing else.

## Quick start

```bash
# Most common: strip AI data + auto-pick a level to fit under 20 MB
python pdf_compress.py input.pdf -s -t 20

# Default 'high' level (balanced quality/size)
python pdf_compress.py input.pdf

# Batch, output to a directory
python pdf_compress.py *.pdf -o out/
```

Output defaults to `name_compressed.pdf`. **The original is never overwritten.**

---

## Options

| Flag | Description |
|---|---|
| `inputs` | One or more input PDFs (globs supported) |
| `-o, --output` | Output file or directory; defaults to a `_compressed` suffix |
| `-l, --level` | Compression level, default `high` |
| `-s, --strip-ai` | Strip Illustrator private data |
| `-t, --target MB` | Target size; auto-selects the level that fits |
| `--version` | Show version |

### Levels

Highest quality → smallest size:

| Level | Image policy | Use case |
|---|---|---|
| `ultra` | Only images > 201 DPI → 200 DPI, q72 | Print or designer review |
| `high` | Only images > 121 DPI → 120 DPI, q75 | **Recommended** — good for screen and print |
| `balanced` | Only images > 101 DPI → 100 DPI, q80 | Smaller, still good |
| `strong` | Only images > 101 DPI → 100 DPI, q65 | Screen preview only |
| `lossless` | No image recompression | Clean up junk, accept zero quality change |

> Note the "only images > N DPI" design: **anything below the threshold is left completely untouched.** Images that render small on the page are usually already at a sensible resolution — recompressing them would cost quality for nothing.

### Auto level selection (`-t`)

Given a target size, the tool tries levels **from highest quality downward** and picks the first one that fits:

```bash
python pdf_compress.py brandbook.pdf -s -t 20
```

```
brandbook.pdf
  34.4 MB -> 16.3 MB  saved 52.7%  [level high | stripped AI data on 63 pages]
  output: brandbook_compressed.pdf
```

If **stripping AI data alone already hits the target**, not a single image gets touched — the ideal outcome.

---

## How it works

### 1. Stripping Illustrator private data

Illustrator-exported PDFs attach a `/PieceInfo` entry on every page pointing to a shared private-data dictionary holding hundreds of `AIPDFPrivateDataN` objects. These are **typeless raw streams** (only `/Length`, no `/Subtype`), so ordinary "remove unused objects" passes can't touch them — you must first sever the `/PieceInfo` entry, only then do they become reclaimable garbage.

### 2. Image resampling

Uses PyMuPDF's `rewrite_images()` with a DPI threshold. It handles CMYK color spaces and JPEG re-encoding correctly — far more reliable than a hand-rolled PIL pipeline, which easily trips over the CMYK inversion convention (PDF CMYK JPEGs often carry a `/Decode [1 0 1 0 1 0 1 0]` inversion array; get it wrong and the whole document's colors are destroyed).

### 3. Cleanup

- `subset_fonts()` — keep only the glyphs actually used
- `save(garbage=4, deflate=True, clean=True, use_objstms=1)` — dedupe objects, compress streams and the cross-reference table

---

## Quality

We don't trust "looks about the same," so it's verified with objective metrics.

Test corpus: 63-page brand book, 1920×1080pt pages, 31 CMYK images.

| Metric | Result |
|---|---|
| Size | 34.43 MB → **16.27 MB** (-52.7%) |
| Pages | 63 → 63 |
| Page dimensions | All 63 identical |
| **Extractable text** | 49,371 chars → **49,371 chars (100%)**, page-by-page identical |
| Font references | 171 → 171 |
| PSNR (per-page render diff) | min 39.9 dB, mean 95.0 dB |
| SSIM | mean 0.9994, min 0.9343 |

For reference: PSNR above 35 dB is generally considered visually indistinguishable; above 40 dB is high fidelity. All 63 pages scored above 35 dB.

To verify yourself: render both the original and compressed files page by page with PyMuPDF, then compute PSNR/SSIM per pixel.

---

## FAQ

**Is text still searchable/copyable after compression?**
Yes. Text and vector graphics are untouched; the text layer is 100% preserved.

**Can I still edit it in Illustrator after `-s`?**
No layer reconstruction. **Always keep the original** — distribute the compressed one, edit the original.

**Does it handle scanned documents?**
Yes. Scanned PDFs are images end to end; `-l strong` typically saves 50%–90%.

**What if the file is encrypted?**
The tool skips it with a message; decrypt first.

**Can I hit an exact size?**
`-t 20` walks levels from highest quality down and picks the first that fits. If even `strong` overshoots, it uses `strong` and flags "target not met".

**Why won't my file compress?**
Likely: (1) it's mostly vector (text/diagram PDFs are already compact), (2) its images are already optimized — nothing is oversampled, or (3) try `-s` to strip AI data.

---

## License

**GNU AGPL-3.0** — see [LICENSE](LICENSE).

### What this means for you

✅ Free to use, modify, distribute — even commercially, even for a fee
✅ Fine for internal tools and personal projects
⚠️ **Must** ship full source code when you distribute it
⚠️ **Must** keep copyright and license notices
❌ **Cannot** embed in a closed-source product without open-sourcing
❌ **Cannot** run as a network service without offering source to its users (AGPL §13)

**Why AGPL?** Because this project depends on [PyMuPDF](https://github.com/pymupdf/PyMuPDF), which is dual-licensed under AGPL-3.0 or a commercial license from Artifex. AGPL's copyleft requires programs using it to be distributed under AGPL as well.

**If AGPL doesn't fit your use case** (e.g., closed-source integration), two options:
1. Buy a commercial PyMuPDF license from [Artifex](https://artifex.com/licensing/)
2. Rewrite against a permissively licensed library such as [pikepdf](https://github.com/pikepdf/pikepdf) (MPL-2.0) or [qpdf](https://github.com/qpdf/qpdf) (Apache-2.0) — though neither offers PyMuPDF's ready-made image resampling, so the core logic would need reimplementing

---

## Credits

- [PyMuPDF](https://github.com/pymupdf/PyMuPDF) © Artifex Software, Inc. — this project exists because of it
- [MuPDF](https://mupdf.com/) — PyMuPDF's underlying engine

---

[中文说明](README.md)
