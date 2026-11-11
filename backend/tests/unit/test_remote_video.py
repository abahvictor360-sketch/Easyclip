import pytest

from src.remote_video import is_facebook_url, remote_video_path
from src.services.video_service import VideoService


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/SomePage/videos/sunday-service/1234567890/",
        "https://web.facebook.com/watch/?v=1234567890",
        "https://m.facebook.com/reel/1234567890",
        "https://fb.watch/abcDEF123/",
        "https://www.facebook.com/share/v/1AbCdEf/",
    ],
)
def test_facebook_links_are_recognised_as_video_sources(url):
    assert is_facebook_url(url)
    assert VideoService.determine_source_type(url) == "video_url"


@pytest.mark.parametrize(
    "url",
    [
        "https://notfacebook.com/videos/1",
        "https://facebook.com.evil.example/videos/1",
        "ftp://www.facebook.com/videos/1",
        "upload://abc.mp4",
    ],
)
def test_other_links_are_not_facebook(url):
    assert not is_facebook_url(url)


def test_unsupported_links_are_rejected():
    with pytest.raises(ValueError):
        VideoService.determine_source_type("https://vimeo.com/123")


def test_each_facebook_link_gets_its_own_stable_download_path():
    first = remote_video_path("https://fb.watch/one/")
    assert first == remote_video_path("https://fb.watch/one/")
    assert first != remote_video_path("https://fb.watch/two/")
    assert first.name.startswith("facebook-") and first.suffix == ".mp4"
