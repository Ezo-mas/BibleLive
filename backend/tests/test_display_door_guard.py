"""Guard the two authorized doors to the audience display."""

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
SERVER = BACKEND / "core" / "server"
SCRIPTS = BACKEND.parent / "scripts"

ALLOWED = {
    "_send_display_reference": {
        ("state.py", "AppState.approve_pending"),
        ("state.py", "AppState.display_manual_passage"),
    },
    "display_manual_passage": {
        ("app.py", "_handle_display_reference"),
        ("state.py", "AppState.offer_follow_along"),
    },
    "approve_pending": {("state.py", "AppState.approve_and_notify")},
    "send_to_display": {
        ("state.py", "AppState._send_display_reference"),
        ("state.py", "AppState._send_display_clear"),
        ("state.py", "AppState.send_display_settings"),
    },
    "DisplayReference": {
        ("state.py", "AppState._send_display_reference"),
        ("state.py", "AppState.send_display_current_state"),
    },
}
REFERENCES = {"_send_display_reference", "display_manual_passage", "approve_pending"}


def _violations(source: str, path: Path, *, server: bool) -> list[str]:
    violations = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def check(self, node, name):
            enclosing = ".".join(self.scope) or "<module>"
            if not server or (path.name, enclosing) not in ALLOWED[name]:
                violations.append(f"{path}:{node.lineno}: {enclosing} references {name}")

        def visit_FunctionDef(self, node):
            # Decorators and defaults belong to the enclosing scope.
            for item in [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults]:
                if item is not None:
                    self.visit(item)
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                if arg.annotation is not None:
                    self.visit(arg.annotation)
            for arg in (node.args.vararg, node.args.kwarg):
                if arg is not None and arg.annotation is not None:
                    self.visit(arg.annotation)
            if node.returns is not None:
                self.visit(node.returns)
            for item in node.type_params:
                self.visit(item)
            self.scope.append(node.name)
            self.visit_body(node.body)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            for item in [*node.decorator_list, *node.bases, *node.keywords]:
                self.visit(item)
            self.scope.append(node.name)
            self.visit_body(node.body)
            self.scope.pop()

        def visit_Module(self, node):
            self.visit_body(node.body)

        def visit_body(self, body):
            for index, item in enumerate(body):
                if (
                    index == 0
                    and isinstance(item, ast.Expr)
                    and isinstance(item.value, ast.Constant)
                    and isinstance(item.value.value, str)
                ):
                    continue  # docstring
                self.visit(item)

        def visit_Name(self, node):
            if node.id in REFERENCES:
                self.check(node, node.id)

        def visit_Attribute(self, node):
            if node.attr in REFERENCES:
                self.check(node, node.attr)
            self.generic_visit(node)

        def visit_Constant(self, node):
            if isinstance(node.value, str) and node.value in REFERENCES:
                enclosing = ".".join(self.scope) or "<module>"
                violations.append(f"{path}:{node.lineno}: {enclosing} references {node.value}")

        def visit_Call(self, node):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else (node.func.attr if isinstance(node.func, ast.Attribute) else None)
            )
            if name in {"send_to_display", "DisplayReference"}:
                self.check(node, name)
            self.generic_visit(node)

    Visitor().visit(ast.parse(source, filename=str(path)))
    return violations


def test_display_door_callers():
    violations = []
    tests = BACKEND / "tests"
    paths = [*BACKEND.rglob("*.py"), *SCRIPTS.rglob("*.py")]
    for path in paths:
        if path.is_relative_to(tests):
            continue  # tests may drive the doors directly; the guard is about production callers
        server = path.is_relative_to(SERVER)
        violations.extend(_violations(path.read_text(encoding="utf-8"), path, server=server))
    assert not violations, "\n" + "\n".join(violations)


def test_guard_detects_illegal_caller():
    source = "def third_door():\n    self._send_display_reference(ref)\n"
    assert _violations(source, Path("state.py"), server=True) == [
        "state.py:2: third_door references _send_display_reference"
    ]


def test_guard_detects_alias():
    source = "def third_door():\n    send = state._send_display_reference\n    await send(ref)\n"
    assert _violations(source, Path("state.py"), server=True) == [
        "state.py:2: third_door references _send_display_reference"
    ]


def test_guard_detects_dynamic_lookup():
    source = 'def third_door():\n    getattr(state, "display_manual_passage")\n'
    assert _violations(source, Path("state.py"), server=True) == [
        "state.py:2: third_door references display_manual_passage"
    ]


def test_guard_detects_raw_display_doors():
    source = "def third_door():\n    connection_manager.send_to_display(DisplayReference())\n"
    assert _violations(source, Path("state.py"), server=True) == [
        "state.py:2: third_door references send_to_display",
        "state.py:2: third_door references DisplayReference",
    ]


def test_guard_detects_new_approval_caller():
    source = "def third_door():\n    state.approve_pending(ref)\n"
    assert _violations(source, Path("state.py"), server=True) == [
        "state.py:2: third_door references approve_pending"
    ]


def test_guard_distinguishes_enclosing_classes_and_functions():
    source = (
        "class Other:\n    def approve_pending(self):\n        self._send_display_reference(ref)\n"
    )
    assert _violations(source, Path("state.py"), server=True) == [
        "state.py:3: Other.approve_pending references _send_display_reference"
    ]
    source = "def outer():\n    def _handle_display_reference():\n        state.display_manual_passage(ref)\n"
    assert _violations(source, Path("app.py"), server=True) == [
        "app.py:3: outer._handle_display_reference references display_manual_passage"
    ]
