# -*- coding: utf-8 -*-
"""多语言覆盖检查：界面上的中文静态文案必须有英文翻译。"""
import ast
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import i18n  # noqa: E402
from rpa.engine import STEP_TYPES  # noqa: E402

pytestmark = pytest.mark.engine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CJK = re.compile(r"[\u4e00-\u9fff]")
SOURCES = ("rpa/gui.py", "rpa/dialogs.py", "rpa/recorder.py")


def _ui_literals(path: str) -> set[str]:
    """收集可能直接显示给用户的中文字符串常量。"""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    found: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node):
            func = node.func
            name = ""
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            targets = []
            if name in ("set_text_source", "tr", "_"):
                targets.extend(node.args[:1])
            targets.extend(kw.value for kw in node.keywords
                           if kw.arg in ("text", "title"))
            for target in targets:
                if (isinstance(target, ast.Constant)
                        and isinstance(target.value, str)
                        and CJK.search(target.value)):
                    found.add(target.value)
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


def test_ui_strings_have_english():
    saved = i18n.get_language()
    i18n.set_language("en")
    try:
        missing: list[str] = []
        for relative in SOURCES:
            for text in _ui_literals(os.path.join(ROOT, relative)):
                if i18n.tr(text) == text:
                    missing.append(f"{relative}: {text!r}")
        for key, (label, hint, _repeat, repeat_hint) in STEP_TYPES.items():
            for text in (label, hint, repeat_hint):
                if text and i18n.tr(text) == text:
                    missing.append(f"STEP_TYPES[{key}]: {text!r}")
        assert not missing, "缺少英文翻译：\n" + "\n".join(missing)
    finally:
        i18n.set_language(saved)
