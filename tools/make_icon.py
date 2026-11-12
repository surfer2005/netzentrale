"""App-Symbol: Bildschirm mit Netzwerk-Knoten."""
import os
import sys
from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
bg = Image.new("RGBA", (S, S))
d = ImageDraw.Draw(bg)
for y in range(S):
    t = y / S
    d.line([(0, y), (S, y)], fill=(int(16 + 22 * t), int(52 + 40 * t), int(110 + 50 * t), 255))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([100, 100, S - 100, S - 100], radius=185, fill=255)
img.paste(bg, (0, 0), mask)
d = ImageDraw.Draw(img)

white, cyan, green = (240, 245, 255, 255), (90, 200, 250, 255), (70, 210, 120, 255)
# Monitor
d.rounded_rectangle([230, 250, 794, 640], radius=36, outline=white, width=30)
d.rectangle([470, 640, 554, 720], fill=white)
d.rounded_rectangle([380, 712, 644, 748], radius=18, fill=white)
# Netzwerk-Knoten im Bildschirm
nodes = [(512, 340), (360, 540), (512, 540), (664, 540)]
for (x, y) in nodes[1:]:
    d.line([nodes[0], (x, y)], fill=cyan, width=18)
for i, (x, y) in enumerate(nodes):
    r = 44 if i == 0 else 36
    d.ellipse([x - r, y - r, x + r, y + r], fill=cyan if i else white)
# grüner Online-Punkt
d.ellipse([700, 760, 800, 860], fill=green)

out = sys.argv[1]
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
img.save(out + ".png")
img.save(out + ".icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
img.save(out + ".ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
