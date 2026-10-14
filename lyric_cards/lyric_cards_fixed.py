# -*- coding: utf-8 -*-
"""
lyric_cards.py
---------------
Chương trình hiển thị các ô vuông trắng nổi (popup) phía trên cửa sổ VS Code
thật đang mở, kèm hiệu ứng typewriter, fade-in/out, phóng to/thu nhỏ,
trôi nhẹ lên trên, đồng bộ với nhạc nền (nếu có).

Chạy bằng nút "Run Python File" trong VS Code (hoặc phím F5 / Ctrl+F5).
"""

import sys
import time
import random
import tkinter as tk
from pathlib import Path

# ======================================================================
# CẤU HÌNH (chỉnh các giá trị dưới đây theo ý muốn)
# ======================================================================

WINDOW_SIZE = 270             # Kích thước ô vuông (px)
FONT_SIZE = 10                 # Cỡ chữ
TYPE_SPEED_MS = 70             # Tốc độ gõ từng ký tự (ms / ký tự)
FADE_STEP_MS = 15              # Khoảng thời gian giữa mỗi bước hiệu ứng (ms)
DISPLAY_DURATION_MS = 2200     # Thời gian ô đứng yên sau khi gõ xong (ms)
SIDE_MARGIN = 40               # Lề trái/phải khi chọn vị trí ngẫu nhiên
TOP_MARGIN = 60                # Lề trên
BOTTOM_MARGIN = 60             # Lề dưới
FLOAT_SPEED = 0.3              # Tốc độ trôi lên trên (px / bước)
PLAY_MUSIC = True              # Bật/tắt nhạc nền
MUSIC_FILE = "music/bai1.mp3"  # Đường dẫn tương đối tới file nhạc
MUSIC_VOLUME = 0.8             # Âm lượng (0.0 - 1.0)
BACKGROUND_COLOR = "#ffffff"   # Màu nền ô
TEXT_COLOR = "#111111"         # Màu chữ

ROUNDED_CORNERS = True         # Bo góc nhẹ (dùng kỹ thuật transparentcolor)
CLICK_THROUGH = False          # Cho phép bấm xuyên qua ô (thử nghiệm, mặc định TẮT để đảm bảo chạy ổn định)

GROW_STEPS = 14                # Số bước hiệu ứng phóng to / thu nhỏ
GROW_TOTAL_MS = 500             # Tổng thời gian hiệu ứng phóng to (ms)
HIDE_STEPS = 14
HIDE_TOTAL_MS = 260

KEY_COLOR = "#ff00fe"          # Màu "chìa khóa" dùng để bo góc trong suốt

# ======================================================================
# DỮ LIỆU NỘI DUNG - 15 câu, sửa thoải mái tại đây
# ======================================================================

LYRICS = [
    {"time": 8.0, "text": "All my ex’s hate me\nAnd I hate them back"},
    {"time": 14.0, "text": "And it’s all because of me\nAnd it’s all because of me"},
    {"time": 19.5, "text": "All my ex’s hate me\nAnd I hate them back"},
    {"time": 24.5, "text": "And it’s all because of me\nAnd it’s all because of me"},
    {"time": 27.5, "text": "Em đã nghe bài ca anh viết về em\nVề cô người yêu mà anh là người đứng xem"},
    {"time": 31.5, "text": "Bước trên thánh đường\nKhông còn vấn vương"},
    {"time": 35.5, "text": "Sorry babe em đã tổn thương quá nhiều"},
    {"time": 39.5, "text": "Câu chuyện sau chia tay chẳng ai đúng sai"},
    {"time": 43.0, "text": "Mà do cả hai chẳng thể cùng đến tương lai"},
    {"time": 49.0, "text": "Nên đành chấp nhận nỗi đau cũng cần\nCho cả hai yêu thêm người sau tốt hơn"},
    {"time": 51.75, "text": "Cám ơn anh vì ngày tháng qua"},
    {"time": 57.0, "text": "Và cám ơn anh vì đã để chúng mình xa"},
    {"time": 62.0, "text": "Để rồi mới biết ra"},
    {"time": 67.5, "text": "Ta vốn không thuộc về nhau"},
    {"time": 70.0, "text": "...."},
]

# ======================================================================
# ĐƯỜNG DẪN TƯƠNG ĐỐI (dùng pathlib, tính từ vị trí file .py này)
# ======================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
MUSIC_PATH = SCRIPT_DIR / MUSIC_FILE

# ======================================================================
# THỬ IMPORT PYGAME (không bắt buộc phải có để chương trình vẫn chạy được)
# ======================================================================

pygame = None
if PLAY_MUSIC:
    try:
        import pygame  # type: ignore
    except ImportError:
        print("[Cảnh báo] Chưa cài pygame nên sẽ chạy hiệu ứng KHÔNG có nhạc.")
        print("           Cài bằng lệnh:  py -m pip install pygame")
        pygame = None

# ======================================================================
# KỸ THUẬT CLICK-THROUGH (tuỳ chọn, chỉ hoạt động trên Windows)
# ======================================================================

def try_make_click_through(win: tk.Toplevel) -> None:
    """Cố gắng làm cho cửa sổ 'trong suốt với chuột' để có thể bấm xuyên qua.
    Nếu không thực hiện được (không phải Windows, thiếu ctypes...), bỏ qua
    trong im lặng để không làm crash chương trình."""
    if not CLICK_THROUGH:
        return
    if sys.platform != "win32":
        return
    try:
        import ctypes
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020

        hwnd = win.winfo_id()
        # overrideredirect window đôi khi cần lấy handle "cha thật sự"
        parent_hwnd = ctypes.windll.user32.GetParent(hwnd)
        real_hwnd = parent_hwnd if parent_hwnd else hwnd

        style = ctypes.windll.user32.GetWindowLongW(real_hwnd, GWL_EXSTYLE)
        style |= (WS_EX_LAYERED | WS_EX_TRANSPARENT)
        ctypes.windll.user32.SetWindowLongW(real_hwnd, GWL_EXSTYLE, style)
    except Exception as err:
        print(f"[Cảnh báo] Không bật được chế độ bấm xuyên qua: {err}")


# ======================================================================
# VẼ HÌNH CHỮ NHẬT BO GÓC TRÊN CANVAS
# ======================================================================

def draw_rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius, **kwargs):
    """Vẽ một hình chữ nhật bo góc bằng polygon 'smooth' trên Canvas."""
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


# ======================================================================
# LỚP LYRICCARD - MỘT Ô VUÔNG POPUP
# ======================================================================

class LyricCard:
    """Đại diện cho một ô vuông popup hiển thị một câu lyric."""

    def __init__(self, manager: "LyricManager", text: str):
        self.manager = manager
        self.root = manager.root
        self.text = text
        self.closing = False
        self.after_ids = []
        self.char_index = 0
        self.cursor_on = True
        self.shape_id = None

        # ---- Tạo cửa sổ Toplevel không viền, luôn nổi trên cùng ----
        self.top = tk.Toplevel(self.root)
        self.top.overrideredirect(True)          # không title bar, không viền
        self.top.attributes("-topmost", True)     # luôn nằm trên cùng
        self.top.attributes("-alpha", 0.0)         # bắt đầu trong suốt hoàn toàn

        if ROUNDED_CORNERS:
            try:
                self.top.attributes("-transparentcolor", KEY_COLOR)
                self.top.configure(bg=KEY_COLOR)
                self.canvas_bg = KEY_COLOR
            except tk.TclError:
                # Một số máy/driver không hỗ trợ transparentcolor -> tắt bo góc
                self.canvas_bg = BACKGROUND_COLOR
        else:
            self.top.configure(bg=BACKGROUND_COLOR)
            self.canvas_bg = BACKGROUND_COLOR

        # ---- Vị trí ngẫu nhiên, không tràn màn hình ----
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        max_x = max(SIDE_MARGIN, screen_w - SIDE_MARGIN - WINDOW_SIZE)
        max_y = max(TOP_MARGIN, screen_h - BOTTOM_MARGIN - WINDOW_SIZE)
        self.x = random.randint(SIDE_MARGIN, max_x)
        self.y = random.randint(TOP_MARGIN, max_y)

        self.top.geometry(f"{WINDOW_SIZE}x{WINDOW_SIZE}+{self.x}+{self.y}")

        # ---- Canvas vẽ nền bo góc + chữ ----
        self.canvas = tk.Canvas(
            self.top,
            width=WINDOW_SIZE,
            height=WINDOW_SIZE,
            bg=self.canvas_bg,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self.text_id = self.canvas.create_text(
            WINDOW_SIZE // 2,
            WINDOW_SIZE // 2,
            text="",
            font=("Helvetica", FONT_SIZE, "bold"),
            fill=TEXT_COLOR,
            width=WINDOW_SIZE - 70,
            justify="center",
        )

        # Cho phép Esc đóng toàn bộ chương trình khi ô này (hiếm khi) có focus
        self.top.bind("<Escape>", lambda e: self.manager.quit_all())

        try_make_click_through(self.top)

        self._grow_in(step=0)

    # ------------------------------------------------------------
    def _schedule(self, ms, func):
        """Đặt lịch bằng after() của chính cửa sổ này, lưu id để hủy khi cần."""
        aid = self.top.after(ms, func)
        self.after_ids.append(aid)
        return aid

    def _cancel_all(self):
        for aid in self.after_ids:
            try:
                self.top.after_cancel(aid)
            except Exception:
                pass
        self.after_ids.clear()

    # ---------------- Hiệu ứng phóng to + hiện dần (grow-in) ----------------
    def _grow_in(self, step):
        if self.closing or not self.top.winfo_exists():
            return
        fraction = (step + 1) / GROW_STEPS
        fraction = min(fraction, 1.0)
        self._draw_shape(fraction)
        try:
            self.top.attributes("-alpha", fraction)
        except tk.TclError:
            pass

        if step + 1 < GROW_STEPS:
            interval = max(1, GROW_TOTAL_MS // GROW_STEPS)
            self._schedule(interval, lambda: self._grow_in(step + 1))
        else:
            self._start_typing()

    # ---------------- Hiệu ứng thu nhỏ + mờ dần (hide) ----------------
    def start_hide(self):
        if self.closing:
            return
        self.closing = True
        self._cancel_all()
        self._hide_step(0)

    def _hide_step(self, step):
        if not self.top.winfo_exists():
            return
        fraction = 1.0 - (step + 1) / HIDE_STEPS
        fraction = max(fraction, 0.0)
        self._draw_shape(fraction)
        try:
            self.top.attributes("-alpha", fraction)
        except tk.TclError:
            pass

        if step + 1 < HIDE_STEPS:
            interval = max(1, HIDE_TOTAL_MS // HIDE_STEPS)
            self._schedule(interval, lambda: self._hide_step(step + 1))
        else:
            self._destroy()

    def _destroy(self):
        try:
            self.top.destroy()
        except Exception:
            pass
        if self.manager.current_card is self:
            self.manager.current_card = None

    # ---------------- Vẽ khung hình theo tỉ lệ (dùng chung cho grow/hide) ----------------
    def _draw_shape(self, fraction):
        if self.shape_id is not None:
            self.canvas.delete(self.shape_id)
        size = max(4, WINDOW_SIZE * fraction)
        x1 = (WINDOW_SIZE - size) / 2
        y1 = (WINDOW_SIZE - size) / 2
        x2 = x1 + size
        y2 = y1 + size
        radius = min(24, size / 4)
        if ROUNDED_CORNERS:
            self.shape_id = draw_rounded_rect(
                self.canvas, x1, y1, x2, y2, radius,
                fill=BACKGROUND_COLOR, outline=BACKGROUND_COLOR,
            )
        else:
            self.shape_id = self.canvas.create_rectangle(
                x1, y1, x2, y2, fill=BACKGROUND_COLOR, outline=BACKGROUND_COLOR,
            )
        # Đưa chữ lên trên hình nền
        self.canvas.tag_raise(self.text_id)

    # ---------------- Hiệu ứng gõ chữ kiểu typewriter ----------------
    def _start_typing(self):
        self.char_index = 0
        self.cursor_on = True
        self._type_step()

    def _type_step(self):
        if self.closing or not self.top.winfo_exists():
            return
        if self.char_index < len(self.text):
            self.char_index += 1
            cursor = "|" if self.cursor_on else " "
            self.cursor_on = not self.cursor_on
            shown = self.text[: self.char_index] + cursor
            self.canvas.itemconfig(self.text_id, text=shown)
            self._schedule(TYPE_SPEED_MS, self._type_step)
        else:
            # Gõ xong -> bỏ con trỏ
            self.canvas.itemconfig(self.text_id, text=self.text)
            self._start_float()
            self._schedule(DISPLAY_DURATION_MS, self.start_hide)

    # ---------------- Hiệu ứng trôi nhẹ lên trên trong lúc hiển thị ----------------
    def _start_float(self):
        self._float_pos_y = float(self.y)
        self._float_step()

    def _float_step(self):
        if self.closing or not self.top.winfo_exists():
            return
        self._float_pos_y -= FLOAT_SPEED
        try:
            self.top.geometry(f"+{self.x}+{int(self._float_pos_y)}")
        except tk.TclError:
            return
        self._schedule(40, self._float_step)


# ======================================================================
# LỚP LYRICMANAGER - ĐIỀU PHỐI TOÀN BỘ CHƯƠNG TRÌNH
# ======================================================================

class LyricManager:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()               # ẩn cửa sổ root, chỉ dùng làm gốc
        self.root.bind_all("<Escape>", lambda e: self.quit_all())

        self.current_card = None
        self.lyric_index = 0
        self.start_clock = time.time()
        self.music_ok = False
        self.shutting_down = False
        self.end_scheduled = False

        self._init_music()
        self.root.after(50, self._tick)

    # ---------------- Khởi tạo nhạc nền (nếu bật) ----------------
    def _init_music(self):
        if not PLAY_MUSIC or pygame is None:
            return
        try:
            if not MUSIC_PATH.exists():
                print(f"[Cảnh báo] Không tìm thấy file nhạc: {MUSIC_PATH}")
                print("           Chương trình vẫn chạy hiệu ứng bằng đồng hồ nội bộ.")
                return
            pygame.mixer.init()
            pygame.mixer.music.load(str(MUSIC_PATH))
            pygame.mixer.music.set_volume(max(0.0, min(1.0, MUSIC_VOLUME)))
            pygame.mixer.music.play()
            self.music_ok = True
        except Exception as err:
            print(f"[Cảnh báo] Lỗi khi phát nhạc: {err}")
            print("           Chương trình vẫn chạy hiệu ứng bằng đồng hồ nội bộ.")
            self.music_ok = False

    # ---------------- Lấy thời gian đã trôi qua (đồng bộ với nhạc nếu có) ----------------
    def _get_elapsed(self):
        if self.music_ok:
            try:
                pos_ms = pygame.mixer.music.get_pos()
                if pos_ms is not None and pos_ms >= 0:
                    return pos_ms / 1000.0
            except Exception:
                pass
        return time.time() - self.start_clock

    # ---------------- Vòng lặp điều phối chính (dùng after(), không dùng while) ----------------
    def _tick(self):
        if self.shutting_down:
            return

        elapsed = self._get_elapsed()

        while self.lyric_index < len(LYRICS) and LYRICS[self.lyric_index]["time"] <= elapsed:
            self._show_card(LYRICS[self.lyric_index]["text"])
            self.lyric_index += 1

        all_shown = self.lyric_index >= len(LYRICS)
        music_still_playing = self.music_ok and self._music_busy()

        if all_shown and not music_still_playing and not self.end_scheduled:
            self.end_scheduled = True
            self.root.after(int(DISPLAY_DURATION_MS * 0.6), self._finish)

        if not self.shutting_down:
            self.root.after(50, self._tick)

    def _music_busy(self):
        try:
            return bool(pygame.mixer.music.get_busy())
        except Exception:
            return False

    # ---------------- Hiển thị ô mới, làm ô cũ mờ dần rồi biến mất ----------------
    def _show_card(self, text):
        if self.current_card is not None:
            self.current_card.start_hide()
        self.current_card = LyricCard(self, text)

    # ---------------- Kết thúc chương trình khi hết nhạc / hết danh sách ----------------
    def _finish(self):
        if self.current_card is not None:
            self.current_card.start_hide()
        self.root.after(1000, self.quit_all)

    # ---------------- Thoát sạch sẽ (Esc hoặc kết thúc tự nhiên) ----------------
    def quit_all(self):
        if self.shutting_down:
            return
        self.shutting_down = True

        if self.current_card is not None:
            try:
                self.current_card._cancel_all()
                self.current_card.top.destroy()
            except Exception:
                pass
            self.current_card = None

        # Đóng toàn bộ Toplevel còn sót (phòng trường hợp bất thường)
        for w in list(self.root.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass

        if self.music_ok:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.quit()
            except Exception:
                pass

        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            # Nhấn Ctrl+C trong terminal VS Code cũng phải thoát sạch sẽ
            self.quit_all()


# ======================================================================
# ĐIỂM BẮT ĐẦU CHƯƠNG TRÌNH
# ======================================================================

if __name__ == "__main__":
    manager = LyricManager()
    manager.run()
