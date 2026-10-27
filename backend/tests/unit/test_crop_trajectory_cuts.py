from src.media import reframing

WIDTH = 1920
CROP_W = 608


def _track(points):
    return [(t, x, 0.01) for t, x in points]


def _x_at(keys, t):
    """Crop x the piecewise-linear pan expression would produce at time t."""
    for (t0, x0), (t1, x1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            return x0 + (x1 - x0) * (t - t0) / max(1e-3, t1 - t0)
    return keys[-1][1]


def test_crop_jumps_at_a_camera_cut_instead_of_gliding():
    # Speaker on the left for the first shot, far right after a cut at 5 s.
    track = _track([(i / 3, 400.0) for i in range(15)] + [(5 + i / 3, 1500.0) for i in range(15)])

    keys = reframing.build_crop_trajectory(track, WIDTH, CROP_W, scene_cuts=[5.0])

    before, after = _x_at(keys, 4.99), _x_at(keys, 5.0)
    assert abs(before - (400 - CROP_W / 2)) < 40
    assert abs(after - (1500 - CROP_W / 2)) < 40


def test_without_cuts_the_crop_still_eases():
    track = _track([(i / 3, 400.0) for i in range(15)] + [(5 + i / 3, 1500.0) for i in range(15)])

    keys = reframing.build_crop_trajectory(track, WIDTH, CROP_W)

    # The spring takes time to travel, so just after 5 s it is still far from the target.
    assert _x_at(keys, 5.0) < 1500 - CROP_W / 2 - 200


def test_a_shot_without_detections_borrows_its_neighbours():
    # The middle shot (5-8 s) has no detections; tracking must not fail.
    track = _track(
        [(i / 3, 500.0) for i in range(15)]
        + [(5 + i / 3, None) for i in range(9)]
        + [(8 + i / 3, 1400.0) for i in range(15)]
    )

    keys = reframing.build_crop_trajectory(track, WIDTH, CROP_W, scene_cuts=[5.0, 8.0])

    assert keys
    assert abs(_x_at(keys, 9.0) - (1400 - CROP_W / 2)) < 40
