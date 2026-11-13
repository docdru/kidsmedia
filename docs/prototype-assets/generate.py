"""Generate illustrative sample thumbnails for the Kids Media prototypes."""
import subprocess, os
from PIL import Image

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)


def stars(n, w, h, seed, r=2.2, color="#FFF6D5"):
    import random
    rnd = random.Random(seed)
    return "".join(
        f'<circle cx="{rnd.uniform(0, w):.0f}" cy="{rnd.uniform(0, h * 0.6):.0f}" r="{rnd.uniform(r * 0.5, r):.1f}" fill="{color}" opacity="{rnd.uniform(0.5, 1):.2f}"/>'
        for _ in range(n))


def badge(text, w, color):
    return (f'<rect x="{w - 132}" y="24" width="108" height="64" rx="32" fill="#FFFFFF"/>'
            f'<text x="{w - 78}" y="70" text-anchor="middle" font-family="DejaVu Sans" font-weight="bold" font-size="38" fill="{color}">{text}</text>')


def svg(w, h, body, bg):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}"><rect width="{w}" height="{h}" fill="{bg}"/>{body}</svg>'


SHIP = ('<path d="M150 330 L330 330 L300 380 L180 380 Z" fill="#2A2140"/>'
        '<rect x="236" y="190" width="8" height="140" fill="#2A2140"/>'
        '<path d="M244 196 L320 300 L244 300 Z" fill="#F4E9D0"/><path d="M236 214 L170 300 L236 300 Z" fill="#E8DCC0"/>'
        '<path d="M244 190 l34 10 l-34 10 z" fill="#E8553D"/>')
SEA = '<path d="M0 370 Q60 350 120 370 T240 370 T360 370 T480 370 V480 H0 Z" fill="#2B3A8F"/><path d="M0 400 Q60 385 120 400 T240 400 T360 400 T480 400 V480 H0 Z" fill="#22307A"/>'
MOON = '<circle cx="{x}" cy="{y}" r="70" fill="#F7E7A6"/><circle cx="{x2}" cy="{y2}" r="62" fill="{bg}"/>'

moon_eps = {
    "p1": ("#1E2A6B", SHIP, (360, 120)),
    "p2": ("#2B2466",
           '<rect x="110" y="170" width="260" height="190" rx="10" fill="#E9DDBD" transform="rotate(-6 240 265)"/>'
           '<path d="M150 320 C190 240 250 330 300 230" stroke="#B4432E" stroke-width="6" stroke-dasharray="14 10" fill="none"/>'
           '<path d="M290 215 l24 24 M314 215 l-24 24" stroke="#B4432E" stroke-width="8" stroke-linecap="round"/>'
           '<circle cx="160" cy="320" r="10" fill="#2A2140"/>', (120, 110)),
    "p3": ("#182050",
           '<ellipse cx="200" cy="150" rx="120" ry="55" fill="#5C6699"/><ellipse cx="290" cy="135" rx="90" ry="50" fill="#6D78AD"/>'
           '<path d="M250 190 L215 270 L250 270 L220 350" stroke="#F7E7A6" stroke-width="12" fill="none" stroke-linejoin="round"/>'
           + SHIP.replace("#2A2140", "#141833"), None),
    "p4": ("#202C70",
           '<rect x="320" y="170" width="44" height="200" fill="#F4E9D0"/><rect x="320" y="210" width="44" height="22" fill="#E8553D"/>'
           '<rect x="320" y="270" width="44" height="22" fill="#E8553D"/><path d="M310 170 h64 l-12 -34 h-40 z" fill="#2A2140"/>'
           '<path d="M342 150 L470 110 L470 190 Z" fill="#F7E7A6" opacity="0.5"/>'
           '<path d="M60 340 L200 340 L180 372 L80 372 Z" fill="#2A2140"/><rect x="126" y="250" width="6" height="90" fill="#2A2140"/>'
           '<path d="M132 256 L180 330 L132 330 Z" fill="#F4E9D0"/>', (110, 120)),
    "p5": ("#26306E",
           '<rect x="150" y="230" width="180" height="140" fill="#E9DDBD"/><path d="M130 240 L240 150 L350 240 Z" fill="#B4432E"/>'
           '<rect x="220" y="300" width="40" height="70" fill="#2A2140"/><rect x="175" y="260" width="34" height="34" fill="#F7E7A6"/>'
           '<rect x="271" y="260" width="34" height="34" fill="#F7E7A6"/>', (370, 110)),
}

FOX = ('<path d="M150 170 L190 70 L230 160 Z" fill="#C85A1E"/><path d="M330 170 L290 70 L250 160 Z" fill="#C85A1E"/>'
       '<path d="M140 170 Q240 120 340 170 L300 290 Q240 340 180 290 Z" fill="#E07A2E"/>'
       '<path d="M160 205 Q205 215 240 300 Q195 300 175 270 Z" fill="#FFF3E2"/><path d="M320 205 Q275 215 240 300 Q285 300 305 270 Z" fill="#FFF3E2"/>'
       '<circle cx="205" cy="215" r="10" fill="#2A1A10"/><circle cx="275" cy="215" r="10" fill="#2A1A10"/>'
       '<circle cx="240" cy="290" r="13" fill="#2A1A10"/>'
       '<path d="M140 190 L120 175" stroke="#2A1A10" stroke-width="0"/>'
       '<path d="M176 108 l28 -26 h72 l28 26 z" fill="#5B4636"/><rect x="170" y="104" width="140" height="12" rx="6" fill="#3E2F24"/>')
GLASS = '<circle cx="{x}" cy="{y}" r="38" fill="#CFE7F2" fill-opacity="0.6" stroke="#3E2F24" stroke-width="10"/><path d="M{x2} {y2} l36 36" stroke="#3E2F24" stroke-width="14" stroke-linecap="round"/>'

fox_cases = {
    "f1": ("#F2B33D", '<path d="M90 400 h90 l-10 50 h-70 z" fill="#B4432E"/><ellipse cx="135" cy="395" rx="52" ry="30" fill="#7A4A24"/>'
           '<circle cx="120" cy="385" r="6" fill="#2A1A10"/><circle cx="148" cy="392" r="6" fill="#2A1A10"/>'),
    "f2": ("#8FC3E0", ''.join(f'<ellipse cx="{x}" cy="{y}" rx="13" ry="20" fill="#FFFFFF" transform="rotate(-20 {x} {y})"/>'
                              for x, y in [(60, 440), (110, 410), (160, 440), (210, 410), (260, 440), (310, 410)])),
    "f3": ("#7D6BB0", '<path d="M40 470 L130 360 L220 470 Z" fill="#4E3F80"/><rect x="105" y="410" width="50" height="50" fill="#F7E7A6"/>'
           '<path d="M130 410 v50 M105 435 h50" stroke="#4E3F80" stroke-width="6"/>'),
    "f4": ("#9ED38A", '<path d="M70 360 L120 300 L170 360 L120 430 Z" fill="#E8553D"/><path d="M120 300 v130 M70 360 h100" stroke="#FFFFFF" stroke-width="4"/>'
           '<path d="M120 430 q-20 20 0 40 q20 20 0 40" stroke="#3E2F24" stroke-width="4" fill="none"/>'),
}

music = {
    "m1": ("#F6C343", '<circle cx="240" cy="230" r="90" fill="#FFF3C4"/>' + ''.join(
        f'<rect x="234" y="70" width="12" height="50" rx="6" fill="#FFF3C4" transform="rotate({a} 240 230)"/>' for a in range(0, 360, 45))
        + '<circle cx="210" cy="215" r="10" fill="#6B4300"/><circle cx="270" cy="215" r="10" fill="#6B4300"/><path d="M200 255 Q240 290 280 255" stroke="#6B4300" stroke-width="8" fill="none" stroke-linecap="round"/>'),
    "m2": ("#E8553D", '<ellipse cx="200" cy="300" rx="110" ry="40" fill="#2A2140"/><rect x="300" y="290" width="120" height="20" rx="10" fill="#2A2140"/>'
           '<path d="M200 260 Q170 220 200 180 Q230 140 200 100" stroke="#FFFFFF" stroke-width="8" fill="none" stroke-linecap="round" opacity="0.8"/>'
           '<path d="M300 120 v80" stroke="#FFFFFF" stroke-width="10"/><ellipse cx="288" cy="205" rx="20" ry="15" fill="#FFFFFF"/><path d="M300 120 l40 14 v20 l-40 -14" fill="#FFFFFF"/>'),
    "m3": ("#7FC8BE", '<ellipse cx="250" cy="300" rx="130" ry="90" fill="#8A8FA8"/><circle cx="150" cy="230" r="80" fill="#9AA0BA"/>'
           '<ellipse cx="190" cy="220" rx="50" ry="65" fill="#B3B8CF"/><path d="M90 250 Q60 330 100 390" stroke="#9AA0BA" stroke-width="34" fill="none" stroke-linecap="round"/>'
           '<circle cx="130" cy="215" r="9" fill="#2A2140"/>' + ''.join(f'<rect x="{x}" y="360" width="34" height="70" rx="10" fill="#8A8FA8"/>' for x in (170, 230, 300, 350))),
    "m4": ("#4F79C9", '<ellipse cx="240" cy="330" rx="120" ry="34" fill="#E8553D"/><rect x="120" y="330" width="240" height="90" fill="#E8553D"/><ellipse cx="240" cy="420" rx="120" ry="34" fill="#B4432E"/>'
           '<ellipse cx="240" cy="330" rx="120" ry="34" fill="#FFF3E2"/><path d="M180 280 L230 320 M320 270 L260 318" stroke="#F2B33D" stroke-width="10" stroke-linecap="round"/>'
           + ''.join(f'<path d="M{x} {y} q8 14 0 22 q-8 -8 0 -22z" fill="#CFE7F2"/>' for x, y in [(80, 80), (160, 140), (300, 90), (390, 160), (230, 60), (420, 60)])),
    "m5": ("#1E2A6B", stars(40, 480, 480, 7, 3) + '<path d="M240 120 L268 200 L352 200 L284 250 L310 332 L240 282 L170 332 L196 250 L128 200 L212 200 Z" fill="#F7E7A6"/>'
           '<circle cx="215" cy="230" r="8" fill="#6B4300"/><circle cx="265" cy="230" r="8" fill="#6B4300"/><path d="M220 262 Q240 276 260 262" stroke="#6B4300" stroke-width="6" fill="none" stroke-linecap="round"/>'),
}

def bubbles(seed, n=7):
    import random
    rnd = random.Random(seed)
    return "".join(
        f'<circle cx="{rnd.uniform(40, 600):.0f}" cy="{rnd.uniform(30, 220):.0f}" r="{rnd.uniform(10, 34):.0f}" fill="#FFFFFF" fill-opacity="0.35" stroke="#FFFFFF" stroke-width="3"/>'
        for _ in range(n))

GRASS = '<path d="M0 270 Q160 240 320 270 T640 270 V360 H0 Z" fill="#4E9A48"/><path d="M0 300 Q160 285 320 300 T640 300 V360 H0 Z" fill="#3F8A3B"/>'
cartoons = {
    "c1": ("#8FD0F0", GRASS + '<ellipse cx="330" cy="290" rx="120" ry="24" fill="#5BB3E3"/>'
           + ''.join(f'<circle cx="{x}" cy="{y}" r="{r}" fill="#CFE7F2"/>' for x, y, r in [(290, 230, 14), (330, 200, 18), (370, 235, 12), (350, 170, 10), (310, 175, 9)])
           + bubbles(1) + '<circle cx="540" cy="70" r="40" fill="#F7E7A6"/>'),
    "c2": ("#B6E0F5", GRASS + '<circle cx="260" cy="250" r="40" fill="#C88B4A"/><path d="M260 250 m-24 0 a24 24 0 1 0 24 -24" stroke="#8A5A2B" stroke-width="7" fill="none"/>'
           '<path d="M210 285 h120 q20 0 20 -20" stroke="#B9C47A" stroke-width="24" stroke-linecap="round" fill="none"/>'
           '<circle cx="420" cy="245" r="30" fill="#A06FC8"/><path d="M380 280 h80" stroke="#C6D59A" stroke-width="18" stroke-linecap="round"/>'
           '<path d="M520 200 v90" stroke="#FFFFFF" stroke-width="6"/><path d="M520 200 h40 v14 h-40 v14 h40 v14 h-40" fill="#2A2140"/>' + bubbles(2, 4)),
    "c3": ("#1E2A6B", stars(50, 640, 360, 3, 2.5) + GRASS.replace("#4E9A48", "#244A2A").replace("#3F8A3B", "#1C3B22")
           + ''.join(f'<circle cx="{x}" cy="{y}" r="7" fill="#F7E7A6"/><circle cx="{x}" cy="{y}" r="18" fill="#F7E7A6" opacity="0.25"/>' for x, y in [(120, 200), (200, 160), (320, 220), (420, 150), (520, 210), (260, 120)])
           + '<circle cx="540" cy="70" r="34" fill="#F7E7A6"/><circle cx="556" cy="60" r="30" fill="#1E2A6B"/>'),
    "c4": ("#9BD3EE", GRASS + '<rect x="380" y="90" width="40" height="200" fill="#7A4A24"/><circle cx="400" cy="80" r="110" fill="#4E9A48"/><circle cx="330" cy="110" r="60" fill="#5FAE57"/>'
           '<rect x="340" y="120" width="120" height="70" fill="#C88B4A"/><path d="M330 125 L400 80 L470 125 Z" fill="#B4432E"/><rect x="380" y="145" width="30" height="45" fill="#3E2F24"/>'
           '<path d="M300 190 L340 290 M320 190 L360 290" stroke="#7A4A24" stroke-width="5"/>' + ''.join(f'<path d="M{310+2*i} {200+20*i} h22" stroke="#7A4A24" stroke-width="4"/>' for i in range(5)) + bubbles(4, 3)),
}

jobs = []
for k, (bg, body, moonpos) in moon_eps.items():
    extra = ""
    if moonpos:
        x, y = moonpos
        extra = MOON.format(x=x, y=y, x2=x + 30, y2=y - 18, bg=bg)
    num = k[1]
    jobs.append((k, 480, 480, svg(480, 480, stars(30, 480, 480, int(num)) + extra + body + SEA + badge(num, 480, "#4552C7"), bg)))
for k, (bg, obj) in fox_cases.items():
    num = k[1]
    gx, gy = (360, 380) if k != "f4" else (380, 360)
    jobs.append((k, 480, 480, svg(480, 480, FOX + obj + GLASS.format(x=gx, y=gy, x2=gx + 26, y2=gy + 26) + badge(num, 480, "#B4432E"), bg)))
for k, (bg, body) in music.items():
    jobs.append((k, 480, 480, svg(480, 480, body, bg)))
for k, (bg, body) in cartoons.items():
    jobs.append((k, 640, 360, svg(640, 360, body, bg)))

# group covers: series art without episode badge
jobs.append(("g_moon", 480, 480, svg(480, 480, stars(36, 480, 480, 11) + MOON.format(x=340, y=120, x2=370, y2=102, bg="#1E2A6B") + SHIP + SEA, "#1E2A6B")))
jobs.append(("g_fox", 480, 480, svg(480, 480, FOX + GLASS.format(x=360, y=380, x2=386, y2=406), "#F2B33D")))

for name, w, h, s in jobs:
    p = f"{OUT}/{name}.svg"
    open(p, "w").write(s)
    subprocess.run(["rsvg-convert", "-w", str(w), "-h", str(h), p, "-o", f"{OUT}/{name}.png"], check=True)
    Image.open(f"{OUT}/{name}.png").convert("RGB").save(f"{OUT}/{name}.webp", "WEBP", quality=82)
    os.remove(p); os.remove(f"{OUT}/{name}.png")
print(len(jobs), "images")
