---
name: image-process
description: Resize, convert, compress, crop, annotate images and process video/audio. Use when working with image files or when output needs to be an image.
metadata: {"ccbot":{"emoji":"🖼️","requires":{"bins":["convert"]}}}
---

# Image Processing Skill

Uses ImageMagick (`convert`) and `ffmpeg`. Output images to `output/` for Feishu delivery.

## Setup Check

```bash
which convert && convert --version | head -1    # ImageMagick
which ffmpeg && ffmpeg -version 2>&1 | head -1  # ffmpeg
```

Install: `brew install imagemagick ffmpeg` / `apt install imagemagick ffmpeg`

## Resize & Convert

```bash
# Resize to max 1200px wide, keep aspect ratio
convert input.png -resize 1200x1200\> output/resized.jpg

# Convert format
convert input.heic output/photo.jpg
convert input.pdf[0] output/page1.png    # PDF page to image

# Compress JPEG (quality 0-100)
convert input.jpg -quality 80 output/compressed.jpg

# Batch resize all PNGs
mkdir -p output
for f in *.png; do
  convert "$f" -resize 800x\> "output/${f%.png}.jpg"
done
```

## Crop & Annotate

```bash
# Crop: WxH+X+Y
convert input.png -crop 400x300+100+50 +repage output/cropped.png

# Add text watermark
convert input.png \
  -font Arial -pointsize 36 -fill "rgba(255,255,255,0.6)" \
  -gravity SouthEast -annotate +20+20 "© ccbot 2026" \
  output/watermarked.png

# Add border
convert input.png -bordercolor white -border 20x20 output/bordered.png
```

## Create Thumbnails

```bash
# Grid thumbnail from multiple images
convert input1.jpg input2.jpg input3.jpg input4.jpg \
  -geometry 400x300+4+4 -background white \
  montage - output/grid.jpg
```

## Screenshot (macOS)

```bash
# Full screen
screencapture -x output/screenshot.png

# Interactive selection
screencapture -i output/selection.png

# Window by app name
screencapture -l $(osascript -e 'tell app "Safari" to id of window 1') output/safari.png
```

## Video: Extract / Convert

```bash
# Extract a frame at 5s
ffmpeg -ss 5 -i video.mp4 -frames:v 1 output/frame.jpg

# Trim video (from 1:30 to 2:45)
ffmpeg -ss 90 -to 165 -i input.mp4 -c copy output/clip.mp4

# Convert video format
ffmpeg -i input.mov -c:v libx264 -crf 23 -c:a aac output/video.mp4

# Extract audio
ffmpeg -i video.mp4 -vn -c:a mp3 -q:a 2 output/audio.mp3

# GIF from video (3-second clip)
ffmpeg -ss 10 -t 3 -i input.mp4 -vf "fps=15,scale=480:-1" output/clip.gif
```

## Generate Simple Charts (Python fallback)

```bash
uv run --with matplotlib pillow python3 - <<'EOF'
import matplotlib.pyplot as plt, matplotlib.patches as mpatches

# Simple bar chart
categories = ["Jan", "Feb", "Mar", "Apr"]
values = [120, 98, 145, 133]

fig, ax = plt.subplots(figsize=(8, 5))
bars = ax.bar(categories, values, color=["#4A90D9", "#7B68EE", "#48C9B0", "#F39C12"])
ax.set_title("Monthly Data", fontsize=14, fontweight="bold")
ax.set_ylabel("Value")
for bar, val in zip(bars, values):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 2, str(val),
            ha="center", fontsize=10)
plt.tight_layout()
plt.savefig("output/chart.png", dpi=150)
print("Saved: output/chart.png")
EOF
```

## Tips

- Always output to `output/` — ccbot auto-delivers via Feishu.
- Use `\>` in ImageMagick resize to only shrink (never upscale).
- For HEIC photos (iPhone), install `heif-convert`: `brew install libheif`.
- Large batches: run in parallel with `xargs -P 4`.
