"""Quiet AST parsing of third-party source.

Analyzed code may carry latent issues (for example invalid escape sequences
in string literals) that Python surfaces as ``SyntaxWarning`` when compiling
the module. Those warnings must not leak into the tool's report output.
"""

import ast
import warnings


def parse_quiet(source, filename="<unknown>"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(source, filename=filename)