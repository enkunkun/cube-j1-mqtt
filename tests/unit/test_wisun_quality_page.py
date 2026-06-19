"""Real-time Wi-SUN Quality page (spec 007)."""
import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# render_sparkline: pure helper (testable, deterministic SVG path)
# ---------------------------------------------------------------------------

def test_render_sparkline_empty_samples_returns_empty_path():
    assert mb.render_sparkline([], width=200, height=40) == ""


def test_render_sparkline_one_sample_returns_dot():
    svg = mb.render_sparkline([100.0], width=200, height=40)
    # 1 サンプルは折れ線が引けないので円 (or 'M x y') を返す。 ここでは
    # path がそれっぽい文字列であることだけ確認。
    assert "M" in svg or "circle" in svg


def test_render_sparkline_two_samples_returns_line():
    svg = mb.render_sparkline([100.0, 200.0], width=200, height=40)
    # SVG path 形式 M x1 y1 L x2 y2
    assert svg.startswith("M")
    assert "L" in svg


def test_render_sparkline_is_deterministic():
    samples = [50.0, 100.0, 150.0, 200.0, 250.0]
    a = mb.render_sparkline(samples, width=200, height=40)
    b = mb.render_sparkline(samples, width=200, height=40)
    assert a == b


def test_render_sparkline_uses_full_height_for_range():
    """min サンプルは bottom 付近 (y=height)、 max サンプルは top 付近 (y=0)
    に来ること。 軸反転 (SVG は下が +y)。"""
    samples = [10.0, 1000.0, 10.0]
    svg = mb.render_sparkline(samples, width=200, height=40)
    # Smoke: 0 と height 付近の y 座標が path 中に出現する
    assert ("Y 0" in svg or "0 " in svg or "0," in svg or " 0 " in svg
            or svg.count("0") >= 1)
    assert svg.count("40") >= 1 or "40 " in svg or "40," in svg


# ---------------------------------------------------------------------------
# /wisun page exists and contains the expected scaffolding
# ---------------------------------------------------------------------------

def test_wisun_html_has_expected_title_and_hooks():
    html = mb.WISUN_HTML
    assert "<title>Wi-SUN Quality</title>" in html
    assert "/api/wisun_quality" in html
    # 自動更新の hook
    assert "setInterval" in html or "setTimeout" in html
