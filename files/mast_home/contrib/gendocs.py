import mast.cli
from mast.cli import Params, Option
import mast.config
import mast.cron
import mast._daemon
import mast.datapower.accounts
import mast.datapower.backups
import mast.datapower.crypto
import mast.datapower.datapower
import mast.datapower.deployment
import mast.datapower.developer
import mast.datapower.network
import mast.datapower.ssh
import mast.datapower.status
import mast.datapower.system
import mast.hashes
import mast.home
import mast.jobs
import mast.logging
import mast.plugin_utils
import mast.pprint
import mast.timestamp
import mast.xor

import re
import os
import inspect
import textwrap
import markdown
import subprocess
from collections import OrderedDict
from markdown.extensions.toc import TocExtension
from markdown.extensions.codehilite import CodeHiliteExtension

def system_call(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=False):
    """
    # system_call

    helper function to shell out commands. This should be platform
    agnostic.
    """
    stderr = subprocess.STDOUT
    pipe = subprocess.Popen(
        command,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        shell=shell)
    stdout, stderr = pipe.communicate()
    return stdout, stderr


class LastUpdatedOrderedDict(OrderedDict):
    'Store items in the order the keys were last added'

    def __setitem__(self, key, value):
        if key in self:
            del self[key]
        OrderedDict.__setitem__(self, key, value)

tpl = r"""
<!DOCTYPE html>
<html>
    <head>
        <style>
            body {
                background-color: #f5f5f5;
                margin-left: 10%;
                margin-right: 10%;
                font-family: Sans-Serif;
            }
            #content {
                background-color: #ffffff;
                padding: 5px 5px 5px 5px;
                box-shadow: 10px 10px 5px #888888;
            }
            #inner-content{
                width: 75%;
                margin-left: auto;
                margin-right: auto;
            }
            pre {
                overflow: auto;
                padding: 5px 5px 5px 5px;
            }
            .codehilite .hll { background-color: #ffffcc }
            .codehilite  { background: #f0f0f0;}
            .codehilite .c { color: #408080; font-style: italic } /* Comment */
            .codehilite .err { border: 1px solid #FF0000 } /* Error */
            .codehilite .k { color: #008000; font-weight: bold } /* Keyword */
            .codehilite .o { color: #666666 } /* Operator */
            .codehilite .cm { color: #408080; font-style: italic } /* Comment.Multiline */
            .codehilite .cp { color: #BC7A00 } /* Comment.Preproc */
            .codehilite .c1 { color: #408080; font-style: italic } /* Comment.Single */
            .codehilite .cs { color: #408080; font-style: italic } /* Comment.Special */
            .codehilite .gd { color: #A00000 } /* Generic.Deleted */
            .codehilite .ge { font-style: italic } /* Generic.Emph */
            .codehilite .gr { color: #FF0000 } /* Generic.Error */
            .codehilite .gh { color: #000080; font-weight: bold } /* Generic.Heading */
            .codehilite .gi { color: #00A000 } /* Generic.Inserted */
            .codehilite .go { color: #808080 } /* Generic.Output */
            .codehilite .gp { color: #000080; font-weight: bold } /* Generic.Prompt */
            .codehilite .gs { font-weight: bold } /* Generic.Strong */
            .codehilite .gu { color: #800080; font-weight: bold } /* Generic.Subheading */
            .codehilite .gt { color: #0040D0 } /* Generic.Traceback */
            .codehilite .kc { color: #008000; font-weight: bold } /* Keyword.Constant */
            .codehilite .kd { color: #008000; font-weight: bold } /* Keyword.Declaration */
            .codehilite .kn { color: #008000; font-weight: bold } /* Keyword.Namespace */
            .codehilite .kp { color: #008000 } /* Keyword.Pseudo */
            .codehilite .kr { color: #008000; font-weight: bold } /* Keyword.Reserved */
            .codehilite .kt { color: #B00040 } /* Keyword.Type */
            .codehilite .m { color: #666666 } /* Literal.Number */
            .codehilite .s { color: #BA2121 } /* Literal.String */
            .codehilite .na { color: #7D9029 } /* Name.Attribute */
            .codehilite .nb { color: #008000 } /* Name.Builtin */
            .codehilite .nc { color: #0000FF; font-weight: bold } /* Name.Class */
            .codehilite .no { color: #880000 } /* Name.Constant */
            .codehilite .nd { color: #AA22FF } /* Name.Decorator */
            .codehilite .ni { color: #999999; font-weight: bold } /* Name.Entity */
            .codehilite .ne { color: #D2413A; font-weight: bold } /* Name.Exception */
            .codehilite .nf { color: #0000FF } /* Name.Function */
            .codehilite .nl { color: #A0A000 } /* Name.Label */
            .codehilite .nn { color: #0000FF; font-weight: bold } /* Name.Namespace */
            .codehilite .nt { color: #008000; font-weight: bold } /* Name.Tag */
            .codehilite .nv { color: #19177C } /* Name.Variable */
            .codehilite .ow { color: #AA22FF; font-weight: bold } /* Operator.Word */
            .codehilite .w { color: #bbbbbb } /* Text.Whitespace */
            .codehilite .mf { color: #666666 } /* Literal.Number.Float */
            .codehilite .mh { color: #666666 } /* Literal.Number.Hex */
            .codehilite .mi { color: #666666 } /* Literal.Number.Integer */
            .codehilite .mo { color: #666666 } /* Literal.Number.Oct */
            .codehilite .sb { color: #BA2121 } /* Literal.String.Backtick */
            .codehilite .sc { color: #BA2121 } /* Literal.String.Char */
            .codehilite .sd { color: #BA2121; font-style: italic } /* Literal.String.Doc */
            .codehilite .s2 { color: #BA2121 } /* Literal.String.Double */
            .codehilite .se { color: #BB6622; font-weight: bold } /* Literal.String.Escape */
            .codehilite .sh { color: #BA2121 } /* Literal.String.Heredoc */
            .codehilite .si { color: #BB6688; font-weight: bold } /* Literal.String.Interpol */
            .codehilite .sx { color: #008000 } /* Literal.String.Other */
            .codehilite .sr { color: #BB6688 } /* Literal.String.Regex */
            .codehilite .s1 { color: #BA2121 } /* Literal.String.Single */
            .codehilite .ss { color: #19177C } /* Literal.String.Symbol */
            .codehilite .bp { color: #008000 } /* Name.Builtin.Pseudo */
            .codehilite .vc { color: #19177C } /* Name.Variable.Class */
            .codehilite .vg { color: #19177C } /* Name.Variable.Global */
            .codehilite .vi { color: #19177C } /* Name.Variable.Instance */
            .codehilite .il { color: #666666 } /* Literal.Number.Integer.Long */
        </style>
    </head>
    <body>
        <div id="content">
            <div id="inner-content">
                <%content%>
            </div>
        </div>
    </body>
</html>
"""

api_modules = LastUpdatedOrderedDict([
    (mast.cli, LastUpdatedOrderedDict()),
    (mast.config, LastUpdatedOrderedDict()),
    (mast.cron, LastUpdatedOrderedDict()),
    (mast._daemon, LastUpdatedOrderedDict()),
    (mast.datapower.datapower, LastUpdatedOrderedDict()),
    (mast.hashes, LastUpdatedOrderedDict()),
    (mast.home, LastUpdatedOrderedDict()),
    (mast.jobs, LastUpdatedOrderedDict()),
    (mast.logging, LastUpdatedOrderedDict()),
    (mast.plugin_utils, LastUpdatedOrderedDict()),
    (mast.pprint, LastUpdatedOrderedDict()),
    (mast.timestamp, LastUpdatedOrderedDict()),
    (mast.xor, LastUpdatedOrderedDict()),
    (mast.datapower.accounts, LastUpdatedOrderedDict()),
    (mast.datapower.backups, LastUpdatedOrderedDict()),
    (mast.datapower.crypto, LastUpdatedOrderedDict()),
    (mast.datapower.deployment, LastUpdatedOrderedDict()),
    (mast.datapower.developer, LastUpdatedOrderedDict()),
    (mast.datapower.network, LastUpdatedOrderedDict()),
    (mast.datapower.ssh, LastUpdatedOrderedDict()),
    (mast.datapower.status, LastUpdatedOrderedDict()),
    (mast.datapower.system, LastUpdatedOrderedDict())
])


def get_objects(modules):
    for module in dict(modules):
        functions = inspect.getmembers(module, inspect.isfunction)
        functions = [x for x in functions if not x[0].startswith("_")]
        functions = [x for x in functions if module.__name__ in x[1].__module__]
        modules[module]["functions"] = functions

        classes = inspect.getmembers(module, inspect.isclass)
        classes = [x for x in classes if not x[0].startswith("_")]
        classes = [x for x in classes if module.__name__ in x[1].__module__]
        if classes:
            modules[module]["classes"] = LastUpdatedOrderedDict()
        for cls in classes:
            methods = inspect.getmembers(
                cls[1],
                lambda x: inspect.ismethod(x) or isinstance(x, property))
            methods = [x for x in methods if isinstance(x[1    ], property) or module.__name__ in x[1].__module__]
            methods = [x for x in methods if hasattr(x[1], "__doc__") or x[0].startswith("_")]
            if methods:
                modules[module]["classes"][cls] = methods
    return modules


def escape(string):
    return string.replace("_", "\\_")

def generate_markdown(objects):
    md = ""
    for k, v in list(objects.items()):
        # Module names
        md += "\n# {}\n".format(escape(k.__name__))
        if k.__doc__:
            md += "\n\n{}\n".format(textwrap.dedent(k.__doc__))
        else:
            md += "\n\nNo module level documentation\n"
        for _k, _v in list(v.items()):
            # _k will either be functions or classes
            # md += "\n### {}\n".format(_k)
            if _k == "functions":
                for item in _v:
                    # Functions
                    md += "\n## {}\n".format(escape(item[0]))
                    model = mast.cli.params_model(item[1])
                    if model is not None:
                        # A command: its parameters are in its model
                        md += "\n\n{}\n\nParameters:\n\n".format(
                            textwrap.dedent(item[1].__doc__ or "").strip())
                        for spec in mast.cli.params.options(model):
                            md += "* `{}`: {}\n".format(spec.name, spec.help)
                    elif item[1].__doc__:
                        regex = re.compile(r"(\*\s`.*?--(.*?)`)")
                        doc = item[1].__doc__
                        for match in regex.findall(item[1].__doc__):
                            doc = doc.replace(match[0], "* `{}`".format(match[1].replace("-", "_")))
                        md += "\n\n{}\n".format(
                            textwrap.dedent(doc))
                    else:
                        md += "\n\nNo documentation available for this function!\n"
            elif _k == "classes":
                for __k, __v in list(_v.items()):
                    # class name
                    md += "\n## {}\n".format(escape(__k[0]))
                    if __k[1].__doc__:
                        # Class level docstrings
                        md += "\n\n{}\n".format(textwrap.dedent(__k[1].__doc__))
                    else:
                        md += "\n\nNo documentation available for this class!\n"
                    for method in __v:
                        # Method names and docstrings
                        md += "\n### {}\n".format(escape(method[0]))
                        if method[1].__doc__:
                            md += "\n\n{}\n".format(textwrap.dedent(method[1].__doc__))
                        else:
                            md += "\n\nNo documentation available for this method!\n"
    return md


def generate_cli_reference():
    """The help of every root of the `mast` command and of each of its
    commands, from the command registry (the same parsers as the command
    line)."""
    from mast import roots
    from mast.jobs import registry

    def section(title, help_text):
        text = help_text.replace("  -", "    -")
        return "{}\n\n{}\n\n".format(title, textwrap.dedent(text))

    clis = []
    for name, root in roots.ROOTS.items():
        if not root.program:
            clis.append(roots.load(root.target))
    for provider in registry.providers():
        if provider != roots.CORE:
            clis.append(registry.cli_for(provider))
    ret = ""
    for cli in clis:
        ret += section("# {}".format(cli.parser.prog), cli.parser.format_help())
        for command in cli.commands.values():
            ret += section("## {}".format(command.invocation),
                           "Id: `{}`, permission path: `{}`\n\n{}".format(
                               command.id, command.path,
                               command.parser.format_help()))
    return ret


class Main(Params):
    out_dir: str = Option("doc", short="o")


def main(params: Main):
    out_dir = params.out_dir
    os.makedirs(out_dir, exist_ok=True)
    global tpl
    template_dir = os.path.join(os.environ["MAST_HOME"],
                                "contrib",
                                "templates")

    for filename in os.listdir(template_dir):
        if filename.endswith(".md"):
            in_file = os.path.join(template_dir, filename)
            html_file = filename.replace(".md", ".html")
            html_file = os.path.join(out_dir, html_file)
            md_file = os.path.join(out_dir, filename)

            with open(in_file, "rb") as fin:
                md = fin.read().decode()

            md = md.format(os.environ["MAST_VERSION"])
            html = markdown.markdown(
                md,
                extensions=[
                    TocExtension(title="Table of Contents"),
                    CodeHiliteExtension(guess_lang=False),
                    "tables"])
            html = tpl.replace("<%content%>", html)

            print(("Output: {}".format(html_file)))
            with open(html_file, "w") as fout:
                fout.write(html)

            print(("Output: {}".format(md_file)))
            with open(md_file, "w") as fout:
                fout.write(md)

    api_objects = get_objects(api_modules)
    api_md = generate_markdown(api_objects)
    api_md = "[Back to index](./index.html)\n\n<h1>MAST for IBM DataPower Version {}</h1><h2>API Documentation v {}</h2>\n\n[TOC]\n\n{}".format(os.environ["MAST_VERSION"], os.environ["MAST_VERSION"], api_md)

    api_html = markdown.markdown(
        api_md,
        extensions=[
            TocExtension(title="Table of Contents"),
            CodeHiliteExtension(guess_lang=False),
            "tables"])
    api_html = tpl.replace("<%content%>", api_html)

    filename = os.path.join(out_dir, "APIReference.html")
    print(("Output: {}".format(filename)))
    with open(filename, "w") as fout:
        fout.write(api_html)

    filename = os.path.join(out_dir, "APIReference.md")
    print(("Output: {}".format(filename)))
    with open(filename, "w") as fout:
        fout.write(api_md)

    cli_md = generate_cli_reference()
    cli_md = "[Back to index](./index.html)\n\n<h1>MAST for IBM DataPower Version {}</h1><h2>CLI Reference v {}</h2>\n\n[TOC]\n\n{}".format(os.environ["MAST_VERSION"], os.environ["MAST_VERSION"], cli_md)
    cli_html = markdown.markdown(
        cli_md,
        extensions=[
            TocExtension(title="Table of Contents"),
            CodeHiliteExtension(guess_lang=False),
            "tables"])
    cli_html = tpl.replace("<%content%>", cli_html)

    filename = os.path.join(out_dir, "CLIReference.md")
    print(("Output: {}".format(filename)))
    with open(filename, "w") as fout:
        fout.write(cli_md)

    filename = os.path.join(out_dir, "CLIReference.html")
    print(("Output: {}".format(filename)))
    with open(filename, "w") as fout:
        fout.write(cli_html)


if __name__ == "__main__":
    cli = mast.cli.Cli(main=main)
    cli.run()
