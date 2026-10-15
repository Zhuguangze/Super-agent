"""放大 Data Asset Management 标签区域并采样底色,用于设计 CSS 覆盖块。"""
import sys

sys.path.append(r"c:\users\administrator\appdata\roaming\python\python312\site-packages")

import cv2  # noqa: E402

BASE = r"C:\Users\Administrator\Desktop\selfProject\Super Agent\.scratch\hero-video\frames"
img = cv2.imread(BASE + r"\frame_00.png")

# 标签大致区域裁剪后放大 3 倍
x0, y0, x1, y1 = 320, 285, 480, 400
crop = img[y0:y1, x0:x1]
zoom = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
cv2.imwrite(BASE + r"\label_zoom.png", zoom)

# 沿标签带采样底色(文字上下两条线)
for y in (300, 310, 320, 330, 340, 350, 360, 370, 380):
    b, g, r = img[y, 400]
    print(f"y={y} @x400 -> #{r:02X}{g:02X}{b:02X}")
for x in (340, 360, 380, 420, 450, 470):
    b, g, r = img[345, x]
    print(f"x={x} @y345 -> #{r:02X}{g:02X}{b:02X}")
