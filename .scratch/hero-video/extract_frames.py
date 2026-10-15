"""从 hero-loop.mp4 按时间百分比抽帧，用于确认标签位置与是否随动画移动。"""
import sys

sys.path.append(r"c:\users\administrator\appdata\roaming\python\python312\site-packages")

import cv2  # noqa: E402

VIDEO = r"C:\Users\Administrator\Desktop\selfProject\Super Agent\frontend\public\hero-loop.mp4"
OUT_DIR = r"C:\Users\Administrator\Desktop\selfProject\Super Agent\.scratch\hero-video\frames"

import os  # noqa: E402

os.makedirs(OUT_DIR, exist_ok=True)

cap = cv2.VideoCapture(VIDEO)
fps = cap.get(cv2.CAP_PROP_FPS)
frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
print(f"fps={fps:.2f} frames={frames} duration={frames / fps:.2f}s size={w}x{h}")

for pct in (0, 25, 50, 75, 99):
    idx = min(int(frames * pct / 100), frames - 1)
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, img = cap.read()
    if ok:
        path = os.path.join(OUT_DIR, f"frame_{pct:02d}.png")
        cv2.imwrite(path, img)
        print("saved", path)
cap.release()
