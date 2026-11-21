"""Builds static/img/earth.webp (top: surface colour, bottom: city lights).

Inputs: Natural Earth land-50m (npm world-atlas, via topojson-client feature())
as land.json, and [lat, lon, population] rows for cities >= 40k people
(npm all-the-cities) as cities.json.  Usage: python3 build_earth_texture.py land.json cities.json
"""
import json, math, sys, numpy as np
from PIL import Image, ImageDraw, ImageFilter
W, H = 2048, 1024; S = 2  # supersample the land mask
land = json.load(open(sys.argv[1] if len(sys.argv) > 1 else 'land.json'))
m = Image.new('L', (W*S, H*S), 0); d = ImageDraw.Draw(m)
def px(lon, lat): return ((lon + 180) / 360 * W*S, (90 - lat) / 180 * H*S)
for f in land['features']:
    g = f['geometry']; polys = g['coordinates'] if g['type'] == 'MultiPolygon' else [g['coordinates']]
    for poly in polys:
        for i, ring in enumerate(poly):
            # Unwrap longitudes so rings that cross the date line stay continuous.
            pts = [list(ring[0])]
            for lo, la in ring[1:]:
                prev = pts[-1][0]
                while lo - prev > 180: lo -= 360
                while lo - prev < -180: lo += 360
                pts.append([lo, la])
            span = pts[-1][0] - pts[0][0]
            if abs(span) > 180:  # ring circles a pole: close it through the pole
                pole = -90 if sum(p[1] for p in pts) < 0 else 90
                pts += [[pts[-1][0], pole], [pts[0][0], pole]]
            for off in (-360, 0, 360):
                d.polygon([px(lo + off, la) for lo, la in pts], fill=255 if i == 0 else 0)
mask = np.asarray(m.resize((W, H), Image.LANCZOS), dtype=np.float32) / 255.0

rng = np.random.default_rng(7)
def noise(scale, octaves=5):
    out = np.zeros((H, W), np.float32); amp = 1.0; tot = 0
    for o in range(octaves):
        h = max(2, int(H / scale * 2**o)); w = h * 2
        n = Image.fromarray((rng.random((h, w)) * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
        out += np.asarray(n, np.float32) / 255 * amp; tot += amp; amp *= 0.55
    return out / tot
n1 = noise(64); n2 = noise(16, 4)
lat = np.linspace(90, -90, H)[:, None] * np.ones((1, W))
alat = np.abs(lat)

# Ocean: deep navy, brighter shelf near coasts.
coast = np.asarray(Image.fromarray((mask*255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(10)), np.float32) / 255
coast2 = np.asarray(Image.fromarray((mask*255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3)), np.float32) / 255
ocean = np.stack([6 + 10*coast + 4*n2, 22 + 40*coast + 10*n2, 52 + 70*coast + 14*n2], -1)
ocean += np.stack([0*coast2, 40*coast2, 50*coast2], -1) * (1 - mask)[..., None]

# Land: deep greens with terrain variation; arid belts a touch olive; ice near poles.
t = np.clip(n1*1.4 - 0.2 + (n2-0.5)*0.5, 0, 1)
arid = np.clip(1 - np.abs(alat - 24) / 12, 0, 1) * np.clip(n1*1.6 - 0.4, 0, 1)
landc = np.stack([10 + 30*t + 40*arid, 58 + 80*t + 30*arid, 40 + 40*t - 10*arid], -1)
ice = np.clip((alat - 62) / 10, 0, 1)
landc = landc*(1-ice[..., None]) + np.stack([150+40*n2, 200+30*n2, 215+30*n2], -1)*ice[..., None]
surf = ocean*(1-mask[..., None]) + landc*mask[..., None]
# Bright coastline rim on land.
edge = np.clip(coast2 * (1 - coast2) * 4, 0, 1) * mask
surf += np.stack([20*edge, 90*edge, 70*edge], -1)
surf = np.clip(surf, 0, 255).astype(np.uint8)

# City lights from real city populations.
cities = json.load(open(sys.argv[2] if len(sys.argv) > 2 else 'cities.json'))
L = np.zeros((H, W), np.float32)
for la, lo, pop in cities:
    x = int((lo + 180) / 360 * W) % W; y = min(H-1, int((90 - la) / 180 * H))
    L[y, x] += math.log10(pop) - 4.3
    # sprinkle suburbs around bigger cities
    k = int(min(40, pop / 150000))
    if k:
        dx = rng.normal(0, 1 + math.log10(pop) - 4.5, k); dy = rng.normal(0, 1 + math.log10(pop) - 4.5, k)
        for a, b in zip(dx, dy):
            xx = int(x + a) % W; yy = min(H-1, max(0, int(y + b))); L[yy, xx] += 0.35
core = np.clip(L, 0, 3) / 3
glow = np.asarray(Image.fromarray((core*255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2.2)), np.float32)/255
glow2 = np.asarray(Image.fromarray((core*255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(7)), np.float32)/255
lights = np.clip(core*0.9 + glow*2.2 + glow2*1.6, 0, 1) * np.clip(mask + coast2, 0, 1)
lights = (np.power(lights, 0.8) * 255).astype(np.uint8)

out = np.zeros((H*2, W, 3), np.uint8); out[:H] = surf; out[H:] = np.stack([lights]*3, -1)
img = Image.fromarray(out)
img.save('earth.webp', quality=82, method=6)
img.resize((W//2, H)).save('earth_1024.webp', quality=82, method=6)
Image.fromarray(surf).save('preview_surf.jpg', quality=80)
Image.fromarray(lights).save('preview_lights.jpg', quality=80)
