"""Caption placement. Run: .venv/bin/python tests/test_captions.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import clipper as c


def test_crop_captions_bottom_lower_third():
    an, margin = c.caption_anchor("crop", (1920, 1080))
    assert an == 2, "crop captions are bottom-anchored"
    assert 0.10 * c.H < margin < 0.30 * c.H, "crop captions sit in the lower third"


def test_zoom_captions_above_hud_not_top_bar():
    dims = (1920, 1080)
    an, margin = c.caption_anchor("zoom", dims)
    _, _, top_bar, hud_out_h, _, _ = c.zoom_geometry(dims)
    assert an == 2, "zoom captions are bottom-anchored now (was top bar)"
    assert margin >= hud_out_h, "zoom captions clear the HUD strip"
    assert margin < c.H * 0.5, "zoom captions stay in the lower half, near the action"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"PASS {name}")
    print("caption tests passed")
