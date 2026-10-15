"""在旋转坐标系里精确测量原文字的长度与字高(消除 28.4° 斜切带来的估计误差)。"""
import math
import sys

sys.path.append(r"c:\users\administrator\appdata\roaming\python\python312\site-packages")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

BASE = r"C:\Users\Administrator\Desktop\selfProject\Super Agent\.scratch\hero-video\frames"
img = np.asarray(Image.open(BASE + r"\frame_00.png").convert("L"), dtype=np.float32)
H, W = img.shape

ang = math.radians(28.4)
ca, sa = math.cos(ang), math.sin(ang)
cx, cy = 400.0, 345.0  # 区域中心(近似)

ys, xs = np.mgrid[290:400, 335:485]
vals = img[290:400, 335:485]
bright = vals > 170

# 旋转到文字坐标系
xr = (xs - cx) * ca + (ys - cy) * sa
yr = -(xs - cx) * sa + (ys - cy) * ca

bx, by = xr[bright], yr[bright]
print(f"bright px = {bright.sum()}")
print(f"text length (x') = {bx.max() - bx.min():.1f}px  [{bx.min():.1f} .. {bx.max():.1f}]")
print(f"cap height  (y') = {by.max() - by.min():.1f}px  [{by.min():.1f} .. {by.max():.1f}]")

# y' 直方图看文字主体范围(排除孤立亮点)
hist, edges = np.histogram(by, bins=24)
for h, e in zip(hist, edges):
    print(f"  y' {e:6.1f} {'#' * int(h / 3)}")
