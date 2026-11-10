"""정지 그림 안 주인공 손 위치 (2026-11-04)."""
import io
import json

from PIL import Image

from app.services.jobs import read_still_hands, still_hands, still_meta_path, trim_still_box


def _png(w, h, box):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for x in range(box[0], box[2]):
        for y in range(box[1], box[3]):
            im.putpixel((x, y), (200, 0, 0, 255))
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def test_hands_are_fractions_of_trimmed_box():
    png, box = trim_still_box(_png(200, 300, (50, 60, 150, 260)))
    assert box[0] <= 50 and box[2] >= 150
    joints = [{"name": "right_hand", "x": 55, "y": 160}, {"name": "left_hand", "x": 145, "y": 150}, {"name": "neck", "x": 100, "y": 90}]
    hands = still_hands(joints, box)
    assert hands["l"][0] < 0.2 and hands["r"][0] > 0.8
    assert 0.4 < hands["l"][1] < 0.6


def test_hands_missing_joint_is_none():
    assert still_hands([{"name": "neck", "x": 1, "y": 1}], (0, 0, 10, 10)) is None
    assert still_hands(None, (0, 0, 10, 10)) is None


class _Store:
    def __init__(self):
        self.files = {}

    def save(self, path, data):
        self.files[path] = data
        return path

    def read(self, path):
        return self.files[path]


def test_read_still_hands_roundtrip_and_missing():
    s = _Store()
    s.save(still_meta_path("results/x/still.png"), json.dumps({"hands": {"l": [0.1, 0.5], "r": [0.9, 0.5]}}).encode())
    assert read_still_hands(s, "results/x/still.png") == {"l": [0.1, 0.5], "r": [0.9, 0.5]}
    assert read_still_hands(s, "results/y/still.png") is None
    assert read_still_hands(s, None) is None
