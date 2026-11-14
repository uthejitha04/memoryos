"""Status polling must not prevent a worker from persisting its result."""
import pathlib
import threading
from concurrent.futures import ThreadPoolExecutor

import mobile_ingest as mobile


def test_completion_waits_for_open_status_reader(tmp_path, monkeypatch):
    path = tmp_path / "recording.json"
    mobile.save_json(path, {"status": "processing"})
    reading, release, writing = (threading.Event() for _ in range(3))
    original_read = pathlib.Path.read_text
    original_replace = pathlib.Path.replace

    def held_read(target, *args, **kwargs):
        if target != path:
            return original_read(target, *args, **kwargs)
        with target.open("r", encoding="utf-8") as stream:
            reading.set()
            try:
                assert release.wait(5), "reader was not released"
                return stream.read()
            finally:
                reading.clear()

    def windows_replace(source, destination):
        if destination == path and reading.is_set():
            raise PermissionError("Windows cannot replace an open status file")
        return original_replace(source, destination)

    def finish():
        writing.set()
        mobile.save_json(path, {"status": "complete", "text": "Saved speech"})

    monkeypatch.setattr(pathlib.Path, "read_text", held_read)
    monkeypatch.setattr(pathlib.Path, "replace", windows_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(mobile.load_json, path)
        try:
            assert reading.wait(2)
            writer = pool.submit(finish)
            assert writing.wait(2)
            # Completion must wait rather than fail or overwrite an open file.
            from concurrent.futures import wait
            assert not wait([writer], timeout=0.1).done
        finally:
            release.set()
        assert reader.result(timeout=2) == {"status": "processing"}
        writer.result(timeout=2)
    assert mobile.load_json(path) == {"status": "complete", "text": "Saved speech"}
