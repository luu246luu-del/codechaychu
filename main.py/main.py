"""
main.py
Real-time Hand-Gesture Particle System (Numpy Vectorized) - v3 (Visual Overhaul)
- Two windows: Camera (hand skeleton) + Particles (pure black canvas, no HUD)
- Crisp, separated, glowing pink/coral particles
- Rotating parametric heart -> "TIEN" text sequence on 5-finger gesture
- Zero unnecessary per-frame overhead for lowest possible latency
"""

import cv2
import numpy as np
import mediapipe as mp
import time
import os
from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------
WIDTH, HEIGHT     = 1280, 720
NUM_PARTICLES     = 6000
LERP_SPEED        = 0.22          # higher = snappier response to gesture change
JITTER_STD        = 0.35
TRAIL_DECAY       = 0.80          # lower = shorter trails, crisper separated dots
SPHERE_RADIUS     = 230.0
HEART_SCALE       = 15.5
ROTATION_SPEED_SPHERE = 0.014
ROTATION_SPEED_HEART  = 0.05
HEART_HOLD_SECONDS    = 1.6

# Vivid pink / coral (BGR) - matches the reference look
PARTICLE_COLOR_BASE  = np.array([120,  70, 255], dtype=np.float32)  # hot pink
PARTICLE_COLOR_HEART = np.array([ 90,  40, 255], dtype=np.float32)  # deeper coral-red
PARTICLE_COLOR_SPARKLE = np.array([255, 245, 255], dtype=np.float32)

GLOW_SIGMA_CORE   = 0.8           # tight, keeps particles looking separate/sharp
GLOW_SIGMA_HALO   = 3.2           # soft halo, kept subtle so dots don't merge into a blob
HALO_WEIGHT       = 0.35
SPARKLE_RATIO     = 0.05

# MediaPipe runs on a downscaled copy of the frame -> much faster / lower latency
DETECT_WIDTH, DETECT_HEIGHT = 480, 270

GESTURE_LETTER_TEXT = {
    1: "Nàng",
    2: "Tựa Như Cơn Gió",
    3: "Thoảng",
    4: "Qua Chút Đời Ta",
    5: "Khẽ Bảo",
    6: "Em Giống Ma",
    7: "Ơ Kìaaaaaaa",
    8: "Ma Em Quế ",
}# --------------------------------------------------------------------------
# FONT LOADING (must support Vietnamese diacritics)
# --------------------------------------------------------------------------
FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/tahomabd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]

def resolve_font_path():
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None

FONT_PATH = resolve_font_path()
_FONT_CACHE = {}

def load_font(size):
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    if FONT_PATH is not None:
        try:
            f = ImageFont.truetype(FONT_PATH, size)
            _FONT_CACHE[size] = f
            return f
        except Exception:
            pass
    f = ImageFont.load_default()
    _FONT_CACHE[size] = f
    return f

# --------------------------------------------------------------------------
# TARGET GENERATORS (cached point clouds, regenerated only on state change)
# --------------------------------------------------------------------------
_LETTER_CACHE = {}

def generate_letter_targets(text, num_particles, width, height):
    if text in _LETTER_CACHE:
        xs_all, ys_all = _LETTER_CACHE[text]
    else:
        font_size = int(height * 0.72) if len(text) == 1 else int(height * 0.45)
        font = load_font(font_size)

        img = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        while text_w > width * 0.9 and font_size > 20:
            font_size -= 6
            font = load_font(font_size)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

        x = (width - text_w) // 2 - bbox[0]
        y = (height - text_h) // 2 - bbox[1]
        draw.text((x, y), text, fill=255, font=font)

        arr = np.array(img)
        ys_all, xs_all = np.where(arr > 100)
        if len(xs_all) == 0:
            xs_all = np.array([width // 2])
            ys_all = np.array([height // 2])
        _LETTER_CACHE[text] = (xs_all, ys_all)

    replace = len(xs_all) < num_particles
    idx = np.random.choice(len(xs_all), size=num_particles, replace=replace)
    sampled = np.stack([xs_all[idx].astype(np.float32), ys_all[idx].astype(np.float32)], axis=1)
    sampled += np.random.normal(0, 0.5, size=sampled.shape)
    return sampled


def generate_sphere_targets(num_particles, width, height, rotation_angle, radius=SPHERE_RADIUS):
    idx = np.arange(0, num_particles, dtype=np.float64) + 0.5
    phi = np.arccos(1.0 - 2.0 * idx / num_particles)
    golden_angle = np.pi * (3.0 - np.sqrt(5.0))
    theta = golden_angle * idx

    x = np.sin(phi) * np.cos(theta + rotation_angle)
    y = np.sin(phi) * np.sin(theta + rotation_angle)
    z = np.cos(phi)

    depth_scale = 1.0 + z * 0.28
    screen_x = width / 2 + x * radius * depth_scale
    screen_y = height / 2 + y * radius * depth_scale
    return np.stack([screen_x, screen_y], axis=1)


def generate_heart_targets(num_particles, width, height, rotation_angle, scale=HEART_SCALE):
    """Classic parametric heart curve, edge-weighted (dense outline, lighter fill),
    rotated around the vertical axis for a spinning-card 3D illusion. Fully vectorized."""
    t = np.random.uniform(0, 2 * np.pi, num_particles)

    x = 16 * np.sin(t) ** 3
    y = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)

    # radial factor skewed toward 1.0 -> most particles hug the outline, some fill inside
    r = np.random.power(3.2, size=num_particles)
    x *= r
    y *= r

    cos_a = np.cos(rotation_angle)
    sin_a = np.sin(rotation_angle)
    x_rot = x * cos_a
    z_depth = x * sin_a

    depth_scale = 1.0 + z_depth * 0.015

    screen_x = width / 2 + x_rot * scale * depth_scale
    screen_y = height / 2 - y * scale * depth_scale + 10
    return np.stack([screen_x, screen_y], axis=1)

# --------------------------------------------------------------------------
# HAND TRACKING / FINGER COUNTING
# --------------------------------------------------------------------------
mp_hands = mp.solutions.hands
mp_draw = mp.solutions.drawing_utils
mp_style = mp.solutions.drawing_styles

def count_fingers(hand_landmarks, actual_handedness_label):
    lm = hand_landmarks.landmark
    fingers = []

    if actual_handedness_label == "Right":
        fingers.append(1 if lm[4].x < lm[3].x else 0)
    else:
        fingers.append(1 if lm[4].x > lm[3].x else 0)

    for tip in [8, 12, 16, 20]:
        pip = tip - 2
        fingers.append(1 if lm[tip].y < lm[pip].y else 0)

    return sum(fingers)

# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------
def main():
    cv2.setUseOptimized(True)

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW if os.name == "nt" else 0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    if not cap.isOpened():
        print("[ERROR] Khong the mo webcam.")
        return

    hands = mp_hands.Hands(
        max_num_hands=2,
        model_complexity=0,
        min_detection_confidence=0.7,
        min_tracking_confidence=0.6,
    )

    particles = np.random.rand(NUM_PARTICLES, 2).astype(np.float32) * [WIDTH, HEIGHT]
    rotation_angle = 0.0
    targets = generate_sphere_targets(NUM_PARTICLES, WIDTH, HEIGHT, rotation_angle)
    current_colors = np.tile(PARTICLE_COLOR_BASE, (NUM_PARTICLES, 1))

    display_mode = "idle"
    prev_gesture = -1
    five_phase = None
    five_phase_start = 0.0

    trail_canvas = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)

    prev_time = time.time()
    fps = 0.0

    # tight 3x3-ish stamp -> particles stay crisp and visually separated
    SPLAT_OFFSETS = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]
    SPLAT_WEIGHTS = [1.0, 0.35, 0.35, 0.35, 0.35]

    cv2.namedWindow("Camera", cv2.WINDOW_NORMAL)
    cv2.namedWindow("Particles", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Particles", WIDTH, HEIGHT)

    print("[INFO] He thong hat da khoi dong. Nhan 'q' hoac ESC de thoat.")

    scale_x = WIDTH / DETECT_WIDTH
    scale_y = HEIGHT / DETECT_HEIGHT

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] Mat ket noi webcam.")
            break

        frame = cv2.flip(frame, 1)
        frame = cv2.resize(frame, (WIDTH, HEIGHT))

        # Run MediaPipe on a small downscaled copy -> big latency reduction
        small_rgb = cv2.cvtColor(cv2.resize(frame, (DETECT_WIDTH, DETECT_HEIGHT)), cv2.COLOR_BGR2RGB)
        small_rgb.flags.writeable = False
        results = hands.process(small_rgb)

        finger_count = 0

        if results.multi_hand_landmarks and results.multi_handedness:
            for hand_landmarks, handedness in zip(
                results.multi_hand_landmarks,
                results.multi_handedness
            ):
                raw_label = handedness.classification[0].label
                actual_label = "Right" if raw_label == "Left" else "Left"

                finger_count += count_fingers(
                    hand_landmarks,
                    actual_label
                )

                mp_draw.draw_landmarks(
                    frame,
                    hand_landmarks,
                    mp_hands.HAND_CONNECTIONS,
                    mp_style.get_default_hand_landmarks_style(),
                    mp_style.get_default_hand_connections_style(),
                )

        finger_count = max(0, min(10, finger_count))

        now = time.time()

        # ---------------- STATE MACHINE ----------------
        # ---------------- STATE MACHINE ----------------
        # ---------------- STATE MACHINE ----------------
        # Tu 1 den 8 ngon: hien thi noi dung trong GESTURE_LETTER_TEXT
        # 10 ngon: hien trai tim, sau do chuyen thanh chu TIEN

        if finger_count == 10:
            if prev_gesture != 10:
                five_phase = "heart"
                five_phase_start = now
            elif (
                five_phase == "heart"
                and (now - five_phase_start) >= HEART_HOLD_SECONDS
            ):
                five_phase = "tien"

            new_mode = "heart" if five_phase == "heart" else "tien"

        else:
            five_phase = None
            new_mode = GESTURE_LETTER_TEXT.get(finger_count, "idle")

        mode_changed = (new_mode != display_mode)
        display_mode = new_mode
        prev_gesture = finger_count
        # ---------------- TARGET / COLOR UPDATE ----------------
        if display_mode == "idle":
            rotation_angle += ROTATION_SPEED_SPHERE
            targets = generate_sphere_targets(NUM_PARTICLES, WIDTH, HEIGHT, rotation_angle)
            target_color = PARTICLE_COLOR_BASE
        elif display_mode == "heart":
            rotation_angle += ROTATION_SPEED_HEART
            targets = generate_heart_targets(NUM_PARTICLES, WIDTH, HEIGHT, rotation_angle)
            target_color = PARTICLE_COLOR_HEART
        elif display_mode == "tien":
            if mode_changed:
                targets = generate_letter_targets("TIÊN", NUM_PARTICLES, WIDTH, HEIGHT)
            target_color = PARTICLE_COLOR_BASE
        else:
            if mode_changed:
                targets = generate_letter_targets(display_mode, NUM_PARTICLES, WIDTH, HEIGHT)
            target_color = PARTICLE_COLOR_BASE

        current_colors += (target_color[None, :] - current_colors) * 0.12

        # ---- VECTORIZED PARTICLE PHYSICS ----
        particles += (targets - particles) * LERP_SPEED
        particles += np.random.normal(0.0, JITTER_STD, size=particles.shape)
        particles[:, 0] = np.clip(particles[:, 0], 0, WIDTH - 1)
        particles[:, 1] = np.clip(particles[:, 1], 0, HEIGHT - 1)

        # ---- VECTORIZED RENDERING (crisp, separated dots) ----
        trail_canvas *= TRAIL_DECAY

        xs = particles[:, 0].astype(np.int32)
        ys = particles[:, 1].astype(np.int32)

        brightness = np.random.uniform(0.65, 1.2, size=(NUM_PARTICLES, 1)).astype(np.float32)
        sparkle_mask = np.random.rand(NUM_PARTICLES) < SPARKLE_RATIO
        colors = current_colors * brightness
        colors[sparkle_mask] = PARTICLE_COLOR_SPARKLE

        for (dx, dy), w in zip(SPLAT_OFFSETS, SPLAT_WEIGHTS):
            xs_o = np.clip(xs + dx, 0, WIDTH - 1)
            ys_o = np.clip(ys + dy, 0, HEIGHT - 1)
            np.add.at(trail_canvas, (ys_o, xs_o), colors * w)

        core = np.clip(trail_canvas, 0, 255).astype(np.uint8)
        sharp = cv2.GaussianBlur(core, (0, 0), sigmaX=GLOW_SIGMA_CORE, sigmaY=GLOW_SIGMA_CORE)
        halo = cv2.GaussianBlur(core, (0, 0), sigmaX=GLOW_SIGMA_HALO, sigmaY=GLOW_SIGMA_HALO)
        particle_canvas = cv2.addWeighted(sharp, 1.0, halo, HALO_WEIGHT, 0)
        particle_canvas = cv2.add(particle_canvas, core)  # keep hard pixel cores crisp/separated

        # ---- FPS (terminal only, no on-screen HUD) ----
        dt = now - prev_time
        prev_time = now
        if dt > 0:
            fps = 0.9 * fps + 0.1 * (1.0 / dt)

        print(f"\r[STATE] fingers={finger_count} mode='{display_mode}' FPS={fps:5.1f}   ", end="")

        cv2.imshow("Camera", frame)
        cv2.imshow("Particles", particle_canvas)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q') or key == 27:
            break

    print("\n[INFO] Dang dong ung dung...")
    cap.release()
    hands.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()