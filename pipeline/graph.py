"""A small async graph runner: the control flow of the daily run.

A graph maps a node name to (function, edges). Each function takes the shared
state dict, does its work and returns an edge label; the runner follows the
label to the next node until it reaches END.

    graph = {
        "start":  (classify, {"a": "node_a", "b": "node_b"}),
        "node_a": (do_a,     {"done": END}),
        "node_b": (do_b,     {"done": END}),
    }
    state = await run_graph(graph, "start", {...})

Nodes may be sync or async. The graph and state are passed in, so a single node
or a whole graph can be tested in isolation.
"""
import inspect

END = "end"


class GraphError(RuntimeError):
    """Unknown node, undeclared edge label, or the step budget was exceeded."""


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


async def run_graph(graph: dict, start: str, state: dict, *, max_steps: int = 100, on_step=None) -> dict:
    """Run from `start`; state["_path"] records the visited nodes. `on_step(node,
    label)` is called after each node. `max_steps` guards against a loop that
    never reaches END, since nodes may cycle on purpose."""
    node = start
    path = state.setdefault("_path", [])
    steps = 0
    while node != END:
        if node not in graph:
            raise GraphError(f"no such node: {node!r}")
        if steps >= max_steps:
            raise GraphError(f"graph exceeded {max_steps} steps at {node!r}")
        steps += 1
        path.append(node)
        fn, edges = graph[node]
        label = await _resolve(fn(state))
        if on_step is not None:
            on_step(node, label)
        if label not in edges:
            raise GraphError(f"node {node!r} returned {label!r}; declared edges: {list(edges)}")
        node = edges[label]
    path.append(END)
    return state
