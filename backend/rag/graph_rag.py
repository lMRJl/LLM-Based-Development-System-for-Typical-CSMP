import re
import json
import networkx as nx
from collections import defaultdict
from backend.core.llm import get_llm

ENTITY_EXTRACTION_PROMPT = """你是一个实体关系抽取助手。从以下文本中提取关键实体及其关系。

文本:
{text}

请输出 JSON 格式:
{{
  "entities": [
    {{"name": "实体名", "type": "person|technology|standard|concept|organization|other", "description": "简短说明"}}
  ],
  "relations": [
    {{"source": "实体A", "target": "实体B", "relation": "关系描述"}}
  ]
}}

只输出 JSON，不要其他内容。"""


class GraphRAG:
    """
    Graph RAG: 基于知识图谱的关系增强检索。
    通过实体关系抽取构建知识图谱，支持邻居查询和子图遍历。
    图谱自动持久化到 ChromaDB 目录旁的 .graph.gml 文件。
    """

    def __init__(self):
        from backend.config import get_settings
        self.llm = get_llm()
        self.graph = nx.DiGraph()
        self._entity_chunks: dict[str, list[str]] = defaultdict(list)
        settings = get_settings()
        import os
        self._persist_path = os.path.join(settings.chroma_persist_dir, ".graph.gml")
        self._loaded = False

    def _ensure_loaded(self):
        """Lazy-load graph from disk on first access."""
        if self._loaded:
            return
        self._loaded = True
        try:
            if __import__("os").path.exists(self._persist_path):
                self.graph = nx.read_gml(self._persist_path)
                # Rebuild _entity_chunks from graph node attributes
                for node, data in self.graph.nodes(data=True):
                    doc_ids_str = data.get("doc_ids", "")
                    if doc_ids_str:
                        self._entity_chunks[node] = doc_ids_str.split("|")
                print(f"[GraphRAG] Loaded persisted graph: {self.graph.number_of_nodes()} nodes, "
                      f"{self.graph.number_of_edges()} edges")
        except Exception as e:
            print(f"[GraphRAG] Failed to load persisted graph (starting fresh): {e}")
            self.graph = nx.DiGraph()
            self._entity_chunks.clear()

    def _save(self):
        """Persist graph to disk."""
        try:
            # Encode doc_ids into node attributes (GML doesn't support list attributes)
            for node, doc_ids in self._entity_chunks.items():
                if node in self.graph:
                    self.graph.nodes[node]["doc_ids"] = "|".join(doc_ids)
            nx.write_gml(self.graph, self._persist_path)
        except Exception as e:
            print(f"[GraphRAG] Failed to save graph: {e}")

    def extract_entities(self, text: str) -> dict:
        """Extract entities and relations from text."""
        import json as _json
        prompt = ENTITY_EXTRACTION_PROMPT.format(text=text[:3000])
        response = self.llm.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1024,
        )
        try:
            response = response.strip()
            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join(lines[1:-1]) if response.endswith("```") else "\n".join(lines[1:])
            return _json.loads(response)
        except (_json.JSONDecodeError, IndexError):
            return {"entities": [], "relations": []}

    def add_document(self, doc_id: str, text: str):
        """Extract entities from a document and add to the graph. Auto-persists."""
        self._ensure_loaded()
        extracted = self.extract_entities(text)

        for entity in extracted.get("entities", []):
            name = entity["name"]
            etype = entity.get("type", "other")
            desc = entity.get("description", "")
            if name not in self.graph:
                self.graph.add_node(name, type=etype, description=desc)
            self._entity_chunks[name].append(doc_id)

        for relation in extracted.get("relations", []):
            src = relation["source"]
            tgt = relation["target"]
            rel = relation.get("relation", "related_to")
            if src in self.graph and tgt in self.graph:
                self.graph.add_edge(src, tgt, relation=rel)

        self._save()

    def query_entities(self, query: str, top_k: int = 5) -> list[str]:
        """Find matching entities by keyword search."""
        self._ensure_loaded()
        matches = []
        query_lower = query.lower()
        for node in self.graph.nodes:
            if query_lower in node.lower():
                matches.append(node)
        return matches[:top_k]

    def get_neighbors(self, entity: str, depth: int = 1) -> list[str]:
        """Get neighbor entities within k hops."""
        self._ensure_loaded()
        if entity not in self.graph:
            return []
        neighbors = set()
        current = {entity}
        for _ in range(depth):
            next_level = set()
            for node in current:
                for neighbor in nx.neighbors(self.graph, node):
                    if neighbor not in neighbors:
                        neighbors.add(neighbor)
                        next_level.add(neighbor)
            current = next_level
        return list(neighbors)

    def get_subgraph_context(self, query: str, depth: int = 1) -> list[dict]:
        """
        Query-based graph retrieval: find matching entities, expand to neighbors,
        return context with entity descriptions and relations.
        """
        self._ensure_loaded()
        entities = self.query_entities(query, top_k=5)
        if not entities:
            return []

        all_entities = set(entities)
        for entity in entities:
            neighbors = self.get_neighbors(entity, depth=depth)
            all_entities.update(neighbors)

        context = []
        for entity in list(all_entities)[:20]:
            node_data = self.graph.nodes.get(entity, {})
            edges = []
            for _, tgt, data in self.graph.out_edges(entity, data=True):
                edges.append({"target": tgt, "relation": data.get("relation", "")})
            context.append({
                "entity": entity,
                "type": node_data.get("type", "unknown"),
                "description": node_data.get("description", ""),
                "relations": edges,
                "doc_ids": self._entity_chunks.get(entity, []),
            })

        return context

    def stats(self) -> dict:
        self._ensure_loaded()
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
        }

    def clear(self):
        self.graph.clear()
        self._entity_chunks.clear()
        import os
        try:
            if os.path.exists(self._persist_path):
                os.remove(self._persist_path)
        except Exception as e:
            print(f"[GraphRAG] Failed to remove persisted graph: {e}")


_graph_rag: GraphRAG | None = None


def get_graph_rag() -> GraphRAG:
    global _graph_rag
    if _graph_rag is None:
        _graph_rag = GraphRAG()
    return _graph_rag
