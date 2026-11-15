from itertools import combinations
from mast.logging import make_logger
from mast.cli import Cli
from mast.cli import Params, Option
from mast.hashes import get_sha256
from collections import OrderedDict
import difflib
import glob
import os

class Main(Params):
    pattern: str = Option("", short="p")
    out_dir: str = Option("", short="o")
    wrapcolumn: int = Option(80, short="w")
    tabsize: int = Option(4, short="t")
    no_same_filenames: bool = Option(False, short="n")


def main(params: Main):
    pattern = params.pattern
    out_dir = params.out_dir
    wrapcolumn = params.wrapcolumn
    tabsize = params.tabsize
    no_same_filenames = params.no_same_filenames
    same_filenames = not no_same_filenames
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
    diff = difflib.HtmlDiff(
        tabsize=tabsize,
        wrapcolumn=wrapcolumn,
        linejunk=None,
        charjunk=difflib.IS_CHARACTER_JUNK
    )
    filenames = []
    for filename in glob.glob(pattern):
        filenames.append(os.path.abspath(filename))
    common_prefix = os.path.dirname(os.path.commonprefix(filenames))
    _filenames = list(map(
        lambda x: x.replace(common_prefix, ""),
        filenames,
    ))

    # remove leading /
    _filenames = list(map(
        lambda x: x.lstrip(os.path.sep),
        _filenames,
    ))

    # Sort by dirname
    _filenames.sort(key=lambda p: p.split("/"))

    # sort by filename
    _filenames.sort(key=os.path.basename)
    for filename in _filenames:
        print(("{} {}".format(get_sha256(os.path.join(common_prefix, filename)), filename)))
    for index, (from_file, to_file) in enumerate(combinations(filenames, 2)):
        if same_filenames and os.path.basename(from_file) != os.path.basename(to_file):
            continue
        if get_sha256(from_file) == get_sha256(to_file):
            continue
        with open(from_file, "r") as fp:
            from_lines = fp.readlines()
        with open(to_file, "r") as fp:
            to_lines = fp.readlines()

        if same_filenames:
            diff_filename = "{}-{}-vs-{}-{}.html".format(
                index,
                "_".join(list(OrderedDict.fromkeys([x for x in from_file.split(os.path.sep) if x not in to_file.split(os.path.sep)]))),
                "_".join(list(OrderedDict.fromkeys([x for x in to_file.split(os.path.sep) if x not in from_file.split(os.path.sep)]))),
                os.path.basename(from_file),
            )
        else:
            diff_filename = "{}-{}-vs-{}.html".format(
                index,
                "_".join(list(OrderedDict.fromkeys([x for x in from_file.split(os.path.sep) if x not in to_file.split(os.path.sep)]))),
                "_".join(list(OrderedDict.fromkeys([x for x in to_file.split(os.path.sep) if x not in from_file.split(os.path.sep)]))),
            )

        diff_filename = os.path.join(
            out_dir,
            diff_filename,
        )
        with open(diff_filename, "w") as fp:
            fp.write(
                diff.make_file(
                    from_lines,
                    to_lines,
                    from_file,
                    to_file,
                )
            )


if __name__ == "__main__":
    cli = Cli(main=main)
    try:
        cli.run()
    except SystemExit:
        pass
    except Exception:
        make_logger("error").exception("An unhandled error has occurred")
        raise
