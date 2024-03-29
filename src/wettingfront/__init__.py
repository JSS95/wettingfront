"""Package to analyze wetting front data.

To analyze with command line, specify the parameters in configuration file(s) and run::

    wettingfront analyze <file1> [<file2> ...]
"""

import argparse
import csv
import glob
import json
import logging
import os
import re
import sys
from typing import List, Optional

import numpy as np
import yaml

if sys.version_info < (3, 10):
    from importlib_metadata import entry_points
    from importlib_resources import files
else:
    from importlib.metadata import entry_points
    from importlib.resources import files

__all__ = [
    "get_sample_path",
    "analyze_files",
]


def get_sample_path(*paths: str) -> str:
    """Get path to sample file.

    Arguments:
        paths: Subpaths under ``wettingfront/samples/`` directory.

    Returns:
        Absolute path to the sample file.

    Examples:
        >>> from wettingfront import get_sample_path
        >>> get_sample_path() # doctest: +SKIP
        'path/wettingfront/samples'
        >>> get_sample_path("myfile") # doctest: +SKIP
        'path/wettingfront/samples/myfile'
    """
    return str(files("wettingfront").joinpath("samples", *paths))


def analyze_files(
    *paths: str, recursive: bool = False, entries: Optional[List[str]] = None
) -> bool:
    """Perform analysis from configuration files.

    Supported formats:
        * YAML
        * JSON

    Each file can have multiple entries. Each entry must have ``type`` field which
    specifies the analyzer. For example, the following YAML file contains ``foo``
    entry which is analyzed by ``Foo`` analyzer.

    .. code-block:: yaml

        foo:
            type: Foo
            ...

    Analyzers are searched and loaded from entry point group
    ``"wettingfront.analyzers"``, and must have the following signature:

    * entry name (:obj:`str`)
    * entry fields (:obj:`dict`)

    Arguments:
        paths: Glob pattern for configuration file paths.
        recursive: If True, search *paths* recursively.
        entries: Regular expression for entries.
            If passed, only the matching entries are analyzed.

    Returns:
        Whether the analysis is finished without error.
    """
    # load analyzers
    ANALYZERS = {}
    for ep in entry_points(group="wettingfront.analyzers"):
        ANALYZERS[ep.name] = ep

    glob_paths = []
    for path in paths:
        glob_paths.extend(glob.glob(os.path.expandvars(path), recursive=recursive))

    if entries is not None:
        entry_patterns = [re.compile(e) for e in entries]
    else:
        entry_patterns = []

    ok = True
    for path in glob_paths:
        _, ext = os.path.splitext(path)
        ext = ext.lstrip(os.path.extsep).lower()
        try:
            with open(path, "r") as f:
                if ext == "yaml" or ext == "yml":
                    data = yaml.load(f, Loader=yaml.FullLoader)
                elif ext == "json":
                    data = json.load(f)
                else:
                    logging.error(f"Skipping file: '{path}' (format not supported)")
                    ok = False
                    continue
        except FileNotFoundError:
            logging.error(f"Skipping file: '{path}' (does not exist)")
            ok = False
            continue
        for k, v in data.items():
            if entry_patterns and all([p.fullmatch(k) is None for p in entry_patterns]):
                continue
            try:
                typename = v["type"]
                analyzer = ANALYZERS.get(typename, None)
                if analyzer is not None:
                    analyzer.load()(k, v)
                else:
                    logging.error(
                        f"Skipping entry: '{path}::{k}' (unknown type: '{typename}')"
                    )
            except Exception:
                logging.exception(f"Skipping entry: '{path}::{k}' (exception raised)")
                ok = False
                continue
    return ok


def unidirect_analyzer(k, v):
    """Image analysis for unidirectional liquid imbibition in porous medium.

    .. note::

        To evoke this analyzer, you need ``img`` optional dependency::

            pip install wettingfront[img]

    Unidirectional analyzer detects the horizontal wetting front in the image by
    pixel intensities and fits the data to model.

    The analyzer defines the following fields in configuration entry:

    - **model** (`str`, optional): Wetting front model, implemented by plugins.
    - **path** (`str`): Path to the input video file.
    - **output-vid** (`str`, optional): Path to the output video file.
    - **output-data** (`str`, optional): Path to the output csv file.

    The output csv file contains three colums; time, wetting height, and fitted
    wetting height. The time unit is seconds and the distance unit is pixels.

    The following is the example for an entry in YAML configuration file:

    .. code-block:: yaml

        foo:
            type: Unidirectional
            model: Washburn
            path: foo.mp4
            output-vid: output/foo.mp4
            output-data: output/foo.csv
    """
    import imageio.v3 as iio

    MODELS = {}
    for ep in entry_points(group="wettingfront.models"):
        MODELS[ep.name] = ep

    # Prepare output
    model = v.get("model")
    if model is not None:
        model = MODELS[model].load()
    path = os.path.expandvars(v["path"])
    out_vid = v.get("output-vid")
    out_data = v.get("output-data")
    if out_vid:
        out_vid = os.path.expandvars(v["output-vid"])
        dirname, _ = os.path.split(out_vid)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
    if out_data:
        out_data = os.path.expandvars(v["output-data"])
        dirname, _ = os.path.split(out_data)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

    def yield_result(path):
        for frame in iio.imiter(path, plugin="pyav"):
            gray = np.dot(frame, [0.2989, 0.5870, 0.1140])
            h = np.argmax(np.abs(np.diff(np.mean(gray, axis=1))))
            frame[h, :] = (255, 0, 0)
            yield frame, int(frame.shape[0] - h)

    def iioWriter(path, codec, fps):
        with iio.imopen(path, "w", plugin="pyav") as out:
            out.init_video_stream(codec, fps=fps)
            while True:
                frame = yield
                out.write_frame(frame)

    immeta = iio.immeta(path, plugin="pyav")
    gen = yield_result(path)

    if out_vid:
        iiowriter = iioWriter(out_vid, immeta["codec"], immeta["fps"])
        next(iiowriter)
    if out_data:
        heights = []

    for frame, h in gen:
        if out_vid:
            iiowriter.send(frame)
        if out_data:
            heights.append(h)

    # write data
    if out_data:
        times = np.arange(len(heights)) / immeta["fps"]
        if model is None:
            header = ["time (s)", "height (pixels)"]
            data = zip(times, heights)
        else:
            header = ["time (s)", "height (pixels)", "fitted height (pixels)"]
            func, _ = model(times, heights)
            predict = func(times)
            data = zip(times, heights, predict)

        with open(out_data, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            for row in data:
                writer.writerow(row)


def main():
    """Entry point function."""
    parser = argparse.ArgumentParser(
        prog="wettingfront",
        description="Wetting front analysis.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="WARNING",
        help="set logging level",
    )
    subparsers = parser.add_subparsers(dest="command")

    samples = subparsers.add_parser(
        "samples",
        description="Show path to sample directory.",
        help="show path to sample directory",
    ).add_mutually_exclusive_group()
    samples.add_argument(
        "plugin",
        type=str,
        nargs="?",
        help="name of the plugin",
    )
    samples.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="list plugin names",
    )

    subparsers.add_parser(
        "analyzers",
        description="List installed analyzers.",
        help="list installed analyzers",
    )
    subparsers.add_parser(
        "models",
        description="List installed models.",
        help="list installed models",
    )

    analyze = subparsers.add_parser(
        "analyze",
        description="Parse configuration files and analyze.",
        help="parse configuration files and analyze",
        epilog=(
            "Supported file formats: YAML, JSON.\n"
            "Refer to the package documentation for configuration file structure."
        ),
    )
    analyze.add_argument(
        "file", type=str, nargs="+", help="glob pattern for configuration files"
    )
    analyze.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="recursively find configuration files",
    )
    analyze.add_argument(
        "-e",
        "--entry",
        action="append",
        help="regex pattern for configuration file entries",
    )

    args = parser.parse_args()

    loglevel = args.log_level.upper()
    logging.basicConfig(
        format="[%(asctime)s] [%(levelname)8s] --- %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=loglevel,
    )

    logging.debug(f"Input command: {' '.join(sys.argv)}")

    if args.command is None:
        parser.print_help(sys.stderr)
        sys.exit(1)
    elif args.command == "samples":
        if args.list:
            header = [("PLUGIN", "PATH")]
            paths = [
                (ep.name, ep.load()())
                for ep in entry_points(group="wettingfront.samples")
            ]
            col0_max = max(len(p[0]) for p in header + paths)
            space = 3
            for col0, col1 in header + paths:
                line = col0.ljust(col0_max) + " " * space + col1
                print(line)
        elif args.plugin is None:
            print(get_sample_path())
        else:
            for ep in entry_points(group="wettingfront.samples"):
                if ep.name == args.plugin:
                    getter = ep.load()
                    print(getter())
                    break
            else:
                logging.error(
                    f"Unknown plugin: '{args.plugin}' (use '-l' option to list plugins)"
                )
                sys.exit(1)
    elif args.command in ["analyzers", "models"]:
        header = [("NAME", "SOURCE")]
        eps = [
            (ep.name, ep.value.split(":")[0])
            for ep in entry_points(group=f"wettingfront.{args.command}")
        ]
        col0_max = max(len(m[0]) for m in header + eps)
        space = 3
        for col0, col1 in header + eps:
            line = col0.ljust(col0_max) + " " * space + col1
            print(line)
    elif args.command == "analyze":
        ok = analyze_files(*args.file, recursive=args.recursive, entries=args.entry)
        if not ok:
            sys.exit(1)
