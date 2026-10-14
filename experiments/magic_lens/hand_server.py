import socket
import struct

import cv2
import mediapipe as mp
import numpy as np

HOST, PORT = "127.0.0.1", 5555

hands = mp.solutions.hands.Hands(
    static_image_mode=False,
    max_num_hands=2,
    model_complexity=0,
    min_detection_confidence=0.6,
    min_tracking_confidence=0.5,
)


def recv_all(conn, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("client closed")
        buf += chunk
    return bytes(buf)


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    print(f"hand_server listening on {HOST}:{PORT}")
    while True:
        conn, _ = srv.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            while True:
                length = struct.unpack("!I", recv_all(conn, 4))[0]
                jpg = recv_all(conn, length)
                img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                res = hands.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
                lms = res.multi_hand_landmarks or []
                out = bytearray(struct.pack("B", len(lms)))
                for h in lms:
                    flat = [v for p in h.landmark for v in (p.x, p.y)]
                    out += struct.pack("<42f", *flat)
                conn.sendall(bytes(out))
        except (ConnectionError, OSError):
            pass
        finally:
            conn.close()


if __name__ == "__main__":
    main()
