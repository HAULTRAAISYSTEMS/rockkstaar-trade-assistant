"""Builds the Command Center scene layers in static/img/:
scene_sky.webp (stars, nebula, horizon bloom, floor base), scene_far.webp and
scene_near.webp (transparent mountain ranges with glowing ridges).
The neon floor grid and light trails are drawn live in the page.
Usage: python3 build_command_scene.py static/img"""
import math, sys, numpy as np
from PIL import Image, ImageDraw, ImageFilter
W, H = 2400, 1000; HZ = int(H * 0.64)
rng = np.random.default_rng(11)
def blur(a, r): return np.asarray(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(r)), np.float32)
def blurf(a, r):  # float [0..1]
    return blur(a * 255, r) / 255
def noise2(h, w, scale, octaves=6, seed=0):
    r = np.random.default_rng(seed); out = np.zeros((h, w), np.float32); amp = 1; tot = 0
    for o in range(octaves):
        gh = max(2, int(h / scale * 2**o)); gw = max(2, int(w / scale * 2**o))
        n = Image.fromarray((r.random((gh, gw)) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
        out += np.asarray(n, np.float32) / 255 * amp; tot += amp; amp *= 0.5
    return out / tot
yy = np.arange(H)[:, None].astype(np.float32); xx = np.arange(W)[None, :].astype(np.float32)
img = np.zeros((H, W, 3), np.float32)

# Sky gradient + nebula
t = np.clip(yy / HZ, 0, 1)
img += np.stack([3 + 12 * t**2, 8 + 30 * t**2, 22 + 70 * t**2], -1) * np.ones((1, W, 1))
neb = noise2(H, W, 260, 6, 3); neb2 = noise2(H, W, 180, 5, 5)
skym = (yy < HZ).astype(np.float32)
wisp = np.clip((neb - 0.45) * 3, 0, 1) * skym * np.clip(1 - yy / HZ, 0.2, 1)
purple = np.clip((neb2 - 0.5) * 3, 0, 1) * skym
img += wisp[..., None] * np.array([20, 50, 110]) + purple[..., None] * np.array([55, 20, 95])
# Horizon bloom (centre brightest, where the globe sits)
dx = (xx - W / 2) / (W * 0.45); dy = (yy - HZ) / (H * 0.22)
bloom = np.exp(-(dx**2 + dy**2) * 1.4)
img += bloom[..., None] * np.array([20, 95, 150])

# Stars
for _ in range(2600):
    x = rng.integers(0, W); y = rng.integers(0, HZ - 20)
    b = rng.random() ** 3 * 255
    c = np.array([0.8 + 0.2 * rng.random(), 0.85 + 0.15 * rng.random(), 1.0])
    img[y, x] += c * b
star_glow = np.zeros((H, W), np.float32)
for _ in range(90):
    x = rng.integers(0, W); y = rng.integers(0, HZ - 60); star_glow[y, x] = 255
sg = blur(star_glow, 2.2) * 2.2 + blur(star_glow, 6) * 1.2
img += (sg / 255)[..., None] * np.array([160, 200, 255])

# Floor: dark reflective plane with a neon perspective grid and light trails.
fl = yy > HZ
dfl = np.clip((yy - HZ) / (H - HZ), 0, 1)
floor = np.stack([2 + 4 * dfl, 5 + 10 * dfl, 14 + 22 * dfl], -1) * np.ones((1, W, 1))
floor += bloom[..., None] * np.array([10, 60, 100]) * 0.8
img = np.where(fl[..., None], floor, img)
# Horizon line
hl = np.exp(-((yy - HZ) / 2.5) ** 2) * np.exp(-((xx - W / 2) / (W * 0.4)) ** 2)
img += hl[..., None] * np.array([120, 230, 255])
LAYERS = []
# Mountains: two layers, high at the sides, a valley in the middle.
def ridge(seed, base, amp, rough):
    r = np.random.default_rng(seed); n = 2**11 + 1; h = np.zeros(n); h[0] = r.random(); h[-1] = r.random()
    step = n - 1; a = 1.0
    while step > 1:
        half = step // 2
        for i in range(half, n, step):
            h[i] = (h[i - half] + h[i + half]) / 2 + (r.random() - 0.5) * a
        a *= rough; step = half
    h = np.interp(np.linspace(0, n - 1, W), np.arange(n), h)
    h = (h - h.min()) / (h.max() - h.min())
    side = np.abs(np.linspace(-1, 1, W))
    valley = np.clip((side - 0.22) / 0.5, 0, 1) ** 1.5
    return HZ - (base + amp * h) * (0.12 + 0.88 * valley)
hill = noise2(H, W, 60, 6, 21)
gy, gx = np.gradient(hill * 40)
shade = np.clip(0.5 + (-gx * 0.7 - gy * 0.7) * 1.6, 0, 1)
layers = [  # (seed, base, amp, rough, body colour, rim colour, haze)
    (4, 60, 380, 0.62, np.array([38, 30, 92]), np.array([150, 110, 255]), 0.55),
    (9, 30, 260, 0.64, np.array([10, 14, 40]), np.array([90, 200, 255]), 0.0),
]
for seed, base, amp, rough, body, rim, haze in layers:
    top = ridge(seed, base, amp, rough)
    m = (yy >= top[None, :]) & (yy <= HZ + 2)
    depth = np.clip((yy - top[None, :]) / 140, 0, 1)
    col = body[None, None, :] * (0.55 + 0.9 * shade[..., None]) * (1.25 - 0.6 * depth[..., None])
    # snow / light catching the upper faces
    snow = np.clip(1 - depth * 4, 0, 1) * np.clip(shade - 0.45, 0, 1) * 2
    col += snow[..., None] * rim[None, None, :] * 0.55
    col = col * (1 - haze * 0.35) + np.array([30, 50, 110]) * haze * 0.35
    rgb = np.where(m[..., None], col, 0).astype(np.float32)
    alpha = m.astype(np.float32)
    # glowing rim line along the ridge
    edge = np.zeros((H, W), np.float32)
    ti = np.clip(top.astype(int), 0, H - 1); edge[ti, np.arange(W)] = 255
    eg = blur(edge, 1.2) * 1.6 + blur(edge, 5) * 1.4 + blur(edge, 16) * 0.9
    glow = (eg / 255) * (0.6 if haze else 0.9)
    gcol = rim[None, None, :] * np.ones((H, W, 1))
    a2 = np.clip(glow * 1.2, 0, 1) * (1 - alpha)
    rgb = rgb + (eg / 255)[..., None] * rim * (0.6 if haze else 0.9) * alpha[..., None]
    rgb = np.where(alpha[..., None] > 0, rgb, gcol)
    alpha = np.clip(alpha + a2, 0, 1)
    # crop to the band that holds mountains and save RGBA
    out = np.dstack([np.clip(rgb, 0, 255), alpha * 255]).astype(np.uint8)
    LAYERS.append(out)


fade = np.clip((yy - H * 0.78) / (H * 0.22), 0, 1)[..., None]
img = img * (1 - fade) + np.array([2, 8, 19]) * fade
vig = np.exp(-(((xx - W * 0.12) / (W * 0.22)) ** 2 + ((yy - H * 0.35) / (H * 0.35)) ** 2))
img = img * (1 - 0.35 * vig[..., None])
outdir = sys.argv[1] if len(sys.argv) > 1 else '.'
Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(outdir + '/scene_sky.webp', quality=80, method=6)
for name, L in zip(('scene_far', 'scene_near'), LAYERS):
    Image.fromarray(L, 'RGBA').save(outdir + '/' + name + '.webp', quality=82, method=6)
