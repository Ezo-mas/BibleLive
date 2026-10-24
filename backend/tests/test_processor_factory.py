"""``make_processor`` must keep up with ``BibleLiveAudioProcessor.__init__``.

The factory lists every attribute the constructor sets. When ``__init__`` gains
or loses one, this test names it, instead of a pipeline test failing later with
an AttributeError far from the cause.
"""

import ast
import inspect
import textwrap

import pytest
from processor_factory import make_processor

from audio.pipeline import BibleLiveAudioProcessor

# audio.pipeline imports faster-whisper (optional audio extras).
pytestmark = pytest.mark.audio


def _init_attributes() -> set[str]:
    """Every ``self.<name>`` that ``__init__`` assigns (annotated or not)."""
    source = textwrap.dedent(inspect.getsource(BibleLiveAudioProcessor.__init__))
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                names.add(target.attr)
    return names


def test_factory_sets_exactly_what_init_sets():
    made = set(vars(make_processor()))
    expected = _init_attributes()
    assert expected, "found no attributes in __init__: the parsing above is broken"
    assert sorted(expected - made) == [], "set in __init__, missing from make_processor"
    assert sorted(made - expected) == [], "set by make_processor, no longer in __init__"


def test_unknown_override_is_refused():
    with pytest.raises(TypeError, match="sesion_log"):
        make_processor(sesion_log=None)
