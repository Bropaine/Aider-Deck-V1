from PIL import Image, ImageDraw

S = 1024
MINT = (0x8D, 0xE6, 0xC1, 255)
INK = (0x0C, 0x28, 0x20, 255)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.20), fill=MINT)

# Chevron as a stroked polyline (slim, like IBM Plex Mono's single guillemet)
w = int(S * 0.105)
pts = [(S * 0.265, S * 0.285), (S * 0.485, S * 0.50), (S * 0.265, S * 0.715)]
d.line(pts, fill=INK, width=w, joint="curve")
r = w // 2
for (x, y) in (pts[0], pts[2]):
    d.ellipse([x - r, y - r, x + r, y + r], fill=INK)

# Cursor bar: underscore position, right of the chevron
bar_x0, bar_x1 = S * 0.545, S * 0.765
bar_y0, bar_y1 = S * 0.688, S * 0.763
d.rectangle([bar_x0, bar_y0, bar_x1, bar_y1], fill=INK)

img.resize((256, 256), Image.LANCZOS).save(
    "aider-deck.ico",
    format="ICO",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
print("aider-deck.ico written")
