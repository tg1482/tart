"""The skill doc is only useful if it's true, and a doc nobody checks
goes stale silently. These tests fail when the CLI, the manifest schema,
the app.run signature, or the widget set grows something the doc doesn't
mention — so drift is a red test, not a surprise for whoever reads it
next.
"""

import ast
import builtins
import dataclasses
import inspect
import pathlib
import re

from tartifacts import app, cli, manifest, widgets

SKILL = cli.SKILL_PATH.read_text()


def cli_subcommands() -> set[str]:
    """Every literal the dispatcher in cli.main() compares `cmd` against."""
    source = inspect.getsource(cli.main)
    return set(re.findall(r'cmd (?:==|in \()\s*"([^"]+)"', source)) | set(
        re.findall(r'cmd in \([^)]*?"([^"]+)"\)', source)
    )


def test_every_cli_subcommand_is_documented():
    for cmd in cli_subcommands():
        assert cmd in SKILL, f"`artifact {cmd}` exists but isn't in skill.md"


def test_every_manifest_field_is_documented():
    for field in dataclasses.fields(manifest.Manifest):
        if field.name == "path":  # internal, not a manifest key
            continue
        assert f"`{field.name}`" in SKILL, f"manifest field '{field.name}' isn't in skill.md"


def test_every_app_run_parameter_is_documented():
    for name in inspect.signature(app.run).parameters:
        assert name in SKILL, f"app.run(..., {name}=) isn't in skill.md"


def test_every_public_widget_is_documented():
    public = [
        name for name, obj in vars(widgets).items()
        if not name.startswith("_")
        and (inspect.isfunction(obj) or inspect.isclass(obj))
        and getattr(obj, "__module__", "") == widgets.__name__
    ]
    for name in public:
        assert name in SKILL, f"widgets.{name} isn't in skill.md"


def test_every_source_type_is_documented():
    assert "FileSource" in SKILL


def test_every_environment_variable_is_documented():
    """Env vars are the one part of the interface with no --help to read,
    so an undocumented one is invisible until someone reads the source."""
    package = pathlib.Path(cli.__file__).parent
    found = set()
    for module in package.glob("*.py"):
        source = module.read_text()
        found |= set(re.findall(r'environ(?:\.get)?\(?\[?["\']([A-Z_]+)["\']', source))
    for name in found:
        assert name in SKILL, f"tart reads ${name} but skill.md doesn't mention it"


def test_the_doc_never_names_an_environment_variable_that_does_not_exist(tmp_path):
    """The reverse direction. `TART_POINTER` sat in the doc for a release
    after the rename because the test above only asks whether every var the
    code reads is documented, never whether every var documented exists."""
    from tartifacts import paths

    package = pathlib.Path(cli.__file__).parent
    real = {paths.ENV_VAR}       # read via a constant, not a literal
    for module in package.glob("*.py"):
        real |= set(re.findall(r'environ(?:\.get)?\(?\[?["\']([A-Z_]+)["\']', module.read_text()))
    for named in set(re.findall(r"`(TART_[A-Z_]+)`", SKILL)):
        assert named in real, f"skill.md names ${named}, which the code never reads"


def test_skill_is_reachable_from_the_cli():
    assert cli.SKILL_PATH.exists()
    assert SKILL.startswith("# tart")
    assert "--skill" in SKILL  # documents its own entry point


# --- the other direction ---------------------------------------------------
# The tests above check code -> doc: everything that exists is mentioned.
# These check doc -> code: everything mentioned still exists. That gap let
# the doc keep recommending `mux.register` and a $HOME-relative encoding
# for months after both were deleted.

import importlib  # noqa: E402


def referenced_symbols() -> set[tuple[str, str]]:
    """(module, attribute) pairs the doc tells an agent to call, like
    `tart.data_path()` or `widgets.Cursor`."""
    found = set()
    for module, attr in re.findall(r"\b(tartifacts|app|widgets|registry|roots)\.([a-zA-Z_]\w*)", SKILL):
        found.add((module, attr))
    return found


def test_every_api_the_doc_mentions_actually_exists():
    assert referenced_symbols(), "extractor found nothing — this test would verify nothing"
    aliases = {"app": "tartifacts.app", "widgets": "tartifacts.widgets",
               "registry": "tartifacts.registry",
               "roots": "tartifacts.roots",
               "tartifacts": "tartifacts"}
    for module, attr in sorted(referenced_symbols()):
        mod = importlib.import_module(aliases[module])
        if hasattr(mod, attr):
            continue
        try:
            importlib.import_module(f"{aliases[module]}.{attr}")
        except ImportError:
            raise AssertionError(f"skill.md references {module}.{attr}, which doesn't exist")


def test_doc_does_not_reference_deleted_modules():
    for gone in ("tartifacts.herdr", "tartifacts.tmux", "tartifacts.mux", "mux.py"):
        assert gone not in SKILL, f"skill.md still references the deleted {gone}"


def test_doc_never_names_a_module_that_cannot_be_imported():
    """The rename left `tart.widgets` in a heading for a release. The
    doc->code test above missed it because it only looks up names under the
    NEW package, so the stale one matched nothing and was never checked."""
    for module in set(re.findall(r"`([a-z_]+(?:\.[a-z_]+)+)`", SKILL)):
        root = module.split(".")[0]
        if root in ("tart", "tartifacts"):     # our own package, either spelling
            try:
                importlib.import_module(module)
            except ImportError:
                raise AssertionError(f"skill.md names `{module}`, which cannot be imported")


def undefined_names(source: str) -> set[str]:
    """Names a snippet reads without ever binding. Uses the AST rather than
    a regex, because `widgets.Cursor()` is attribute access, not a bare
    name — a regex flags it and a test that cries wolf gets ignored."""
    tree = ast.parse(source)
    bound = set(dir(builtins))
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            spec = node.args
            bound |= {a.arg for a in [*spec.posonlyargs, *spec.args, *spec.kwonlyargs]}
            bound |= {a.arg for a in (spec.vararg, spec.kwarg) if a}
            bound.add(getattr(node, "name", ""))
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.Name):
            (bound if isinstance(node.ctx, ast.Store) else used).add(node.id)
    return used - bound


def test_every_standalone_python_example_defines_what_it_uses():
    """A verbatim copy of the canonical example raised NameError on a
    missing `Panel` import — the block everyone copies first.

    Only blocks that import something are held to this: the rest are
    deliberate fragments (an `on_key` body, a two-line call), and demanding
    they stand alone would be a test that cries wolf."""
    placeholders = {"my_rich_renderable", "send"}
    blocks = [b for b in re.findall(r"```python\n(.*?)```", SKILL, re.S) if "import " in b]
    assert len(blocks) >= 2, "no standalone examples found — this test would verify nothing"
    for block in blocks:
        missing = undefined_names(block) - placeholders
        assert not missing, f"skill.md example uses {sorted(missing)} without defining or importing it"


def test_manifest_example_parses_and_uses_real_fields():
    import json

    block = re.search(r"```json\n(\{.*?\})\n```", SKILL, re.S)
    assert block, "the manifest example should be a fenced json block"
    example = json.loads(block.group(1))
    known = {f.name for f in dataclasses.fields(manifest.Manifest)} - {"path"}
    assert set(example) <= known, f"example uses fields that don't exist: {set(example) - known}"
    assert {"title", "run"} <= set(example)   # the required ones are shown


def test_documented_cli_commands_are_all_dispatchable():
    documented = set(re.findall(r"`tart (list|run|render|fetch|roots)\b", SKILL))
    assert documented, "extractor found no documented commands"
    assert documented <= cli_subcommands(), f"doc shows commands the CLI lacks: {documented - cli_subcommands()}"
