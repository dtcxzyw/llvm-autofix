import tree_sitter_bash
from tree_sitter import Language, Parser

BASH_LANGUAGE = Language(tree_sitter_bash.language())
bash_parser = Parser(BASH_LANGUAGE)


def get_commands(code: str):
  tree = bash_parser.parse(code.encode())
  cmds = []

  def ex(n, d):
    if n.type == "command_name":
      cmds.append(code[n.start_byte : n.end_byte].strip().split("\\n")[0])
    for child in n.children:
      ex(child, d + 1)

  ex(tree.root_node, 0)

  return cmds
