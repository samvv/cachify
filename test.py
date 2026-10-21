
from collections.abc import Sequence
from dataclasses import dataclass

import sys
from cachify import cache

@dataclass
class LitExpr:
    value: str

type Expr = LitExpr

@dataclass
class Rule:
    name: str
    expr: Expr

@dataclass
class Grammar:
    rules: Sequence[Rule]

@cache
def get_mapping(grammar: Grammar):
    out = {}
    for rule in grammar.rules:
      out[rule.name] = rule
    return out

def lookup_rule(name: str, grammar: Grammar):
    return get_mapping(grammar).get(name)

g1 = Grammar([ Rule('foo', LitExpr('123456789')) ])
g2 = Grammar([ Rule('bar', LitExpr('abcdefghi')) ])

lookup_rule('foo', g1)
lookup_rule('bar', g1)
lookup_rule('foo', g2)
lookup_rule('bar', g2)
