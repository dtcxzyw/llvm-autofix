from autofix.lms.tool import FuncToolBase, FuncToolCallException, FuncToolSpec


class TodoTool(FuncToolBase):
  def __init__(self):
    self.todos = []

  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "todo",
      "Manage a todo list to track sub-tasks and goals during the analysis and repair process.",
      [
        FuncToolSpec.Param(
          "action",
          "string",
          True,
          "The action to perform: 'add', 'list', 'complete', 'update', or 'delete'.",
        ),
        FuncToolSpec.Param(
          "task",
          "string",
          False,
          "The description of the task (required for 'add' and 'update' if changing description).",
        ),
        FuncToolSpec.Param(
          "index",
          "integer",
          False,
          "The 1-based index of the task (required for 'complete', 'update', and 'delete').",
        ),
        FuncToolSpec.Param(
          "notes",
          "string",
          False,
          "Additional notes or sub-tasks for the todo item (optional for 'add' and 'update').",
        ),
      ],
    )

  def _call(
    self,
    *,
    action: str,
    task: str = None,
    index: int = None,
    notes: str = None,
    **kwargs,
  ) -> str:
    if action == "add":
      if not task:
        raise FuncToolCallException("Task description is required for 'add' action.")
      self.todos.append({"task": task, "completed": False, "notes": notes or ""})
      return f"Added todo: {task}"

    elif action == "list":
      if not self.todos:
        return "Todo list is empty."
      res = []
      for i, todo in enumerate(self.todos):
        status = "[x]" if todo["completed"] else "[ ]"
        item = f"{i + 1}. {status} {todo['task']}"
        if todo["notes"]:
          item += f" (Notes: {todo['notes']})"
        res.append(item)
      return "\n".join(res)

    elif action == "complete":
      if index is None:
        raise FuncToolCallException("Index is required for 'complete' action.")
      if index < 1 or index > len(self.todos):
        raise FuncToolCallException(
          f"Invalid index {index}. Total tasks: {len(self.todos)}"
        )
      self.todos[index - 1]["completed"] = True
      return f"Completed todo: {self.todos[index - 1]['task']}"

    elif action == "update":
      if index is None:
        raise FuncToolCallException("Index is required for 'update' action.")
      if index < 1 or index > len(self.todos):
        raise FuncToolCallException(
          f"Invalid index {index}. Total tasks: {len(self.todos)}"
        )
      if task:
        self.todos[index - 1]["task"] = task
      if notes is not None:
        self.todos[index - 1]["notes"] = notes
      return f"Updated todo: {self.todos[index - 1]['task']}"

    elif action == "delete":
      if index is None:
        raise FuncToolCallException("Index is required for 'delete' action.")
      if index < 1 or index > len(self.todos):
        raise FuncToolCallException(
          f"Invalid index {index}. Total tasks: {len(self.todos)}"
        )
      removed = self.todos.pop(index - 1)
      return f"Deleted todo: {removed['task']}"

    else:
      raise FuncToolCallException(f"Invalid action: {action}")
