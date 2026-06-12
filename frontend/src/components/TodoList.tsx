import { TODO_MARKER, TodoEntry } from "@/lib/todos";

export function TodoListBody({ todos }: { todos: TodoEntry[] | null }) {
  if (!todos || todos.length === 0) {
    return <p className="todo-empty">No todo items.</p>;
  }
  return (
    <ul className="todo-list">
      {todos.map((todo, index) => (
        <li key={`${todo.text}-${index}`} className={`todo-item ${todo.status}`}>
          <span className="todo-marker" aria-hidden>
            {TODO_MARKER[todo.status]}
          </span>
          <span className="todo-body">
            <span className="todo-text">{todo.text}</span>
            {todo.detail ? <span className="todo-detail">{todo.detail}</span> : null}
          </span>
        </li>
      ))}
    </ul>
  );
}
