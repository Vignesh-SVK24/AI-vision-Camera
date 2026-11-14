"""Camera diagnostic tool testing indexes 0-3 with DSHOW, MSMF, and default backends."""

import sys
import time
import cv2

BACKENDS = [
    ("DSHOW", cv2.CAP_DSHOW),
    ("MSMF", cv2.CAP_MSMF),
    ("DEFAULT", cv2.CAP_ANY),
]

def test_camera(index: int, backend_name: str, backend_val: int):
    print(f"\n--- Testing Index {index} with Backend {backend_name} ---")
    try:
        if backend_val == cv2.CAP_ANY:
            cap = cv2.VideoCapture(index)
        else:
            cap = cv2.VideoCapture(index, backend_val)
    except Exception as exc:
        print(f"Exception initializing VideoCapture: {exc}")
        return False, False, (0, 0)

    opened = cap.isOpened()
    print(f"opened: {opened}")
    if not opened:
        cap.release()
        return False, False, (0, 0)

    # Allow sensor to warm up
    time.sleep(0.5)

    frame_ok = False
    ret = False
    frame = None
    for attempt in range(5):
        ret, frame = cap.read()
        if ret and frame is not None and frame.size > 0:
            frame_ok = True
            break
        time.sleep(0.2)

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) if opened else 0
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) if opened else 0
    actual_res = (frame.shape[1], frame.shape[0]) if (frame_ok and frame is not None) else (width, height)

    print(f"frame_ok: {frame_ok}")
    print(f"resolution: {actual_res[0]}x{actual_res[1]}")

    cap.release()
    return opened, frame_ok, actual_res

def main():
    print(f"OpenCV version: {cv2.__version__}")
    results = []
    for idx in range(4):
        for b_name, b_val in BACKENDS:
            opened, frame_ok, res = test_camera(idx, b_name, b_val)
            results.append({
                "index": idx,
                "backend": b_name,
                "opened": opened,
                "frame_ok": frame_ok,
                "resolution": f"{res[0]}x{res[1]}" if frame_ok else "N/A"
            })

    print("\n" + "=" * 60)
    print(f"{'Index':<7} | {'Backend':<10} | {'Opened':<8} | {'Frame OK':<10} | {'Resolution':<12}")
    print("-" * 60)
    for r in results:
        print(f"{r['index']:<7} | {r['backend']:<10} | {str(r['opened']):<8} | {str(r['frame_ok']):<10} | {r['resolution']:<12}")
    print("=" * 60)

if __name__ == "__main__":
    main()
