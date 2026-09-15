"""The finalize progress bar should only move forward during the encode."""

from app.routes.content import _transcode_band_progress


def test_encode_maps_onto_ten_to_sixty_band():
    assert _transcode_band_progress(0, 10) == 10
    assert _transcode_band_progress(50, 10) == 35
    assert _transcode_band_progress(100, 10) == 60


def test_message_without_percent_keeps_bar_in_place():
    # Seen on ReleaseDraft:Df5af2dc: "Encoding 100%" at 59, then
    # "Poster frame written" (no percent) dropped the bar to 10.
    assert _transcode_band_progress(None, 59) == 59


def test_bar_never_goes_backwards():
    assert _transcode_band_progress(20, 45) == 45


def test_real_sequence_from_test_upload_is_monotonic():
    bar = 10
    seen = []
    for pct in [None, 6, 14, 23, 32, 40, 49, 57, 63, 71, 80, 89, 100, None]:
        bar = _transcode_band_progress(pct, bar)
        seen.append(bar)
    assert seen == sorted(seen)
    assert seen[-1] == 60
