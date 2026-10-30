import io

import pytest
from PIL import Image

from app.services import media_service, media_storage
from app.services.media_service import MediaError, process_image


def _image_bytes(fmt: str, size=(64, 32), mode="RGB", color=(200, 30, 30), **save_kwargs) -> bytes:
    im = Image.new(mode, size, color)
    buf = io.BytesIO()
    im.save(buf, fmt, **save_kwargs)
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def test_process_rotates_by_exif_and_drops_metadata():
    """EXIF の向きを反映してから、位置情報を含むメタデータを書き出さない。"""
    exif = Image.Exif()
    exif[0x0112] = 6  # 右に90度回して見る
    exif[0x8825] = {1: "N", 2: (35.0, 39.0, 0.0)}  # GPS
    data = _image_bytes("JPEG", size=(80, 40), exif=exif.tobytes())
    out = process_image(data, max_side=2048, variants=("s", "og"))
    assert (out.width, out.height) == (40, 80)
    main = _open(out.files[""])
    assert main.format == "WEBP" and main.size == (40, 80)
    assert not main.info.get("exif") and not main.info.get("icc_profile")
    assert not _open(out.files["og"]).info.get("exif")


def test_process_resizes_and_makes_variants():
    data = _image_bytes("PNG", size=(3000, 1000))
    out = process_image(data, max_side=2048, variants=("s", "og"))
    assert (out.width, out.height) == (2048, 683)
    assert out.sizes == {"s": (480, 160), "og": (1200, 400)}
    assert _open(out.files["s"]).format == "WEBP"
    og = _open(out.files["og"])
    assert og.format == "JPEG" and og.mode == "RGB"
    assert set(out.files) == {"", "s", "og"}


def test_process_keeps_transparency_but_flattens_og():
    data = _image_bytes("PNG", size=(50, 50), mode="RGBA", color=(0, 0, 255, 0))
    out = process_image(data, max_side=2048, variants=("og",))
    assert _open(out.files[""]).mode == "RGBA"
    og = _open(out.files["og"])
    assert og.mode == "RGB" and og.getpixel((10, 10)) == (255, 255, 255)
    # 透明な所がなければ RGB にする
    opaque = process_image(_image_bytes("PNG", mode="RGBA", color=(1, 2, 3, 255)), max_side=2048)
    assert _open(opaque.files[""]).mode == "RGB"


def test_process_rejects_non_images_and_unsupported():
    with pytest.raises(MediaError) as e:
        process_image(b"not an image", max_side=2048)
    assert e.value.code == "not_image"
    with pytest.raises(MediaError) as e:
        process_image(_image_bytes("BMP"), max_side=2048)
    assert e.value.code == "format"
    frames = [Image.new("RGB", (10, 10), c) for c in ((255, 0, 0), (0, 255, 0))]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    with pytest.raises(MediaError) as e:
        process_image(buf.getvalue(), max_side=2048)
    assert e.value.code == "animated"


def test_process_rejects_too_many_pixels(monkeypatch):
    monkeypatch.setattr(media_service, "MAX_SOURCE_PIXELS", 1000)
    with pytest.raises(MediaError) as e:
        process_image(_image_bytes("PNG", size=(40, 40)), max_side=2048)
    assert e.value.code == "pixels"


def test_variant_url():
    key = "abcdefghijklmnopqrstuvwx"
    src = media_storage.public_url("announcement", key)
    assert src == f"/media/announcement/ab/{key}.webp"
    assert media_storage.variant_url(src, "og", kind="announcement") == f"/media/announcement/ab/{key}_og.jpg"
    assert media_storage.variant_url(src, "s") == f"/media/announcement/ab/{key}_s.webp"
    assert media_storage.variant_url(src, "og", kind="chat") is None
    assert media_storage.variant_url("/static/images/a.png", "og") is None
    assert media_storage.variant_url(f"/media/announcement/zz/{key}.webp", "og") is None
