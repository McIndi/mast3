# -*- coding: utf-8 -*-
from mast.logging import make_logger
from mast.cli import Cli
from mast.cli import Params, Option
from mast.util import require_optional

# pandas is not installed with MAST.
pd = require_optional("pandas")
import os

class Main(Params):
    left_file: str = Option(
        "", short="l",
        help='The workbook which contains the worksheet to consider the '
             '"left" table')
    right_file: str = Option(
        "", short="r",
        help='The workbook which contains the worksheet to consider the '
             '"right" table')
    out_file: str = Option(
        "", short="o",
        help='The file to output the "joined" tables to')
    on: list[str] = Option(
        default_factory=list, short="O",
        help='A (space-seperated) list of column names to join on')
    how: str = Option(
        "left", short="H",
        help='how to join the two tables, must be one of "left", '
             '"right", "outer" or "inner"')


def main(params: Main):
    """given two xlsx (excel) workbooks, each containing one worksheet,
join the two worksheets and output a new workbook.


For more information on joining tables please see the
[pandas dataframe merge documentation](http://pandas.pydata.org/pandas-docs/version/0.17.1/generated/pandas.DataFrame.merge.html)"""
    left_file = params.left_file
    right_file = params.right_file
    out_file = params.out_file
    on = params.on
    how = params.how
    left = pd.read_excel(left_file)
    right = pd.read_excel(right_file)
    new = pd.merge(left, right, on=on, how=how)
    print(("SAVING {}".format(out_file)))
    new.to_excel(out_file, index=False)


if __name__ == "__main__":
    cli = Cli(main=main, description=main.__doc__)
    try:
        cli.run()
    except SystemExit:
        pass
    except Exception:
        make_logger("error").exception("An unhandled exception has occurred")
        raise
