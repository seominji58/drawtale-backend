"""4족형 정지 그림 안 입 · 머리 꼭대기 · 등 자리 (jobs.still_body, 11/6). 그림은 여기서 그린다 — 강아지 · 기린 · 머리 큰 고양이 모양."""

import io

from PIL import Image, ImageDraw

from app.services.jobs import still_body

FILL = (200, 160, 80, 255)


def _png(draw) -> bytes:
    im = Image.new("RGBA", (900, 700), (0, 0, 0, 0))
    draw(ImageDraw.Draw(im))
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def _joints(**pts) -> list[dict]:
    return [{"name": k, "x": x, "y": y} for k, (x, y) in pts.items()]


def _legs(d, xs):
    for x in xs:
        d.rectangle((x, 460, x + 34, 640), fill=FILL)


def _near(p, x, y, tol=0.06):
    return abs(p[0] - x) <= tol and abs(p[1] - y) <= tol


BOX = (0, 0, 900, 700)


def test_dog_head_on_the_right_with_ears():
    def draw(d):
        d.rounded_rectangle((200, 300, 600, 470), 40, fill=FILL)
        _legs(d, (230, 300, 500, 560))
        d.ellipse((560, 180, 760, 380), fill=FILL)          # 머리
        d.ellipse((740, 200, 800, 330), fill=FILL)          # 늘어진 귀 — 주둥이보다 앞
        d.ellipse((700, 300, 790, 360), fill=FILL)          # 주둥이 (아래)
    body = still_body(_png(draw), _joints(hip=(260, 380), torso=(520, 370), neck=(580, 340),
                                          right_hip_f=(520, 460), left_hip_f=(560, 460)), BOX)
    assert body
    # 입 = 주둥이 아래 앞(턱). 귀 끝(위 · 더 앞)이 아니다
    assert 300 / 700 < body["mouth"][1] <= 362 / 700
    assert body["mouth"][0] > 730 / 900
    assert _near(body["head"], 660 / 900, 180 / 700, 0.1)
    assert abs(body["back"][1] - 300 / 700) < 0.03          # 몸 윗선


def test_giraffe_mouth_is_at_the_nose_not_beside_the_neck():
    def draw(d):
        d.rounded_rectangle((200, 330, 560, 470), 40, fill=FILL)
        _legs(d, (230, 300, 460, 520))
        d.polygon([(500, 350), (560, 340), (700, 110), (650, 95)], fill=FILL)   # 긴 목
        d.ellipse((620, 50, 770, 140), fill=FILL)                                # 작은 머리
    body = still_body(_png(draw), _joints(hip=(260, 400), torso=(500, 390), neck=(540, 360),
                                          right_hip_f=(460, 460), left_hip_f=(520, 460)), BOX)
    assert body
    # 입 = 작은 머리의 아래 앞(턱) — 목 옆 허공(가운데 높이)이 아니다
    assert 50 / 700 < body["mouth"][1] <= 142 / 700 and body["mouth"][0] > 700 / 900
    assert body["head"][1] < 60 / 700
    assert abs(body["back"][1] - 330 / 700) < 0.03


def test_big_head_on_the_left():
    def draw(d):
        d.rounded_rectangle((330, 330, 660, 470), 50, fill=FILL)
        _legs(d, (360, 420, 560, 610))
        d.ellipse((90, 150, 380, 440), fill=FILL)
    body = still_body(_png(draw), _joints(hip=(620, 400), torso=(400, 390), neck=(360, 360),
                                          right_hip_f=(360, 460), left_hip_f=(420, 460)), BOX)
    assert body
    assert body["mouth"][0] < 0.2 and body["mouth"][1] > 295 / 700   # 왼쪽 머리 앞 아래(턱)
    assert _near(body["head"], 235 / 900, 150 / 700, 0.08)
    assert abs(body["back"][1] - 330 / 700) < 0.03


def test_without_joints_or_alpha_there_is_nothing():
    assert still_body(b"", None, BOX) is None
    assert still_body(_png(lambda d: None), _joints(hip=(1, 1), torso=(2, 2), neck=(3, 3)), BOX) is None
    rgb = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(rgb, format="PNG")
    assert still_body(rgb.getvalue(), _joints(hip=(1, 1), torso=(5, 5), neck=(8, 5)), (0, 0, 10, 10)) is None
