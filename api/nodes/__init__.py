"""
Node registry for workflow execution.

Provides registration and discovery of node types.
Nodes are registered by class name and can be looked up
by the workflow runner.
"""
from typing import Type

from .base import NodeBase


# Global node registry
_NODE_REGISTRY: dict[str, Type[NodeBase]] = {}


def register_node(node_class: Type[NodeBase]) -> Type[NodeBase]:
    """
    Register a node class in the registry.

    Can be used as a decorator:
        @register_node
        class MyNode(NodeBase[MyInput, MyOutput]):
            ...

    Or called directly:
        register_node(MyNode)

    Args:
        node_class: Node class to register

    Returns:
        The node class (for decorator usage)
    """
    node_type = node_class.get_node_type()

    # Register by class name
    _NODE_REGISTRY[node_type] = node_class

    # Also register with 'nodes/' prefix for CWL compatibility
    _NODE_REGISTRY[f"nodes/{node_type}"] = node_class

    return node_class


def get_node_registry() -> dict[str, Type[NodeBase]]:
    """
    Get the full node registry.

    Returns:
        Dict mapping node type names to node classes
    """
    return _NODE_REGISTRY.copy()


def get_node_class(node_type: str) -> Type[NodeBase] | None:
    """
    Get a node class by type name.

    Args:
        node_type: Node type name (e.g., "LLMNode" or "nodes/LLMNode")

    Returns:
        Node class or None if not found
    """
    return _NODE_REGISTRY.get(node_type)


def list_node_types() -> list[str]:
    """
    List all registered node types.

    Returns:
        List of node type names (without 'nodes/' prefix)
    """
    return [k for k in _NODE_REGISTRY.keys() if not k.startswith("nodes/")]


def get_node_info_all() -> list[dict]:
    """
    Get info about all registered nodes.

    Returns:
        List of node info dicts
    """
    return [
        cls.get_node_info()
        for name, cls in _NODE_REGISTRY.items()
        if not name.startswith("nodes/")
    ]


# Import and register built-in nodes (must come after registry definition)
from .llm_node import LLMNode  # noqa: E402
from .pipeline_config_node import PipelineConfigNode  # noqa: E402
from .ranker_node import RankerNode  # noqa: E402

register_node(LLMNode)
register_node(PipelineConfigNode)
register_node(RankerNode)


__all__ = [
    "NodeBase",
    "register_node",
    "get_node_registry",
    "get_node_class",
    "list_node_types",
    "get_node_info_all",
    "LLMNode",
    "PipelineConfigNode",
    "RankerNode",
]
