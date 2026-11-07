from .base import Context, Parser, Pending
from .rules import RulesParser


def make_parser(kind: str, world, **kw) -> Parser:
    if kind == "rules":
        return RulesParser(world)
    if kind == "anthropic":
        from .llm import AnthropicParser
        return AnthropicParser(world, **kw)
    if kind == "ollama":
        from .llm import OllamaParser
        return OllamaParser(world, **kw)
    raise ValueError(f"parser desconocido: {kind}")


__all__ = ["Context", "Parser", "Pending", "RulesParser", "make_parser"]
