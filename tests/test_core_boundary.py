"""The Core boundary is held by a test, not a convention (Phase 1864).

``fuaran_ui._core`` is the private home of this host's Core twins (see its package
docstring). Two rules, each an AST walk over every module under ``src/fuaran_ui/_core``
— every ``import`` / ``from … import`` statement, including the ones nested inside a
function body, so a lazy import cannot slip past:

1. ``_core`` imports no ``fuaran_ui`` module outside itself, except the named wire
   foundation (:data:`WIRE_FOUNDATION`) it encodes and guards with.
2. Nothing inside the boundary imports from the host's domain packages. The domain set
   is DERIVED from the package directory — every top-level ``fuaran_ui`` module or
   package that is neither ``_core`` nor the wire foundation — so a domain package added
   later is covered without editing this file. The wire foundation's own module-scope
   imports are held to the same rule, so importing ``_core`` never loads a domain module.

Why the wire foundation is an allow-list rather than inside the boundary is recorded in
``CONTRIBUTING.md`` ("The Core boundary").

The checker proves it can fail on every run (:func:`test_the_checker_flags_a_planted_domain_import`),
not only on the day it was written.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = "fuaran_ui"
PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / PACKAGE
CORE = f"{PACKAGE}._core"
CORE_ROOT = PACKAGE_ROOT / "_core"

#: The host's wire foundation — canonical JSON, the structural value model, the decode
#: result codes, the shape guard and its limits. ``_core`` may import these and nothing
#: else from ``fuaran_ui``. Adding a name here widens the boundary: say why in
#: ``CONTRIBUTING.md`` in the same change.
WIRE_FOUNDATION = frozenset(
    {
        f"{PACKAGE}.canonical",
        f"{PACKAGE}.model",
        f"{PACKAGE}.result",
        f"{PACKAGE}.shapeguard",
        f"{PACKAGE}.limits",
    }
)


def _within(name: str, root: str) -> bool:
    return name == root or name.startswith(root + ".")


def _module_name(path: Path) -> tuple[str, bool]:
    """The dotted module name of a source file under ``src/``, and whether it is a package."""
    rel = path.relative_to(PACKAGE_ROOT.parent).with_suffix("")
    parts = list(rel.parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts), is_package


def imported_names(source: str, module: str, is_package: bool, *, module_scope_only: bool = False) -> list[str]:
    """Every absolute module name a source file imports, relative imports resolved.

    ``from X import y`` yields both ``X`` and ``X.y`` — ``y`` may be a submodule, and a
    reach into the package root itself (``from fuaran_ui import model``) must be seen as
    ``fuaran_ui`` as well as ``fuaran_ui.model``.
    """
    tree = ast.parse(source)
    package = module if is_package else module.rpartition(".")[0]
    nodes = tree.body if module_scope_only else list(ast.walk(tree))
    names: list[str] = []
    for node in nodes:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                if node.level > 1:
                    base = base[: len(base) - (node.level - 1)]
                target = ".".join([*base, node.module] if node.module else base)
            else:
                target = node.module or ""
            names.append(target)
            names.extend(f"{target}.{alias.name}" for alias in node.names if alias.name != "*")
    return names


def _outside_core(name: str) -> bool:
    """A ``fuaran_ui`` module that is neither inside ``_core`` nor in the wire foundation."""
    if not _within(name, PACKAGE):
        return False  # the standard library
    if _within(name, CORE):
        return False
    return not any(_within(name, f) for f in WIRE_FOUNDATION)


def _domain_packages() -> list[str]:
    """Every top-level ``fuaran_ui`` module or package that is not the boundary or its foundation."""
    found: list[str] = []
    for entry in sorted(PACKAGE_ROOT.iterdir()):
        if entry.name.startswith(("_", ".")):
            continue  # _core, __init__, __pycache__
        if entry.is_dir() and (entry / "__init__.py").exists():
            found.append(f"{PACKAGE}.{entry.name}")
        elif entry.suffix == ".py":
            found.append(f"{PACKAGE}.{entry.stem}")
    return [name for name in found if name not in WIRE_FOUNDATION]


def _domain_hits(names: list[str], domain: list[str]) -> list[str]:
    return [name for name in names if name == PACKAGE or any(_within(name, d) for d in domain)]


def _core_sources() -> list[Path]:
    return sorted(CORE_ROOT.rglob("*.py"))


def test_the_boundary_is_populated() -> None:
    # A walk over nothing passes vacuously; the boundary must actually hold the twins.
    modules = {_module_name(p)[0] for p in _core_sources()}
    for twin in (
        f"{CORE}.dataframe.model",
        f"{CORE}.dataframe.codec",
        f"{CORE}.dataframe.evaluate",
        f"{CORE}.function.space",
        f"{CORE}.function.registry",
    ):
        assert twin in modules, f"{twin} is missing from the Core boundary"


def test_the_published_paths_re_export_the_twins_unchanged() -> None:
    # Moving a twin behind the boundary must not move a published name: each published path
    # hands out the SAME object the boundary defines, never a copy that could drift from it.
    from fuaran_ui import function
    from fuaran_ui._core.function import registry, space
    from fuaran_ui.ui import capability

    assert capability.HoleSpace is space.HoleSpace
    assert registry.HoleSpace is space.HoleSpace
    for name in (
        "EXACT",
        "SUBSUMES",
        "ComposePath",
        "FunctionEntry",
        "FunctionRegistry",
        "NoPath",
        "SigEntry",
        "Signature",
        "SignatureQuery",
        "function_entry",
        "slot_hole",
        "value_hole",
        "HoleSpace",
    ):
        assert getattr(function, name) is getattr(registry, name), name


def test_the_wire_foundation_is_frozen() -> None:
    # Widening the allow-list is a deliberate act: it must change this pin as well as the
    # set above, and CONTRIBUTING.md must say why.
    assert sorted(f.rpartition(".")[2] for f in WIRE_FOUNDATION) == [
        "canonical",
        "limits",
        "model",
        "result",
        "shapeguard",
    ], "the Core boundary's wire-foundation allow-list changed; record why in CONTRIBUTING.md"
    for name in WIRE_FOUNDATION:
        assert (PACKAGE_ROOT / f"{name.rpartition('.')[2]}.py").is_file(), f"stale allow-list entry {name}"


def test_core_imports_no_fuaran_ui_module_outside_itself() -> None:
    violations: list[str] = []
    for path in _core_sources():
        module, is_package = _module_name(path)
        for name in imported_names(path.read_text(encoding="utf-8"), module, is_package):
            if _outside_core(name):
                violations.append(f"{module} imports {name}")
    assert not violations, (
        "fuaran_ui._core may import only itself and the wire foundation "
        f"({', '.join(sorted(WIRE_FOUNDATION))}):\n  " + "\n  ".join(violations)
    )


def test_nothing_inside_the_boundary_imports_a_domain_package() -> None:
    domain = _domain_packages()
    assert f"{PACKAGE}.ui" in domain and f"{PACKAGE}.schema" in domain, domain  # the probe measures something
    violations: list[str] = []
    for path in _core_sources():
        module, is_package = _module_name(path)
        for name in _domain_hits(imported_names(path.read_text(encoding="utf-8"), module, is_package), domain):
            violations.append(f"{module} imports {name}")
    # The foundation is loaded whenever _core is, so its module-scope imports are held to
    # the same rule. (``model.Node``'s notebook display imports the renderer inside the
    # method, on display only — CONTRIBUTING.md records that coupling.)
    for name in sorted(WIRE_FOUNDATION):
        path = PACKAGE_ROOT / f"{name.rpartition('.')[2]}.py"
        module, is_package = _module_name(path)
        source = path.read_text(encoding="utf-8")
        for hit in _domain_hits(imported_names(source, module, is_package, module_scope_only=True), domain):
            violations.append(f"{module} (wire foundation) imports {hit} at module scope")
    assert not violations, "the Core boundary imports a domain package:\n  " + "\n  ".join(violations)


def test_the_checker_flags_a_planted_domain_import() -> None:
    # The rule's own falsifier, run every time: each planted spelling must be caught, and
    # the legitimate spellings must not be.
    domain = _domain_packages()
    module, is_package = f"{CORE}.dataframe.codec", False
    planted = {
        "from ...ui import capability": f"{PACKAGE}.ui",
        "from fuaran_ui.schema.decode import decode": f"{PACKAGE}.schema.decode",
        "import fuaran_ui.renderer": f"{PACKAGE}.renderer",
        "def f():\n    from ...compute import evaluate": f"{PACKAGE}.compute",
        "from ... import validator": f"{PACKAGE}.validator",
    }
    for source, expected in planted.items():
        names = imported_names(source, module, is_package)
        assert any(_within(n, expected) for n in _domain_hits(names, domain)), source
        assert any(_outside_core(n) for n in names), source
    for clean in ("from ...canonical import encode_value", "from .model import NULL", "import math"):
        names = imported_names(clean, module, is_package)
        assert not _domain_hits(names, domain) and not any(_outside_core(n) for n in names), clean
