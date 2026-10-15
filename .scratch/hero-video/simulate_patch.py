"""正确几何下的覆盖模拟:浅色带 + 深色新文字。全部在转正空间操作后逆变换回贴。"""
import math
import sys

sys.path.append(r"c:\users\administrator\appdata\roaming\python\python312\site-packages")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

BASE = r"C:\Users\Administrator\Desktop\selfProject\Super Agent\.scratch\hero-video\frames"
img = cv2.imread(BASE + r"\frame_00.png")
H, W = img.shape[:2]

theta = 26.4
center = (404.2, 341.3)
M = cv2.getRotationMatrix2D(center, theta, 1.0)
rot = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LANCZOS4)
cx, cy = center

# ── 在转正空间采样颜色(uint8 溢出坑:先转 int)──
def sample(dx, dy):
    b, g, r = (int(v) for v in rot[int(cy + dy), int(cx + dx)])
    return "#%02X%02X%02X" % (r, g, b)

print("band light  L/M/R:", sample(-70, 2), sample(0, 2), sample(70, 6))
# 文字亮色:带内高亮像素均值
vals = []
for dx in range(-75, 80, 2):
    for dy in np.arange(-8, 9, 1.0):
        b, g, r = (int(v) for v in rot[int(cy + dy), int(cx + dx)])
        if (b + g + r) / 3 > 195:
            vals.append((b, g, r))
tb, tg, tr = np.mean(vals, axis=0)
text_hex = "#%02X%02X%02X" % (int(tr), int(tg), int(tb))
print("text light:", text_hex, f"(n={len(vals)})")

# ── 补丁矩形(转正空间):恰好盖住浅带含文字段,不压上下深色描边 ──
PW, PH = 164, 28  # 轴向 x' -82..+82,法向 y' -10..+18
py0, py1 = -10, 18
patch_tl = (cx - PW / 2, cy + py0)
patch_br = (cx + PW / 2, cy + py1)

# 渐变底(带内实测色:左 #848BC3 → 右 #B3B6E4;保持 BGR 序,与 OpenCV 一致)
grad = np.zeros((PH, PW, 3), np.float32)
c_l = np.array(rot[int(cy + 2), int(cx - 70)], np.float32)
c_r = np.array(rot[int(cy + 6), int(cx + 70)], np.float32)
for i in range(PW):
    t = i / (PW - 1)
    grad[:, i] = c_l * (1 - t) + c_r * t
patch = grad.astype(np.uint8)

pil = Image.fromarray(cv2.cvtColor(patch, cv2.COLOR_BGR2RGB))
font = None
for fp in (r"C:\Windows\Fonts\segoeuisb.ttf", r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"):
    try:
        font = ImageFont.truetype(fp, 21)
        break
    except OSError:
        continue
print("font:", font.path if font else "none")
text = "Session Memory Assets"
draw = ImageDraw.Draw(pil)
bbox = draw.textbbox((0, 0), text, font=font)
tw = bbox[2] - bbox[0]
tx = (PW - tw) / 2 - bbox[0]
ty = (PH - (bbox[3] - bbox[1])) / 2 - bbox[1]
draw.text((tx, ty + 1), text, font=font, fill=(60, 62, 100, 140))
draw.text((tx, ty), text, font=font, fill=(int(tr), int(tg), int(tb)))
patch = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

# 把补丁贴进转正图(之前漏了这一步)
rot[int(cy + py0) : int(cy + py1), int(cx - PW // 2) : int(cx + PW // 2)] = patch

# ── 贴回原图:补丁四角逆变换 → 多边形掩码 ──
Minv = cv2.invertAffineTransform(M)
corners_rot = np.array([[patch_tl[0], patch_tl[1]], [patch_br[0], patch_tl[1]], [patch_br[0], patch_br[1]], [patch_tl[0], patch_br[1]]], np.float32)
corners_orig = cv2.transform(corners_rot[None], Minv)[0]
# 逆变换整幅旋转图,只在多边形内取值
back = cv2.warpAffine(rot, Minv, (W, H), flags=cv2.INTER_LANCZOS4)
mask = np.zeros((H, W), np.uint8)
cv2.fillPoly(mask, [corners_orig.astype(np.int32)], 255)
# 边缘羽化
mask = cv2.GaussianBlur(mask, (0, 0), 2.2)
comp = np.where(mask[..., None] > 0, back, img)

cv2.imwrite(BASE + r"\patch_sim.png", comp)
zx, zy = int(cx) - 110, int(cy) - 70
zoom = cv2.resize(comp[zy : zy + 140, zx : zx + 220], None, fx=3, fy=3, interpolation=cv2.INTER_LANCZOS4)
cv2.imwrite(BASE + r"\patch_sim_zoom.png", zoom)

# ── CSS 参数 ──
print(f"CSS: left={cx / W * 100:.2f}% top={cy / H * 100:.2f}%")
print(f"CSS: w={PW / W * 100:.2f}% h={PH / H * 100:.2f}% rotate={theta}deg")
print(f"CSS: font={21 / W * 100:.2f}cqw color={text_hex} band=linear-gradient(90deg,{sample(-70, 2)},{sample(70, 6)})")
