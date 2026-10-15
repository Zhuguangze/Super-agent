"""PCA 求文字精确角度 -> 转正条带 + 标尺。"""
import math
import sys

sys.path.append(r"c:\users\administrator\appdata\roaming\python\python312\site-packages")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

BASE = r"C:\Users\Administrator\Desktop\selfProject\Super Agent\.scratch\hero-video\frames"
img = cv2.imread(BASE + r"\frame_00.png")
gray = img.mean(axis=2)

# 文字像素:亮色,且在轴线附近(轴线上 P0->P1,法向 -20..+26)
P0 = np.array([351.7, 314.3])
P1 = np.array([451.7, 368.3])
axis = P1 - P0
axis_len = float(np.linalg.norm(axis))
u = axis / axis_len
n = np.array([-u[1], u[0]])

pts = []
for t in np.linspace(-0.05, 1.05, 60):
    p = P0 + u * (t * axis_len)
    for s in np.arange(-20, 26.5, 1.0):
        q = p + n * s
        xi, yi = int(round(q[0])), int(round(q[1]))
        if gray[yi, xi] > 170:
            pts.append((q[0], q[1]))
pts = np.array(pts)
print("text px:", len(pts))
mean = pts.mean(axis=0)
cov = np.cov((pts - mean).T)
evals, evecs = np.linalg.eigh(cov)
v = evecs[:, -1]  # 主方向
if v[0] < 0:
    v = -v
theta = math.degrees(math.atan2(v[1], v[0]))
print(f"PCA angle = {theta:.2f} deg (y-down), principal/secondary eval ratio = {evals[-1] / evals[0]:.1f}")

center = (float(mean[0]), float(mean[1]))
M = cv2.getRotationMatrix2D(center, theta, 1.0)  # OpenCV: 正角使 +theta 方向内容转平(经验验证)
rot = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_LANCZOS4)

x0, x1 = int(center[0] - 150), int(center[0] + 150)
y0, y1 = int(center[1] - 45), int(center[1] + 45)
strip = rot[y0:y1, x0:x1]
strip = cv2.resize(strip, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
for ox in range(-150, 151, 20):
    px = (ox + 150) * 4
    cv2.line(strip, (px, 0), (px, strip.shape[0]), (0, 255, 255) if ox % 100 == 0 else (60, 200, 200), 1)
    if ox % 40 == 0:
        cv2.putText(strip, str(ox), (px + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
for oy in range(-45, 46, 15):
    py = (oy + 45) * 4
    cv2.line(strip, (0, py), (strip.shape[1], py), (0, 255, 255), 1)
    cv2.putText(strip, str(oy), (2, py - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
cv2.imwrite(BASE + r"\strip_ruler.png", strip)
print("center", center)
