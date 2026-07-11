from pathlib import Path

from pdf2dt.assets import LocalFirstDownloader
from pdf2dt.assets.models import DownloadStatus


def test_local_first_downloader_reads_file_uri_for_temp_image(tmp_path: Path) -> None:
    image = tmp_path / "figure.png"
    image.write_bytes(b"test image")

    result = LocalFirstDownloader([]).download(image.as_uri())

    assert result.status is DownloadStatus.OK
    assert result.content == b"test image"
    assert result.content_type == "image/png"


def test_local_first_downloader_reads_windows_drive_file_uri(tmp_path: Path) -> None:
    image = tmp_path / "figure.png"
    image.write_bytes(b"test image")
    uri = f"file:///{image.drive}{image.as_posix()[2:]}"

    result = LocalFirstDownloader([]).download(uri)

    assert result.status is DownloadStatus.OK
    assert result.content == b"test image"
