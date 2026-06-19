"""Fitness Expert Agent — A2A server :9005.

The one specialist with **no MCP server**. Instead of live APIs it answers from a
vector database of public-domain fitness literature, retrieved via RAG
(:mod:`core.fitness_rag`, wired up in :mod:`agents._rag_executor`). Run standalone:

    python -m agents.fitness_agent

The vector index must be built first (the launch scripts do this automatically):

    python -m scripts.build_fitness_index
"""

from agents._rag_executor import run_fitness

if __name__ == "__main__":
    run_fitness()
