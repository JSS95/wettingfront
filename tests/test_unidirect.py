import os
import subprocess

import yaml

from wettingfront import get_sample_path


def test_Unidirectional(tmp_path):
    config = dict(
        data1={
            "type": "Unidirectional",
            "model": "Washburn",
            "path": get_sample_path("example.mp4"),
            "output-vid": str(tmp_path / "vid.mp4"),
            "output-data": str(tmp_path / "data.csv"),
        },
        data2={
            "type": "Unidirectional",
            "path": get_sample_path("example.mp4"),
            "output-vid": str(tmp_path / "vid.mp4"),
            "output-data": str(tmp_path / "data.csv"),
        },
    )
    path = tmp_path / "config.yml"
    with open(path, "w") as f:
        yaml.dump(config, f)
    code = subprocess.call(
        [
            "wettingfront",
            "analyze",
            path,
        ],
    )
    assert not code
    assert os.path.exists(config["data1"]["output-vid"])
    assert os.path.exists(config["data1"]["output-data"])
    assert os.path.exists(config["data2"]["output-vid"])
    assert os.path.exists(config["data2"]["output-data"])
